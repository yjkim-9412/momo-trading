/**
 * coin-utils.js — Coin-specific formatting and utility functions
 *
 * Provides price/qty formatters, status helpers, time formatting,
 * report cache helpers, and DOM convenience functions.
 */
(function () {
  'use strict';

  var Coin = Admin.Coin;

  // ── Coin quantity formatting (0-8 decimals) ──
  Coin.formatCoinQty = function (n) {
    if (n == null || Number.isNaN(Number(n))) return '-';
    var value = Number(n);
    if (value === 0) return '0';
    if (Math.abs(value) < 0.0001) return value.toExponential(4);
    if (Math.abs(value) < 1) return value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
    if (Math.abs(value) < 100) return value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
    if (Math.abs(value) < 10000) return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  };

  // ── Price formatting (KRW or decimal) ──
  Coin.formatPrice = function (n) {
    if (n == null || Number.isNaN(Number(n))) return '-';
    var value = Number(n);
    if (value >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (value >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (value >= 0.01) return value.toFixed(4);
    return value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
  };

  // ── P&L with color ──
  Coin.formatPnl = function (pnl, rate) {
    var pnlVal = Number(pnl ?? 0);
    var rateVal = Number(rate ?? 0);
    var sign = pnlVal >= 0 ? '+' : '';
    var color = pnlVal > 0 ? 'text-green-400' : pnlVal < 0 ? 'text-red-400' : 'text-gray-400';
    var pnlText = sign + Admin.formatKRW(pnlVal);
    var rateText = sign + rateVal.toFixed(2) + '%';
    return '<span class="' + color + '">' + pnlText + ' / ' + rateText + '</span>';
  };

  // ── Relative time ──
  Coin.formatTimeAgo = function (dateStr) {
    if (!dateStr) return '';
    var now = Date.now();
    var then = new Date(dateStr).getTime();
    if (Number.isNaN(then)) return '';
    var diff = Math.floor((now - then) / 1000);
    if (diff < 60) return diff + '초 전';
    if (diff < 3600) return Math.floor(diff / 60) + '분 전';
    if (diff < 86400) return Math.floor(diff / 3600) + '시간 전';
    return Math.floor(diff / 86400) + '일 전';
  };

  // ── Seoul timezone full date-time ──
  Coin.formatDateTime = function (ts) {
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
  };

  // ── Elapsed time ──
  Coin.formatDuration = function (seconds) {
    if (seconds == null || Number.isNaN(Number(seconds)) || seconds < 0) return '-';
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = Math.floor(seconds % 60);
    if (h > 0) return h + '시간 ' + m + '분';
    if (m > 0) return m + '분';
    return s + '초';
  };

  // ── Truncate with ellipsis ──
  Coin.truncateText = function (text, limit) {
    if (!text) return '';
    if (limit == null) limit = 72;
    var normalized = String(text).replace(/\s+/g, ' ').trim();
    return normalized.length > limit ? normalized.slice(0, limit) + '...' : normalized;
  };

  // ── Map scan source labels ──
  Coin.formatCoinScanSource = function (scanSource) {
    var source = String(scanSource || '').toUpperCase();
    if (source === 'DISCOVERY') return '발견';
    if (source === 'WATCHLIST') return '고정';
    if (source === 'WATCHLIST_FALLBACK') return '고정 폴백';
    if (source === 'HOLDING_FALLBACK') return '보유 폴백';
    return '';
  };

  // ── Signed KRW ──
  Coin.formatSignedKRW = function (value) {
    var amount = Number(value ?? 0);
    if (!Number.isFinite(amount)) return '-';
    var sign = amount > 0 ? '+' : amount < 0 ? '-' : '';
    return sign + Admin.formatKRW(Math.abs(amount));
  };

  // ── Set inline status element ──
  Coin.setInlineStatus = function (id, text, color) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.style.color = color || '';
  };

  // ── Set global status text ──
  Coin.setStatusText = function (text, color) {
    var el = document.getElementById('status-text');
    if (!el) return;
    el.textContent = text;
    el.style.color = color || '';
  };

  // ── Set element text content ──
  Coin.setTextContent = function (id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  };

  // ── Safe JSON parsing ──
  Coin.safeJsonParse = function (value, fallback) {
    if (fallback === undefined) fallback = null;
    if (value == null || value === '') return fallback;
    if (typeof value === 'object') return value;
    try {
      return JSON.parse(value);
    } catch (e) {
      return fallback;
    }
  };

  // ── Report source label ──
  Coin.getReportSourceLabel = function (source) {
    if (source === 'AUTO_PRE_CYCLE') return '자동 회고';
    if (source === 'MANUAL') return '수동 생성';
    return '리포트';
  };

  // ── Extract report timestamp ──
  Coin.resolveReportTimestamp = function (report) {
    return (report && (report.period_ended_at || report.created_at)) || null;
  };

  // ── Populate report caches ──
  Coin.setReportCaches = function (reports) {
    Coin.reportListCache = Array.isArray(reports) ? reports : [];
    Coin.reportCacheById = new Map(Coin.reportListCache.map(function (report) { return [report.id, report]; }));
    if (Coin.latestReportCache && Coin.latestReportCache.id && !Coin.reportCacheById.has(Coin.latestReportCache.id)) {
      Coin.reportCacheById.set(Coin.latestReportCache.id, Coin.latestReportCache);
    }
  };

  // ── Get selected cached report ──
  Coin.getSelectedCachedReport = function () {
    if (Coin.currentReportSelection.mode === 'latest') {
      return Coin.latestReportCache || Coin.reportListCache[0] || null;
    }
    if (Coin.currentReportSelection.mode === 'id' && Coin.currentReportSelection.id) {
      return Coin.reportCacheById.get(Coin.currentReportSelection.id) || null;
    }
    return null;
  };
})();
