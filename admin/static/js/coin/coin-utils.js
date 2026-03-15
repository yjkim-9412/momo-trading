// ── coin-utils.js — Coin-specific formatting and utility functions ──

import { formatKRW } from '../shared/admin-core.js';
import { state } from './coin-state.js';

// ── Coin quantity formatting (0-8 decimals) ──
export function formatCoinQty(n) {
  if (n == null || Number.isNaN(Number(n))) return '-';
  var value = Number(n);
  if (value === 0) return '0';
  if (Math.abs(value) < 0.0001) return value.toExponential(4);
  if (Math.abs(value) < 1) return value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
  if (Math.abs(value) < 100) return value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  if (Math.abs(value) < 10000) return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// ── Price formatting (KRW or decimal) ──
export function formatPrice(n) {
  if (n == null || Number.isNaN(Number(n))) return '-';
  var value = Number(n);
  if (value >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (value >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (value >= 0.01) return value.toFixed(4);
  return value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
}

// ── KRW formatting for account/pending UI ──
export function formatDetailedKRW(n) {
  if (n == null || Number.isNaN(Number(n))) return '-';
  var value = Number(n);
  var rounded = Math.round(value);
  var exact = rounded.toLocaleString() + '원';
  if (Math.abs(rounded) < 10000) return exact;
  return exact + ' (' + formatKRW(rounded) + ')';
}

export function formatPreciseKRWTitle(n) {
  if (n == null || Number.isNaN(Number(n))) return '';
  var value = Number(n);
  return value.toLocaleString('ko-KR', { maximumFractionDigits: 6 }) + ' KRW';
}

// ── P&L with color ──
export function formatPnl(pnl, rate) {
  var pnlVal = Number(pnl ?? 0);
  var rateVal = Number(rate ?? 0);
  var sign = pnlVal > 0 ? '+' : pnlVal < 0 ? '-' : '';
  var color = pnlVal > 0 ? 'text-green-400' : pnlVal < 0 ? 'text-red-400' : 'text-gray-400';
  var pnlText = sign + formatDetailedKRW(Math.abs(pnlVal));
  var rateSign = rateVal > 0 ? '+' : rateVal < 0 ? '-' : '';
  var rateText = rateSign + Math.abs(rateVal).toFixed(2) + '%';
  return '<span class="' + color + '">' + pnlText + ' / ' + rateText + '</span>';
}

// ── Relative time ──
export function formatTimeAgo(dateStr) {
  if (!dateStr) return '';
  var now = Date.now();
  var then = new Date(dateStr).getTime();
  if (Number.isNaN(then)) return '';
  var diff = Math.floor((now - then) / 1000);
  if (diff < 60) return diff + '초 전';
  if (diff < 3600) return Math.floor(diff / 60) + '분 전';
  if (diff < 86400) return Math.floor(diff / 3600) + '시간 전';
  return Math.floor(diff / 86400) + '일 전';
}

// ── Seoul timezone full date-time ──
export function formatDateTime(ts) {
  if (!ts) return '--';
  try {
    var d = new Date(ts);
    return d.toLocaleString('ko-KR', {
      timeZone: 'Asia/Seoul',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch (e) {
    return String(ts);
  }
}

// ── Elapsed time ──
export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds)) || seconds < 0) return '-';
  var h = Math.floor(seconds / 3600);
  var m = Math.floor((seconds % 3600) / 60);
  var s = Math.floor(seconds % 60);
  if (h > 0) return h + '시간 ' + m + '분';
  if (m > 0) return m + '분';
  return s + '초';
}

// ── Timebox label ──
export function formatTimeboxHours(hours) {
  var value = Number(hours);
  if (!Number.isFinite(value) || value <= 0) return '--';
  return value + '시간';
}

export function formatTradingStyleMode(mode) {
  var value = String(mode || '').toUpperCase();
  if (value === 'AGGRESSIVE') return '공격적';
  if (value === 'CONSERVATIVE') return '보수적';
  return value || '--';
}

// ── Truncate with ellipsis ──
export function truncateText(text, limit) {
  if (!text) return '';
  if (limit == null) limit = 72;
  var normalized = String(text).replace(/\s+/g, ' ').trim();
  return normalized.length > limit ? normalized.slice(0, limit) + '...' : normalized;
}

// ── Map scan source labels ──
export function formatCoinScanSource(scanSource) {
  var source = String(scanSource || '').toUpperCase();
  if (source === 'DISCOVERY') return '발견';
  if (source === 'WATCHLIST') return '고정';
  if (source === 'WATCHLIST_FALLBACK') return '고정 폴백';
  if (source === 'HOLDING_FALLBACK') return '보유 폴백';
  return '';
}

// ── Signed KRW ──
export function formatSignedKRW(value) {
  var amount = Number(value ?? 0);
  if (!Number.isFinite(amount)) return '-';
  var sign = amount > 0 ? '+' : amount < 0 ? '-' : '';
  return sign + formatDetailedKRW(Math.abs(amount));
}

// ── Set inline status element ──
export function setInlineStatus(id, text, color) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = color || '';
}

// ── Set global status text ──
export function setStatusText(text, color) {
  var el = document.getElementById('status-text');
  if (!el) return;
  el.textContent = text;
  el.style.color = color || '';
}

// ── Set element text content ──
export function setTextContent(id, text) {
  var el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ── Safe JSON parsing ──
export function safeJsonParse(value, fallback) {
  if (fallback === undefined) fallback = null;
  if (value == null || value === '') return fallback;
  if (typeof value === 'object') return value;
  try {
    return JSON.parse(value);
  } catch (e) {
    return fallback;
  }
}

// ── Report source label ──
export function getReportSourceLabel(source) {
  if (source === 'AUTO_PRE_CYCLE') return '자동 회고';
  if (source === 'AUTO_SETTLEMENT') return '자동 정산';
  if (source === 'MANUAL') return '수동 리포트';
  return '리포트';
}

// ── Report trigger label ──
export function getReportTriggerReasonLabel(triggerReason) {
  var reason = String(triggerReason || '').toUpperCase();
  if (reason === 'TIMEBOX_12H') return '12시간 타임박스';
  if (reason === 'TIMEBOX_24H') return '24시간 타임박스';
  if (reason === 'MANUAL_GENERATE') return '수동 실행';
  return '';
}

// ── Report source + trigger label ──
export function getReportOriginLabel(source, triggerReason) {
  var sourceLabel = getReportSourceLabel(source);
  var triggerLabel = getReportTriggerReasonLabel(triggerReason);
  if (source === 'MANUAL') return sourceLabel;
  if (!triggerLabel || triggerLabel === sourceLabel) return sourceLabel;
  return sourceLabel + ' · ' + triggerLabel;
}

// ── Report headline ──
export function getReportHeadline(source) {
  if (source === 'AUTO_SETTLEMENT') return '코인 자동 정산 리포트';
  if (source === 'MANUAL') return '코인 수동 리포트';
  if (source === 'AUTO_PRE_CYCLE') return '코인 자동 회고 리포트';
  return '코인 리포트';
}

// ── Extract report timestamp ──
export function resolveReportTimestamp(report) {
  return (report && (report.period_ended_at || report.created_at)) || null;
}

// ── Populate report caches ──
export function setReportCaches(reports) {
  state.reportListCache = Array.isArray(reports) ? reports : [];
  state.reportCacheById = new Map(state.reportListCache.map(function (report) { return [report.id, report]; }));
  if (state.latestReportCache && state.latestReportCache.id && !state.reportCacheById.has(state.latestReportCache.id)) {
    state.reportCacheById.set(state.latestReportCache.id, state.latestReportCache);
  }
}

// ── Get selected cached report ──
export function getSelectedCachedReport() {
  if (state.currentReportSelection.mode === 'latest') {
    return state.latestReportCache || state.reportListCache[0] || null;
  }
  if (state.currentReportSelection.mode === 'id' && state.currentReportSelection.id) {
    return state.reportCacheById.get(state.currentReportSelection.id) || null;
  }
  return null;
}

// ── Metric delta flash ──
export function flashMetricDelta(deltaId, deltaValue) {
  var el = document.getElementById(deltaId);
  if (!el) return;

  var absDelta = Math.abs(Number(deltaValue || 0));
  if (absDelta < 1) {
    el.textContent = '';
    el.className = 'metric-delta';
    return;
  }

  var isPositive = deltaValue > 0;
  el.textContent = (isPositive ? '+' : '-') + formatKRW(absDelta);
  el.className = 'metric-delta show ' + (isPositive ? 'positive' : 'negative');

  if (state.metricDeltaTimers[deltaId]) clearTimeout(state.metricDeltaTimers[deltaId]);
  state.metricDeltaTimers[deltaId] = setTimeout(function () {
    el.className = 'metric-delta';
    el.textContent = '';
    delete state.metricDeltaTimers[deltaId];
  }, 3000);
}

// ── Update metric value with delta animation ──
export function updateMetricValue(id, deltaId, numericValue, formattedValue, threshold) {
  if (threshold == null) threshold = 1;
  var el = document.getElementById(id);
  if (!el) return;

  var prevValue = Number(el.dataset.value);
  el.textContent = formattedValue;
  el.dataset.value = String(numericValue);
  if (!Number.isNaN(prevValue) && Math.abs(numericValue - prevValue) >= threshold) {
    flashMetricDelta(deltaId, numericValue - prevValue);
  }
}
