// ── app-state.js — Multi-market state management (ES module) ──

import * as Monitor from '../shared/admin-monitor.js';
import * as Feed from '../shared/admin-feed.js';
import * as Toast from '../shared/admin-toast.js';
import { formatKRW } from '../shared/admin-core.js';

// ── API base ──
export const API = '/api/v1/admin';

// ── Card theme (outcome CSS classes) ──
export const CardTheme = {
  progress: 'outcome-progress',
  buy: 'outcome-buy',
  sell: 'outcome-sell',
  hold: 'outcome-hold',
  error: 'outcome-error',
};

// ── Mutable state ──
export const state = {
  // View / UI state
  currentView: 'live',
  currentMarket: 'KRX',
  currentLogFilter: 'ALL',
  autoScroll: true,
  missedCount: 0,
  isNearTop: false,

  // Enabled markets
  enabledMarkets: ['KRX'],

  // Timer references
  accountPollTimer: null,
  triggerPending: false,
  reportPending: false,

  // Per-market feed state
  marketState: {
    KRX: createMarketFeedState(),
    US: createMarketFeedState(),
  },
  activityBuffer: { KRX: [], US: [] },

  // Per-market monitor state
  monitorState: {
    KRX: createMonitorState(),
    US: createMonitorState(),
  },

  // Legacy alias — some functions reference this directly
  stockCards: null,

  // Lazy callback for priceFormatter (set by app-main.js after defining formatAmount)
  _priceFormatter: null,
  _extractProductContext: null,
  _renderProductBadge: null,
  _renderProductStrip: null,
};

// Initialize legacy alias
state.stockCards = state.marketState.KRX.stockCards;

// ── Constants ──
export var BUFFER_MAX = 300;

// ── Factory functions ──
export function createMarketFeedState() {
  return {
    stockCards: {},
    activityCount: 0,
    loaded: false,
    scrollPos: 0,
    feedItems: [],
    feedTradingDate: null,
    feedHasMore: false,
    feedCursor: null,
    feedLoading: false,
    loadedActivityIds: new Set(),
  };
}

export function createMonitorState() {
  return {
    cycleActive: false,
    cycleId: null,
    startedAt: null,
    scannedCount: 0,
    analyzedCount: 0,
    slots: {},
    completed: [],
    lastCycleSummary: null,
  };
}

// ── Scope helpers ──
export function currentScope() {
  return state.currentMarket === 'KRX' ? 'KRX' : 'US';
}

export function getStockCards() {
  return state.marketState[currentScope()].stockCards;
}

export function getActivityCount() {
  return state.marketState[currentScope()].activityCount;
}

export function setActivityCount(v) {
  state.marketState[currentScope()].activityCount = v;
}

export function incActivityCount() {
  state.marketState[currentScope()].activityCount++;
}

export function getMarketState(scope) {
  return state.marketState[scope || currentScope()];
}

export function getLoadedActivityIds() {
  return state.marketState[currentScope()].loadedActivityIds;
}

export function getMonitorState() {
  return state.monitorState[currentScope()];
}

// ── Feed identity / memory ──
export function getActivityIdentity(data) {
  return data.id || (data.created_at || '') + '|' + (data.activity_type || '') + '|' + (data.phase || '') + '|' + (data.symbol || '') + '|' + (data.summary || '');
}

export function rememberFeedActivities(scope, activities, opts) {
  var prepend = opts && opts.prepend;
  var ms = getMarketState(scope);
  var accepted = [];
  activities.forEach(function (activity) {
    var identity = getActivityIdentity(activity);
    if (ms.loadedActivityIds.has(identity)) return;
    ms.loadedActivityIds.add(identity);
    accepted.push(activity);
  });
  if (!accepted.length) return 0;
  ms.feedItems = prepend
    ? [].concat(accepted, ms.feedItems)
    : [].concat(ms.feedItems, accepted);
  return accepted.length;
}

export function resetFeedState(scope, opts) {
  var preserveLoaded = opts && opts.preserveLoaded;
  var ms = getMarketState(scope);
  ms.feedItems = [];
  ms.feedTradingDate = null;
  ms.feedHasMore = false;
  ms.feedCursor = null;
  ms.feedLoading = false;
  ms.loadedActivityIds = new Set();
  ms.activityCount = 0;
  if (!preserveLoaded) ms.loaded = false;
}

// ── Formatting helpers (needed by Feed.init at module scope) ──
export function truncateNumber(value, digits) {
  digits = digits || 0;
  var factor = Math.pow(10, digits);
  if (value >= 0) return Math.floor(value * factor) / factor;
  return Math.ceil(value * factor) / factor;
}

