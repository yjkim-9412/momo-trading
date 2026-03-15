// ── coin-account.js — Account display: balance, holdings, pending orders ──
//
// Security note: all dynamic content is escaped via escapeHtml()
// before DOM insertion. This mirrors the original coin-app.js exactly.

import { escapeHtml, formatTime, fetchJSON } from '../shared/admin-core.js';
import { API } from './coin-state.js';
// state accessed indirectly via API
import {
  formatCoinQty, formatPrice, formatPnl, truncateText,
  formatDetailedKRW, formatPreciseKRWTitle,
  setInlineStatus, setTextContent, updateMetricValue,
} from './coin-utils.js';

// -- Load account overview --

export async function loadBalance() {
  try {
    var json = await fetchJSON(API + '/account/overview');
    var data = json.data;
    if (!data) return;
    renderBalance(data.balance || data);
    renderHoldings(data.holdings || []);
    renderPendingOrders(data.pending_orders || []);
  } catch (err) {
    console.error('[coin] balance load error:', err);
    setInlineStatus('sys-exchange', '오류', '#f87171');
  }
}

// -- Render balance metrics --

export function renderBalance(b) {
  if (!b) {
    setInlineStatus('sys-exchange', '데이터 없음', '#9ca3af');
    return;
  }
  var totalAsset = Number(b.total_asset ?? 0);
  var cash = Number(b.cash ?? 0);
  var coinValue = Number(b.coin_value ?? b.stock_value ?? 0);
  var lockedKrw = Number(b.locked_krw ?? 0);
  var totalPnl = Number(b.total_pnl ?? 0);
  var totalPnlRate = Number(b.total_pnl_rate ?? 0);

  updateMetricValue('total-asset', 'total-asset-delta', totalAsset, formatDetailedKRW(totalAsset));
  updateMetricValue('cash-balance', 'cash-balance-delta', cash, formatDetailedKRW(cash));
  updateMetricValue('coin-eval', 'coin-eval-delta', coinValue, formatDetailedKRW(coinValue));
  updateMetricValue('locked-krw', 'locked-krw-delta', lockedKrw, formatDetailedKRW(lockedKrw));
  _setValueTitle('total-asset', totalAsset);
  _setValueTitle('cash-balance', cash);
  _setValueTitle('coin-eval', coinValue);
  _setValueTitle('locked-krw', lockedKrw);

  var pnlEl = document.getElementById('total-pnl');
  if (pnlEl) {
    pnlEl.innerHTML = formatPnl(totalPnl, totalPnlRate);
  }

  var exchangeMessage = b.is_valid === false
    ? '오류' + (b.status_message ? ' · ' + truncateText(b.status_message, 48) : '')
    : '정상' + (b.status_message ? ' · ' + truncateText(b.status_message, 48) : '');
  setInlineStatus('sys-exchange', exchangeMessage, b.is_valid === false ? '#f87171' : '#34d399');
}

// -- Render holdings --

export function renderHoldings(holdings) {
  var container = document.getElementById('holdings-list');
  if (!container) return;

  setTextContent('holdings-count', ((holdings && holdings.length) || 0) + '종목');

  if (!holdings || !holdings.length) {
    container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">보유 코인 없음</div>';
    return;
  }

  container.innerHTML = holdings.map(function (h) {
    var pnlVal = Number(h.pnl ?? 0);
    var pnlRate = Number(h.pnl_rate ?? 0);
    var pnlColor = pnlVal > 0 ? '#34d399' : pnlVal < 0 ? '#f87171' : '#9ca3af';
    var pnlSign = pnlVal > 0 ? '+' : pnlVal < 0 ? '-' : '';
    var qty = formatCoinQty(h.quantity);
    var avgP = formatPrice(h.avg_buy_price);
    var curP = formatPrice(h.current_price);
    var evalValue = Number(h.current_price || 0) * Number(h.quantity || 0);
    var evalAmt = formatDetailedKRW(evalValue);
    var evalTitle = escapeHtml(formatPreciseKRWTitle(evalValue));
    var pnlText = pnlSign + formatDetailedKRW(Math.abs(pnlVal));
    var pnlTitle = escapeHtml(formatPreciseKRWTitle(pnlVal));
    var name = h.name || h.symbol || '';
    var symbol = h.symbol || '';

    return `
    <div class="holding-card" style="background:rgba(30,30,46,0.6); border-radius:8px; padding:10px 12px; margin-bottom:6px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div style="min-width:0;">
          <div style="color:#e2e8f0; font-weight:500; font-size:13px;">${escapeHtml(name)}</div>
          <div style="color:#6b7280; font-size:11px;">${escapeHtml(symbol)} · ${qty}</div>
        </div>
        <span style="color:${pnlColor}; font-weight:500; font-size:13px;">${pnlSign}${Math.abs(pnlRate).toFixed(2)}%</span>
      </div>
      <div style="display:flex; justify-content:space-between; color:#9ca3af; font-size:11px; margin-top:4px;">
        <span>평단 ${avgP}</span>
        <span>현재 ${curP}</span>
      </div>
      <div style="display:flex; justify-content:space-between; color:#6b7280; font-size:11px; margin-top:2px;">
        <span title="${evalTitle}">평가 ${evalAmt}</span>
        <span style="color:${pnlColor};" title="${pnlTitle}">${pnlText}</span>
      </div>
    </div>
  `;
  }).join('');
}

