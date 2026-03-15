/**
 * coin-state.js — Coin dashboard state management
 *
 * Initializes Admin.API, Admin.Coin namespace, feed/monitor state,
 * report caches, and wires up Admin.Monitor / Admin.Feed shared modules.
 * Must be loaded first among coin/*.js files.
 */
(function () {
  'use strict';

  // ── API endpoint & notification tag ──
  Admin.API = '/api/v1/admin-coin';
  Admin.notificationTagPrefix = 'momo-coin';

  // ── Card theme (coin-specific badge colors) ──
  Admin.CardTheme = {
    progress: 'bg-violet-900/40 text-violet-300',
    buy: 'bg-green-900/40 text-green-300',
    sell: 'bg-amber-900/40 text-amber-200',
    hold: 'bg-gray-700/60 text-gray-300',
    error: 'bg-red-900/40 text-red-300',
    skip: 'bg-gray-700/60 text-gray-300',
  };

  // ── Namespace ──
  var Coin = Admin.Coin = {};

  // ── View / UI state ──
  Coin.currentView = 'live';
  Coin.currentLogFilter = 'ALL';
  Coin.autoScroll = true;
  Coin.missedCount = 0;
  Coin.isNearTop = false;
  Coin.eventSource = null;
  Coin.monitorExpanded = true;

  // ── Timer references ──
  Coin.scanCountdownTimer = null;
  Coin.monitorUpdateTimer = null;
  Coin.monitorElapsedTimer = null;
  Coin.titleFlashInterval = null;
  Coin.accountRefreshTimer = null;
  Coin.metricDeltaTimers = {};

  // ── Feed state ──
  Coin.feedState = createFeedState();

  // ── Monitor state ──
  Coin.monitorState = {
    cycleActive: false,
    cycleId: null,
    startedAt: null,
    scannedCount: 0,
    analyzedCount: 0,
    slots: {},
    completed: [],
    lastCycleSummary: null,
  };

  // ── Report caches ──
  Coin.latestReportCache = null;
  Coin.reportListCache = [];
  Coin.reportCacheById = new Map();
  Coin.currentReportSelection = { mode: null, id: null };

  // ── System / agent state ──
  Coin.lastSystemStatus = null;
  Coin.lastAgentState = null;
  Coin.manualScanRequestPending = false;
  Coin.manualScanQueuedAt = null;
  Coin.manualScanSessionObserved = false;
  Coin.manualScanNotice = null;
  Coin.lastManualCycleActive = false;
  Coin.manualReportRequestPending = false;

  // ── Scan timeline ──
  Coin.lastCycleTime = null;
  Coin.scanIntervalHours = null;

  // ── Agent phase labels (coin-specific) ──
  Coin.AGENT_PHASE_LABELS = {
    BOOTSTRAP: '준비',
    SCAN: '스캔',
    ANALYSIS: '분석',
    DECISION: '판단',
    COMPLETE: '완료',
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

  // ── Feed accessors ──
  Coin.getStockCards = function () { return Coin.feedState.stockCards; };
  Coin.getActivityCount = function () { return Coin.feedState.activityCount; };
  Coin.setActivityCount = function (v) { Coin.feedState.activityCount = v; };
  Coin.incActivityCount = function () { Coin.feedState.activityCount += 1; };
  Coin.getLoadedActivityIds = function () { return Coin.feedState.loadedActivityIds; };

  Coin.getActivityIdentity = function (data) {
    return data.id || (data.created_at || '') + '|' + (data.activity_type || '') + '|' + (data.phase || '') + '|' + (data.symbol || '') + '|' + (data.summary || '');
  };

  Coin.rememberFeedActivities = function (activities, opts) {
    var prepend = opts && opts.prepend;
    var accepted = [];
    activities.forEach(function (activity) {
      var identity = Coin.getActivityIdentity(activity);
      if (Coin.feedState.loadedActivityIds.has(identity)) return;
      Coin.feedState.loadedActivityIds.add(identity);
      accepted.push(activity);
    });
    if (!accepted.length) return 0;
    Coin.feedState.feedItems = prepend
      ? [].concat(accepted, Coin.feedState.feedItems)
      : [].concat(Coin.feedState.feedItems, accepted);
    return accepted.length;
  };

  Coin.resetFeedState = function (opts) {
    var preserveLoaded = opts && opts.preserveLoaded;
    Coin.feedState.feedItems = [];
    Coin.feedState.feedTradingDate = null;
    Coin.feedState.feedHasMore = false;
    Coin.feedState.feedCursor = null;
    Coin.feedState.feedLoading = false;
    Coin.feedState.loadedActivityIds = new Set();
    Coin.feedState.activityCount = 0;
    if (!preserveLoaded) Coin.feedState.loaded = false;
  };

  // ── Initialize shared modules ──

  Admin.Monitor.init({
    getState: function () { return Coin.monitorState; },
    setState: function (s) { Object.assign(Coin.monitorState, s); },
    resolveScope: function () { return 'CRYPTO'; },
    getScopeState: function () { return Coin.monitorState; },
    setScopeState: function (_scope, s) { Object.assign(Coin.monitorState, s); },
    scopeLabel: function () { return '코인 사이클'; },
    syncAgentSnapshot: function (state) {
      // Delegate to Coin.syncAgentSnapshot (defined in coin-actions.js, loaded later)
      if (Coin.syncAgentSnapshot) Coin.syncAgentSnapshot(state);
    },
  });

  Admin.Feed.init({
    getStockCards: function () { return Coin.getStockCards(); },
    getActivityCount: function () { return Coin.getActivityCount(); },
    setActivityCount: function (v) { Coin.setActivityCount(v); },
    incActivityCount: function () { Coin.incActivityCount(); },
    getCurrentLogFilter: function () { return Coin.currentLogFilter; },
    getAutoScroll: function () { return Coin.autoScroll; },
    setAutoScroll: function (v) { Coin.autoScroll = v; },
    getMissedCount: function () { return Coin.missedCount; },
    setMissedCount: function (v) { Coin.missedCount = v; },
    getLoadedActivityIds: function () { return Coin.getLoadedActivityIds(); },
    getChatContainer: function () { return document.getElementById('chat-container'); },
    priceFormatter: function (n) { return Coin.formatPrice(n); },
    extractProductContext: function () { return null; },
    renderProductBadge: function () { return ''; },
    renderProductStrip: function () { return ''; },
  });
})();
