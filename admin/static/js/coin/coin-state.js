// ── coin-state.js — Coin dashboard state, constants, feed accessors, shared module wiring ──

import * as Monitor from '../shared/admin-monitor.js';
import * as Feed from '../shared/admin-feed.js';
import * as Toast from '../shared/admin-toast.js';
import { formatPrice } from './coin-utils.js';

// ── API endpoint ──
export const API = '/api/v1/admin-coin';

// ── Card theme (coin-specific badge colors) ──
export const CardTheme = {
  progress: 'bg-violet-900/40 text-violet-300',
  buy: 'bg-green-900/40 text-green-300',
  sell: 'bg-amber-900/40 text-amber-200',
  hold: 'bg-gray-700/60 text-gray-300',
  error: 'bg-red-900/40 text-red-300',
  skip: 'bg-gray-700/60 text-gray-300',
};

// ── Feed state factory ──
function createFeedState() {
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

// ── Mutable state ──
export const state = {
  // View / UI
  currentView: 'live',
  currentLogFilter: 'ALL',
  autoScroll: true,
  missedCount: 0,
  isNearTop: false,
  eventSource: null,
  monitorExpanded: true,

  // Timers
  scanCountdownTimer: null,
  monitorUpdateTimer: null,
  monitorElapsedTimer: null,
  titleFlashInterval: null,
  accountRefreshTimer: null,
  metricDeltaTimers: {},

  // Feed
  feedState: createFeedState(),

  // Monitor
  monitorState: {
    cycleActive: false,
    cycleId: null,
    startedAt: null,
    scannedCount: 0,
    analyzedCount: 0,
    slots: {},
    completed: [],
    lastCycleSummary: null,
  },

  // Report caches
  latestReportCache: null,
  reportListCache: [],
  reportCacheById: new Map(),
  currentReportSelection: { mode: null, id: null },

  // System / agent state
  lastSystemStatus: null,
  lastAgentState: null,
  manualScanRequestPending: false,
  manualScanQueuedAt: null,
  manualScanSessionObserved: false,
  manualScanNotice: null,
  lastManualCycleActive: false,
  manualReportRequestPending: false,

  // Scan timeline
  lastCycleTime: null,
  scanIntervalHours: null,

  // Agent phase labels (coin-specific)
  AGENT_PHASE_LABELS: {
    BOOTSTRAP: '준비',
    SCAN: '스캔',
    ANALYSIS: '분석',
    DECISION: '판단',
    COMPLETE: '완료',
  },
};

// ── Feed accessors ──

export function getStockCards() { return state.feedState.stockCards; }
export function getActivityCount() { return state.feedState.activityCount; }
export function setActivityCount(v) { state.feedState.activityCount = v; }
export function incActivityCount() { state.feedState.activityCount += 1; }
export function getLoadedActivityIds() { return state.feedState.loadedActivityIds; }

export function getActivityIdentity(data) {
  return data.id || (data.created_at || '') + '|' + (data.activity_type || '') + '|' + (data.phase || '') + '|' + (data.symbol || '') + '|' + (data.summary || '');
}

export function rememberFeedActivities(activities, opts) {
  var prepend = opts && opts.prepend;
  var accepted = [];
  activities.forEach(function (activity) {
    var identity = getActivityIdentity(activity);
    if (state.feedState.loadedActivityIds.has(identity)) return;
    state.feedState.loadedActivityIds.add(identity);
    accepted.push(activity);
  });
  if (!accepted.length) return 0;
  state.feedState.feedItems = prepend
    ? [].concat(accepted, state.feedState.feedItems)
    : [].concat(state.feedState.feedItems, accepted);
  return accepted.length;
}

export function resetFeedState(opts) {
  var preserveLoaded = opts && opts.preserveLoaded;
  state.feedState.feedItems = [];
  state.feedState.feedTradingDate = null;
  state.feedState.feedHasMore = false;
  state.feedState.feedCursor = null;
  state.feedState.feedLoading = false;
  state.feedState.loadedActivityIds = new Set();
  state.feedState.activityCount = 0;
  if (!preserveLoaded) state.feedState.loaded = false;
}

// ── Initialize shared modules ──

// Late-bound reference for syncAgentSnapshot (defined in coin-actions.js)
let _syncAgentSnapshotFn = null;
export function registerSyncAgentSnapshot(fn) { _syncAgentSnapshotFn = fn; }

Monitor.init({
  getState: function () { return state.monitorState; },
  setState: function (s) { Object.assign(state.monitorState, s); },
  resolveScope: function () { return 'CRYPTO'; },
  getScopeState: function () { return state.monitorState; },
  setScopeState: function (_scope, s) { Object.assign(state.monitorState, s); },
  scopeLabel: function () { return '코인 사이클'; },
  syncAgentSnapshot: function (agentState) {
    if (_syncAgentSnapshotFn) _syncAgentSnapshotFn(agentState);
  },
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
  priceFormatter: function (n) { return formatPrice(n); },
  extractProductContext: function () { return null; },
  renderProductBadge: function () { return ''; },
  renderProductStrip: function () { return ''; },
});

Toast.setNotificationPrefix('momo-coin');