export function formatAmount(amount, currency) {
  currency = currency || 'KRW';
  if (amount == null || Number.isNaN(Number(amount))) return '-';
  if (currency === 'PCT') {
    var value = Number(amount);
    return (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
  }
  if (currency === 'USD') {
    return Number(amount).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }) + ' USD';
  }
  return formatKRW(Number(amount));
}

export function formatSignedAmount(amount, currency) {
  currency = currency || 'KRW';
  if (amount == null || Number.isNaN(Number(amount))) return '-';
  if (currency === 'PCT') {
    return formatAmount(amount, 'PCT');
  }
  var prefix = Number(amount) >= 0 ? '+' : '';
  return prefix + formatAmount(amount, currency);
}

export function convertKrwToUsd(amount, exchangeRate) {
  var rate = Number(exchangeRate || 0);
  if (!rate) return null;
  return Number(amount) / rate;
}

// ── Product Context Helpers ──
export function extractProductContext(data) {
  if (!data.detail) return null;
  try {
    var obj = typeof data.detail === 'string' ? JSON.parse(data.detail) : data.detail;
    return obj.product_context || null;
  } catch (e) { return null; }
}

export function renderProductBadge(pc) {
  if (!pc) return '';
  var mult = pc.leverage_multiplier || 1;
  var signed = pc.signed_exposure || mult;
  var label = (pc.is_inverse ? '' : '+') + signed + 'x';
  var restricted = pc.restricted_product;
  if (pc.is_inverse) {
    return '<span class="product-badge product-inverse' + (restricted ? ' product-restricted' : '') + '">' + label + '</span>';
  } else if (pc.is_leveraged || mult > 1) {
    return '<span class="product-badge product-leveraged' + (restricted ? ' product-restricted' : '') + '">' + label + '</span>';
  }
  return '';
}

export function renderProductStrip(pc) {
  if (!pc) return '';
  var mult = pc.leverage_multiplier || 1;
  var signed = pc.signed_exposure || mult;
  var label = (pc.is_inverse ? '' : '+') + signed + 'x';
  var typeLabel = pc.product_type ? pc.product_type.replace(/_/g, ' ') : '';
  var source = pc.classification_source || '';
  var restricted = pc.restricted_product;
  var cls = restricted ? 'product-strip-restricted' : 'product-strip';
  return '<div class="' + cls + ' mb-2"><span class="font-medium">' + label + '</span><span class="opacity-75">' + typeLabel + '</span>' + (restricted ? '<span class="product-restricted-label">\uC81C\uD55C \uC0C1\uD488</span>' : '') + '<span class="opacity-50">' + source + '</span></div>';
}

// ── Initialize shared modules ──
Monitor.init({
  getState: function () { return state.monitorState[currentScope()]; },
  setState: function (s) { state.monitorState[currentScope()] = s; },
  resolveScope: function (data) {
    var ms = (data && data.market_scope) || 'KRX';
    return ms === 'US' ? 'US' : 'KRX';
  },
  getScopeState: function (scope) { return state.monitorState[scope]; },
  setScopeState: function (scope, s) { state.monitorState[scope] = s; },
  scopeLabel: function () { return currentScope() === 'KRX' ? '\uAD6D\uB0B4' : '\uD574\uC678'; },
});

Feed.init({
  getStockCards: function () { return getStockCards(); },
  getActivityCount: function () { return getActivityCount(); },
  setActivityCount: function (v) { setActivityCount(v); },
  incActivityCount: function () { incActivityCount(); },
  getCurrentLogFilter: function () { return state.currentLogFilter; },
  getAutoScroll: function () { return state.autoScroll; },
  setAutoScroll: function (v) { state.autoScroll = v; },
  getMissedCount: function () { return state.missedCount; },
  setMissedCount: function (v) { state.missedCount = v; },
  getLoadedActivityIds: function () { return getLoadedActivityIds(); },
  getChatContainer: function () { return document.getElementById('chat-container'); },
  priceFormatter: function (n) { return formatAmount(n); },
  extractProductContext: function (data) { return extractProductContext(data); },
  renderProductBadge: function (ctx) { return renderProductBadge(ctx); },
  renderProductStrip: function (ctx) { return renderProductStrip(ctx); },
  rememberActivity: function (data) {
    // Dedup + store to feedItems for market-switch replay
    var scope = currentScope();
    return rememberFeedActivities(scope, [data]) > 0;
  },
});

Toast.setNotificationPrefix('momo');
