/**
 * coin-account.js -- Account display: balance, holdings, pending orders
 *
 * Security note: all dynamic content is escaped via Admin.escapeHtml()
 * before DOM insertion. This mirrors the original coin-app.js exactly.
 */
(function () {
  'use strict';

  var Coin = Admin.Coin;

  // -- Load account overview --

  Coin.loadBalance = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/account/overview');
      var data = json.data;
      if (!data) return;
      Coin.renderBalance(data.balance || data);
      Coin.renderHoldings(data.holdings || []);
      Coin.renderPendingOrders(data.pending_orders || []);
    } catch (err) {
      console.error('[coin] balance load error:', err);
      Coin.setInlineStatus('sys-exchange', '오류', '#f87171');
    }
  };

  // -- Render balance metrics --

  Coin.renderBalance = function (b) {
    if (!b) {
      Coin.setInlineStatus('sys-exchange', '데이터 없음', '#9ca3af');
      return;
    }
    var totalAsset = Number(b.total_asset ?? 0);
    var cash = Number(b.cash ?? 0);
    var coinValue = Number(b.coin_value ?? b.stock_value ?? 0);
    var lockedKrw = Number(b.locked_krw ?? 0);
    var totalPnl = Number(b.total_pnl ?? 0);
    var totalPnlRate = Number(b.total_pnl_rate ?? 0);

    Coin.updateMetricValue('total-asset', 'total-asset-delta', totalAsset, Admin.formatKRW(totalAsset));
    Coin.updateMetricValue('cash-balance', 'cash-balance-delta', cash, Admin.formatKRW(cash));
    Coin.updateMetricValue('coin-eval', 'coin-eval-delta', coinValue, Admin.formatKRW(coinValue));
    Coin.updateMetricValue('locked-krw', 'locked-krw-delta', lockedKrw, Admin.formatKRW(lockedKrw));

    var pnlEl = document.getElementById('total-pnl');
    if (pnlEl) {
      pnlEl.innerHTML = Coin.formatPnl(totalPnl, totalPnlRate);
    }

    var exchangeMessage = b.is_valid === false
      ? '오류' + (b.status_message ? ' · ' + Coin.truncateText(b.status_message, 48) : '')
      : '정상' + (b.status_message ? ' · ' + Coin.truncateText(b.status_message, 48) : '');
    Coin.setInlineStatus('sys-exchange', exchangeMessage, b.is_valid === false ? '#f87171' : '#34d399');
  };

  // -- Render holdings --

  Coin.renderHoldings = function (holdings) {
    var container = document.getElementById('holdings-list');
    if (!container) return;

    Coin.setTextContent('holdings-count', ((holdings && holdings.length) || 0) + '종목');

    if (!holdings || !holdings.length) {
      container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">보유 코인 없음</div>';
      return;
    }

    container.innerHTML = holdings.map(function (h) {
      var pnlVal = Number(h.pnl ?? 0);
      var pnlRate = Number(h.pnl_rate ?? 0);
      var pnlColor = pnlVal > 0 ? '#34d399' : pnlVal < 0 ? '#f87171' : '#9ca3af';
      var pnlSign = pnlVal >= 0 ? '+' : '';
      var qty = Coin.formatCoinQty(h.quantity);
      var avgPrice = Coin.formatPrice(h.avg_buy_price);
      var curPrice = Coin.formatPrice(h.current_price);
      var evalAmt = Admin.formatKRW(h.current_price * h.quantity);
      var name = h.name || h.symbol || '';
      var symbol = h.symbol || '';

      return `
      <div class="holding-card" style="background:rgba(30,30,46,0.6); border-radius:8px; padding:10px 12px; margin-bottom:6px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div style="min-width:0;">
            <div style="color:#e2e8f0; font-weight:500; font-size:13px;">${Admin.escapeHtml(name)}</div>
            <div style="color:#6b7280; font-size:11px;">${Admin.escapeHtml(symbol)} · ${qty}</div>
          </div>
          <span style="color:${pnlColor}; font-weight:500; font-size:13px;">${pnlSign}${pnlRate.toFixed(2)}%</span>
        </div>
        <div style="display:flex; justify-content:space-between; color:#9ca3af; font-size:11px; margin-top:4px;">
          <span>평단 ${avgPrice}</span>
          <span>현재 ${curPrice}</span>
        </div>
        <div style="display:flex; justify-content:space-between; color:#6b7280; font-size:11px; margin-top:2px;">
          <span>평가 ${evalAmt}</span>
          <span style="color:${pnlColor};">${pnlSign}${Admin.formatKRW(pnlVal)}</span>
        </div>
      </div>
    `;
    }).join('');
  };

  // -- Pending order time --

  Coin.formatPendingOrderTime = function (order) {
    if (order && order.updated_at) return Admin.formatTime(order.updated_at);
    if (order && order.submitted_at) return Admin.formatTime(order.submitted_at);
    var orderTime = String((order && order.order_time) || '');
    if (orderTime.length === 6) {
      return orderTime.slice(0, 2) + ':' + orderTime.slice(2, 4) + ':' + orderTime.slice(4, 6);
    }
    return '';
  };

  // -- Order status meta --

  Coin.getPendingOrderStatusMeta = function (status) {
    var normalized = String(status || '').toUpperCase();
    switch (normalized) {
      case 'PARTIAL': return { label: '부분체결', className: 'status-partial' };
      case 'FILLED': return { label: '체결완료', className: 'status-filled' };
      case 'CANCELED': return { label: '취소', className: 'status-canceled' };
      case 'OPEN': return { label: '대기', className: '' };
      case 'SUBMITTED': return { label: '접수', className: '' };
      default: return { label: normalized || '대기', className: '' };
    }
  };

  // -- Render pending orders --

  Coin.renderPendingOrders = function (orders) {
    var container = document.getElementById('pending-list');
    var countEl = document.getElementById('pending-count');
    if (!container || !countEl) return;

    if (!orders || !orders.length) {
      countEl.textContent = '0건';
      container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">미체결 주문 없음</div>';
      return;
    }

    var totalAmount = orders.reduce(function (sum, order) {
      return sum + (Number(order.order_price || 0) * Number(order.remaining_qty || 0));
    }, 0);
    countEl.textContent = orders.length + '건 (' + Admin.formatKRW(totalAmount) + ')';

    container.innerHTML = orders.map(function (order) {
      var isBuy = order.side === '매수';
      var sideClass = isBuy ? 'buy' : 'sell';
      var statusMeta = Coin.getPendingOrderStatusMeta(order.status);
      var updatedAt = Coin.formatPendingOrderTime(order);
      var remainingQty = Coin.formatCoinQty(order.remaining_qty);
      var orderQty = Coin.formatCoinQty(order.order_qty);
      var filledQty = Coin.formatCoinQty(order.filled_qty);
      var statusDetail = order.status_detail
        ? `<div class="text-[10px] text-gray-500 truncate" title="${Admin.escapeHtml(order.status_detail)}">${Admin.escapeHtml(order.status_detail)}</div>`
        : '';
      return `
      <div class="coin-pending-card">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="text-gray-100 font-medium truncate" title="${Admin.escapeHtml(order.symbol)}">${Admin.escapeHtml(order.name || order.symbol)}</div>
            <div class="text-[11px] text-gray-500">${Admin.escapeHtml(order.symbol || '')}</div>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <span class="coin-side-chip ${sideClass}">${Admin.escapeHtml(order.side || '')}</span>
            <span class="coin-status-chip ${statusMeta.className}">${Admin.escapeHtml(statusMeta.label)}</span>
          </div>
        </div>
        <div class="flex justify-between items-center gap-2 mt-1.5 text-[11px] text-gray-300">
          <span>주문가</span>
          <span>${Coin.formatPrice(order.order_price)} KRW</span>
        </div>
        <div class="flex justify-between items-center gap-2 text-[11px] text-gray-400 mt-1">
          <span>미체결 ${remainingQty} / 주문 ${orderQty}</span>
          <span>${Admin.formatKRW(Number(order.order_price || 0) * Number(order.remaining_qty || 0))}</span>
        </div>
        <div class="flex justify-between items-center gap-2 text-[11px] text-gray-500 mt-1">
          <span>체결 ${filledQty}</span>
          <span>${Admin.escapeHtml(updatedAt || '')}</span>
        </div>
        ${statusDetail}
      </div>
    `;
    }).join('');
  };
})();
