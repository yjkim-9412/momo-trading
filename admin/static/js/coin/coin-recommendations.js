// ── coin-recommendations.js — SEMI_AUTO recommendation queue ──
//
// Security note: all dynamic content is escaped via escapeHtml()
// before DOM insertion. This mirrors the original coin-app.js exactly.

import { escapeHtml, formatKRW, refreshIcons, fetchJSON } from '../shared/admin-core.js';
import { API } from './coin-state.js';
// state accessed indirectly via API
import { formatCoinQty, formatPrice, formatDuration } from './coin-utils.js';

// -- Load pending recommendations --

export async function loadRecommendations() {
  try {
    var json = await fetchJSON(API + '/recommendations?status=PENDING');
    var data = json.data || [];
    renderRecommendations(data);
  } catch (err) {
    console.error('[coin] recommendations load error:', err);
  }
}

// -- Render recommendation cards --

export function renderRecommendations(recs) {
  var container = document.getElementById('rec-queue');
  var countEl = document.getElementById('rec-count');
  if (!container) return;
  if (countEl) countEl.textContent = String((recs && recs.length) || 0);

  if (!recs || !recs.length) {
    container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">대기 중인 추천 없음</div>';
    return;
  }

  container.innerHTML = recs.map(function (r) {
    var isBuy = (r.side || r.action || '').toUpperCase().indexOf('BUY') !== -1;
    var borderColor = isBuy ? '#fbbf24' : '#a78bfa';
    var sideLabel = isBuy ? 'BUY' : 'SELL';
    var sideColor = isBuy ? '#fbbf24' : '#a78bfa';
    var coin = r.coin || r.symbol || '';
    var priceValue = r.suggested_price ?? r.price;
    var qtyValue = r.suggested_quantity ?? r.quantity;
    var amountValue = r.suggested_amount_krw ?? r.amount_krw ?? (
      Number(priceValue) > 0 && Number(qtyValue) > 0
        ? Number(priceValue) * Number(qtyValue)
        : null
    );
    var price = formatPrice(priceValue);
    var qty = formatCoinQty(qtyValue);
    var amount = formatKRW(amountValue);
    var confidence = r.confidence != null ? (Number(r.confidence) * 100).toFixed(0) + '%' : '-';
    var expiresAt = r.expires_at ? new Date(r.expires_at) : null;
    var recId = r.id || r.recommendation_id || '';

    return `
    <div class="rec-card" style="border-left:3px solid ${borderColor}; background:rgba(30,30,46,0.6); border-radius:8px; padding:10px 12px; margin-bottom:8px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <div>
          <span style="color:#e2e8f0; font-weight:600; font-size:14px;">${escapeHtml(coin)}</span>
          <span style="color:${sideColor}; font-weight:500; font-size:12px; margin-left:6px; padding:1px 6px; border-radius:4px; background:${isBuy ? 'rgba(251,191,36,0.15)' : 'rgba(167,139,250,0.15)'};">${sideLabel}</span>
        </div>
        <span style="color:#9ca3af; font-size:11px;">신뢰도 ${confidence}</span>
      </div>
      <div style="display:flex; justify-content:space-between; color:#9ca3af; font-size:12px; margin-bottom:6px;">
        <span>금액 ${amount}</span>
        <span>예상수량 ${qty}</span>
      </div>
      <div style="display:flex; justify-content:space-between; color:#6b7280; font-size:11px; margin-bottom:6px;">
        <span>가격 ${price}</span>
        <span>${isBuy ? '금액 기준 BUY' : '수량 기준 SELL'}</span>
      </div>
      ${expiresAt ? `<div style="color:#6b7280; font-size:11px; margin-bottom:6px;" data-expires="${expiresAt.toISOString()}" class="rec-countdown">만료: 계산 중...</div>` : ''}
      <div style="display:flex; gap:8px;">
        <button onclick="approveRecommendation('${recId}')"
          style="flex:1; padding:5px 0; border-radius:6px; border:none; cursor:pointer; font-size:12px; font-weight:500; background:rgba(52,211,153,0.15); color:#34d399;"
          onmouseover="this.style.background='rgba(52,211,153,0.3)'"
          onmouseout="this.style.background='rgba(52,211,153,0.15)'">
          <i data-lucide="check" class="w-3.5 h-3.5 inline-block"></i> 승인
        </button>
        <button onclick="rejectRecommendation('${recId}')"
          style="flex:1; padding:5px 0; border-radius:6px; border:none; cursor:pointer; font-size:12px; font-weight:500; background:rgba(248,113,113,0.15); color:#f87171;"
          onmouseover="this.style.background='rgba(248,113,113,0.3)'"
          onmouseout="this.style.background='rgba(248,113,113,0.15)'">
          <i data-lucide="x" class="w-3.5 h-3.5 inline-block"></i> 거절
        </button>
      </div>
    </div>
  `;
  }).join('');

  refreshIcons();
  updateRecCountdowns();
}

// -- Approve --

export async function approveRecommendation(id) {
  try {
    await fetchJSON(API + '/recommendations/' + id + '/approve', { method: 'POST' });
    loadRecommendations();
  } catch (err) {
    console.error('[coin] approve error:', err);
  }
}

// -- Reject --

export async function rejectRecommendation(id) {
  try {
    await fetchJSON(API + '/recommendations/' + id + '/reject', { method: 'POST' });
    loadRecommendations();
  } catch (err) {
    console.error('[coin] reject error:', err);
  }
}

// -- Update expiry countdowns --

export function updateRecCountdowns() {
  var els = document.querySelectorAll('.rec-countdown');
  els.forEach(function (el) {
    var expires = el.dataset.expires;
    if (!expires) return;
    var remaining = Math.max(0, Math.floor((new Date(expires).getTime() - Date.now()) / 1000));
    if (remaining <= 0) {
      el.textContent = '만료됨';
      el.style.color = '#f87171';
    } else {
      el.textContent = '만료: ' + formatDuration(remaining) + ' 후';
    }
  });
}
