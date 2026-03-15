/**
 * app-state.js — Multi-market state management
 *
 * Defines Admin.API, Admin.CardTheme, Admin.App namespace with all
 * per-market state variables, scope helpers, and shared module init.
 * Loaded first among app/* files.
 */
(function () {
  'use strict';

  // ── API base & notification prefix ──
  Admin.API = '/api/v1/admin';
  Admin.notificationTagPrefix = 'momo';

  // ── Card theme (outcome CSS classes) ──
  Admin.CardTheme = {
    progress: 'outcome-progress',
    buy: 'outcome-buy',
    sell: 'outcome-sell',
    hold: 'outcome-hold',
    error: 'outcome-error',
  };

  // ── Constants (shared) ──
  Admin.LLM_AGENT_DEFAULTS = {
    tier1: {
      display_name: '\uD6C4\uBCF4 \uBD84\uC11D \uC5D0\uC774\uC804\uD2B8',
      short_label: '\uD6C4\uBCF4 \uBD84\uC11D',
      description: '\uCC28\uD2B8\xB7\uC2DC\uC7A5 \uCEE8\uD14D\uC2A4\uD2B8\uB97C \uBC14\uD0D5\uC73C\uB85C \uB9E4\uC218 \uD6C4\uBCF4\uC640 \uBAA9\uD45C/\uC190\uC808\uC744 1\uCC28 \uD310\uB2E8',
      icon: 'search',
    },
    tier2: {
      display_name: '\uCD5C\uC885 \uAC80\uD1A0 \uC5D0\uC774\uC804\uD2B8',
      short_label: '\uCD5C\uC885 \uAC80\uD1A0',
      description: '1\uCC28 \uBD84\uC11D \uACB0\uACFC\uB97C \uB9AC\uC2A4\uD06C\xB7\uD3EC\uD2B8\uD3F4\uB9AC\uC624 \uAD00\uC810\uC5D0\uC11C \uC7AC\uAC80\uC99D\uD574 \uC8FC\uBB38 \uC2B9\uC778 \uC5EC\uBD80\uB97C \uACB0\uC815',
      icon: 'shield-check',
    },
  };
  Admin.ADMIN_AGENT_LABELS = {
    TIER1: Admin.LLM_AGENT_DEFAULTS.tier1.display_name,
    TIER2: Admin.LLM_AGENT_DEFAULTS.tier2.display_name,
  };
  Admin.PIPELINE_STEPS = ['data', 'tier1', 'tier2', 'strategy', 'decision'];
  Admin.PIPELINE_LABELS = { data: '\uC870\uD68C', tier1: 'Tier1', tier2: 'Tier2', strategy: '\uC804\uB7B5', decision: '\uACB0\uC815' };
  Admin.ORIGINAL_TITLE = document.title || 'MOMO Trading Admin';

  // ── App namespace ──
  var App = Admin.App = {};

  // ── View / UI state ──
  App.currentView = 'live';
  App.currentMarket = 'KRX';
  App.currentLogFilter = 'ALL';
  App.autoScroll = true;
  App.missedCount = 0;
  App.isNearTop = false;

  // ── Enabled markets ──
  App.enabledMarkets = ['KRX'];

  // ── Timer references ──
  App.accountPollTimer = null;
  App.triggerPending = false;
  App.reportPending = false;

  // ── Per-market feed state ──
  var BUFFER_MAX = 300;
  App.BUFFER_MAX = BUFFER_MAX;

  function createMarketFeedState() {
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
  App.createMarketFeedState = createMarketFeedState;

  App.marketState = {
    KRX: createMarketFeedState(),
    US: createMarketFeedState(),
  };
  App.activityBuffer = { KRX: [], US: [] };

  // ── Per-market monitor state ──
  function createMonitorState() {
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
  App.createMonitorState = createMonitorState;

  App.monitorState = {
    KRX: createMonitorState(),
    US: createMonitorState(),
  };

  // Legacy alias — some functions reference this directly
  App.stockCards = App.marketState.KRX.stockCards;

  // ── Scope helpers ──
  App.currentScope = function () {
    return App.currentMarket === 'KRX' ? 'KRX' : 'US';
  };

  App.getStockCards = function () {
    return App.marketState[App.currentScope()].stockCards;
  };

  App.getActivityCount = function () {
    return App.marketState[App.currentScope()].activityCount;
  };

  App.setActivityCount = function (v) {
    App.marketState[App.currentScope()].activityCount = v;
  };

  App.incActivityCount = function () {
    App.marketState[App.currentScope()].activityCount++;
  };

  App.getMarketState = function (scope) {
    return App.marketState[scope || App.currentScope()];
  };

  App.getLoadedActivityIds = function () {
    return App.marketState[App.currentScope()].loadedActivityIds;
  };

  App.getMonitorState = function () {
    return App.monitorState[App.currentScope()];
  };

  // ── Feed identity / memory ──
  App.getActivityIdentity = function (data) {
    return data.id || (data.created_at || '') + '|' + (data.activity_type || '') + '|' + (data.phase || '') + '|' + (data.symbol || '') + '|' + (data.summary || '');
  };

  App.rememberFeedActivities = function (scope, activities, opts) {
    var prepend = opts && opts.prepend;
    var state = App.getMarketState(scope);
    var accepted = [];
    activities.forEach(function (activity) {
      var identity = App.getActivityIdentity(activity);
      if (state.loadedActivityIds.has(identity)) return;
      state.loadedActivityIds.add(identity);
      accepted.push(activity);
    });
    if (!accepted.length) return 0;
    state.feedItems = prepend
      ? [].concat(accepted, state.feedItems)
      : [].concat(state.feedItems, accepted);
    return accepted.length;
  };

  App.resetFeedState = function (scope, opts) {
    var preserveLoaded = opts && opts.preserveLoaded;
    var state = App.getMarketState(scope);
    state.feedItems = [];
    state.feedTradingDate = null;
    state.feedHasMore = false;
    state.feedCursor = null;
    state.feedLoading = false;
    state.loadedActivityIds = new Set();
    state.activityCount = 0;
    if (!preserveLoaded) state.loaded = false;
  };

  // ── Initialize shared modules ──
  Admin.Monitor.init({
    getState: function () { return App.monitorState[App.currentScope()]; },
    setState: function (s) { App.monitorState[App.currentScope()] = s; },
    resolveScope: function (data) {
      var ms = (data && data.market_scope) || 'KRX';
      return ms === 'US' ? 'US' : 'KRX';
    },
    getScopeState: function (scope) { return App.monitorState[scope]; },
    setScopeState: function (scope, s) { App.monitorState[scope] = s; },
    scopeLabel: function () { return App.currentScope() === 'KRX' ? '\uAD6D\uB0B4' : '\uD574\uC678'; },
  });

  Admin.Feed.init({
    getStockCards: function () { return App.getStockCards(); },
    getActivityCount: function () { return App.getActivityCount(); },
    setActivityCount: function (v) { App.setActivityCount(v); },
    incActivityCount: function () { App.incActivityCount(); },
    getCurrentLogFilter: function () { return App.currentLogFilter; },
    getAutoScroll: function () { return App.autoScroll; },
    setAutoScroll: function (v) { App.autoScroll = v; },
    getMissedCount: function () { return App.missedCount; },
    setMissedCount: function (v) { App.missedCount = v; },
    getLoadedActivityIds: function () { return App.getLoadedActivityIds(); },
    getChatContainer: function () { return document.getElementById('chat-container'); },
    priceFormatter: function (n) { return App.formatAmount(n); },
    extractProductContext: function (data) { return App.extractProductContext(data); },
    renderProductBadge: function (ctx) { return App.renderProductBadge(ctx); },
    renderProductStrip: function (ctx) { return App.renderProductStrip(ctx); },
    rememberActivity: function (data) {
      // Dedup + store to feedItems for market-switch replay
      var scope = App.currentScope();
      return App.rememberFeedActivities(scope, [data]) > 0;
    },
  });
})();