// -- Pending order time --

export function formatPendingOrderTime(order) {
  if (order && order.updated_at) return formatTime(order.updated_at);
  if (order && order.submitted_at) return formatTime(order.submitted_at);
  var orderTime = String((order && order.order_time) || '');
  if (orderTime.length === 6) {
    return orderTime.slice(0, 2) + ':' + orderTime.slice(2, 4) + ':' + orderTime.slice(4, 6);
  }
  return '';
}

// -- Order status meta --

export function getPendingOrderStatusMeta(status) {
  var normalized = String(status || '').toUpperCase();
  switch (normalized) {
    case 'PARTIAL': return { label: '부분체결', className: 'status-partial' };
    case 'FILLED': return { label: '체결완료', className: 'status-filled' };
    case 'CANCELED': return { label: '취소', className: 'status-canceled' };
    case 'OPEN': return { label: '대기', className: '' };
    case 'SUBMITTED': return { label: '접수', className: '' };
    default: return { label: normalized || '대기', className: '' };
  }
}

// -- Render pending orders --

export function renderPendingOrders(orders) {
  var container = document.getElementById('pending-list');
  var countEl = document.getElementById('pending-count');
  if (!container || !countEl) return;

  if (!orders || !orders.length) {
    countEl.textContent = '0건';
    countEl.title = '';
    container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">미체결 주문 없음</div>';
    return;
  }

  var totalAmount = orders.reduce(function (sum, order) {
    return sum + (Number(order.order_price || 0) * Number(order.remaining_qty || 0));
  }, 0);
  countEl.textContent = orders.length + '건 (' + formatDetailedKRW(totalAmount) + ')';
  countEl.title = formatPreciseKRWTitle(totalAmount);

  container.innerHTML = orders.map(function (order) {
    var isBuy = order.side === '매수';
    var sideClass = isBuy ? 'buy' : 'sell';
    var statusMeta = getPendingOrderStatusMeta(order.status);
    var updatedAt = formatPendingOrderTime(order);
    var remainingQty = formatCoinQty(order.remaining_qty);
    var orderQty = formatCoinQty(order.order_qty);
    var filledQty = formatCoinQty(order.filled_qty);
    var remainingValue = Number(order.order_price || 0) * Number(order.remaining_qty || 0);
    var statusDetail = order.status_detail
      ? `<div class="text-[10px] text-gray-500 truncate" title="${escapeHtml(order.status_detail)}">${escapeHtml(order.status_detail)}</div>`
      : '';
    return `
    <div class="coin-pending-card">
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0">
          <div class="text-gray-100 font-medium truncate" title="${escapeHtml(order.symbol)}">${escapeHtml(order.name || order.symbol)}</div>
          <div class="text-[11px] text-gray-500">${escapeHtml(order.symbol || '')}</div>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <span class="coin-side-chip ${sideClass}">${escapeHtml(order.side || '')}</span>
          <span class="coin-status-chip ${statusMeta.className}">${escapeHtml(statusMeta.label)}</span>
        </div>
      </div>
      <div class="flex justify-between items-center gap-2 mt-1.5 text-[11px] text-gray-300">
        <span>주문가</span>
        <span>${formatPrice(order.order_price)} KRW</span>
      </div>
      <div class="flex justify-between items-center gap-2 text-[11px] text-gray-400 mt-1">
        <span>미체결 ${remainingQty} / 주문 ${orderQty}</span>
        <span title="${escapeHtml(formatPreciseKRWTitle(remainingValue))}">${formatDetailedKRW(remainingValue)}</span>
      </div>
      <div class="flex justify-between items-center gap-2 text-[11px] text-gray-500 mt-1">
        <span>체결 ${filledQty}</span>
        <span>${escapeHtml(updatedAt || '')}</span>
      </div>
      ${statusDetail}
    </div>
  `;
  }).join('');
}

function _setValueTitle(id, numericValue) {
  var el = document.getElementById(id);
  if (el) el.title = formatPreciseKRWTitle(numericValue);
}
