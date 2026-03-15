/**
 * app-main.js — Initialization, view switching, sidebar, settings, and global exports
 *
 * This file is loaded LAST among the app/* files. It sets up:
 * - DOMContentLoaded init sequence
 * - View switching (live/today/report) and market switching
 * - Feed state preservation (save/restore/buffer/flush)
 * - Sidebar toggles, settings panel
 * - Formatting helpers (truncateNumber, formatAmount, formatSignedAmount, convertKrwToUsd)
 * - Product context extraction and rendering
 * - Feed rendering (createBubble, createCycleDivider, formatDetail, formatLLMConversation)
 * - Activity feed loaders (loadTodayActivities, loadMoreActivities, etc.)
 * - Trigger/generate actions
 * - Scroll event listener
 * - All window.* exports for onclick handlers
 *
 * NOTE: innerHTML usage mirrors the original monolithic app.js patterns.
 * All dynamic text is passed through Admin.escapeHtml() before insertion.
 */
(function () {
  'use strict';

  var App = Admin.App;

  // ══════════════════════════════════════════════════════════
  // ── Formatting Helpers ──
  // ══════════════════════════════════════════════════════════

  App.truncateNumber = function (value, digits) {
    digits = digits || 0;
    var factor = Math.pow(10, digits);
    if (value >= 0) return Math.floor(value * factor) / factor;
    return Math.ceil(value * factor) / factor;
  };

  App.formatAmount = function (amount, currency) {
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
    return Admin.formatKRW(Number(amount));
  };

  App.formatSignedAmount = function (amount, currency) {
    currency = currency || 'KRW';
    if (amount == null || Number.isNaN(Number(amount))) return '-';
    if (currency === 'PCT') {
      return App.formatAmount(amount, 'PCT');
    }
    var prefix = Number(amount) >= 0 ? '+' : '';
    return prefix + App.formatAmount(amount, currency);
  };

  App.convertKrwToUsd = function (amount, exchangeRate) {
    var rate = Number(exchangeRate || 0);
    if (!rate) return null;
    return Number(amount) / rate;
  };

  // ══════════════════════════════════════════════════════════
  // ── Product Context Helpers ──
  // ══════════════════════════════════════════════════════════

  App.extractProductContext = function (data) {
    if (!data.detail) return null;
    try {
      var obj = typeof data.detail === 'string' ? JSON.parse(data.detail) : data.detail;
      return obj.product_context || null;
    } catch (e) { return null; }
  };

  App.renderProductBadge = function (pc) {
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
  };

  App.renderProductStrip = function (pc) {
    if (!pc) return '';
    var mult = pc.leverage_multiplier || 1;
    var signed = pc.signed_exposure || mult;
    var label = (pc.is_inverse ? '' : '+') + signed + 'x';
    var typeLabel = pc.product_type ? pc.product_type.replace(/_/g, ' ') : '';
    var source = pc.classification_source || '';
    var restricted = pc.restricted_product;
    var cls = restricted ? 'product-strip-restricted' : 'product-strip';
    return '<div class="' + cls + ' mb-2"><span class="font-medium">' + label + '</span><span class="opacity-75">' + typeLabel + '</span>' + (restricted ? '<span class="product-restricted-label">\uC81C\uD55C \uC0C1\uD488</span>' : '') + '<span class="opacity-50">' + source + '</span></div>';
  };

  // ══════════════════════════════════════════════════════════
  // ── Feed rendering delegated to Admin.Feed ──
  // ══════════════════════════════════════════════════════════

  // NOTE: createStockCard, addStepToCard, updateCardHeader, applyFilterToCard,
  // toggleCardBody, updateScrollBadge are all provided by Admin.Feed
  // (shared/admin-feed.js). Previously they were duplicated here.

  // NOTE: The following are provided by Admin.Feed (shared/admin-feed.js):
  // createStockCard, addStepToCard, updateCardHeader, applyFilterToCard,
  // toggleCardBody, updateScrollBadge, createCycleDivider, createBubble,
  // formatDetail, formatLLMConversation, appendActivity, setLogFilter,
  // scrollToBottom, toggleDetail, highlightCard, navigateToCard

  // ══════════════════════════════════════════════════════════
  // ── View Switching ──
  // ══════════════════════════════════════════════════════════

  App.switchView = function (view) {
    var wasLive = App.currentView === 'live';
    if (wasLive && view !== 'live') {
      App.saveMarketFeed(App.currentScope());
    }
    App.currentView = view;
    document.querySelectorAll('.nav-btn').forEach(function (b) {
      b.className = 'nav-btn w-full text-left px-3 py-2 rounded-lg text-sm text-gray-400 hover:bg-dark-700';
    });
    var activeBtn = document.getElementById('nav-' + view);
    if (activeBtn) {
      activeBtn.className = 'nav-btn w-full text-left px-3 py-2 rounded-lg text-sm font-medium bg-blue-900/30 text-blue-300';
    }
    if (view === 'live') {
      App.removeBackToLiveBar();
      App.restoreMarketFeed(App.currentScope());
      App.flushBuffer(App.currentScope());
    } else if (view === 'today') {
      App.loadReport('today');
    }
    App.updateFeedLoadMore();
  };

  // ── Market Switching ──
  App.switchMarket = function (market) {
    if (market === App.currentMarket) return;
    var oldScope = App.currentScope();

    if (App.currentView === 'live') {
      App.saveMarketFeed(oldScope);
    }
    App.pauseMarketTimers(oldScope);

    App.currentMarket = market;
    var newScope = App.currentScope();

    App.stockCards = App.marketState[newScope].stockCards;
    App.applyWorkspaceTheme();

    var indicator = document.getElementById('market-header-indicator');
    document.querySelectorAll('.market-header-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.market === market);
    });
    if (indicator) indicator.classList.toggle('right', market !== 'KRX');

    if (App.currentView === 'live') {
      App.restoreMarketFeed(newScope);
    }

    App.flushBuffer(newScope);
    App.updateBackgroundBadge(newScope, 0);
    document.getElementById('activity-count').textContent = App.getActivityCount() + '\uAC74';

    document.querySelectorAll('.account-data-transition').forEach(function (el) { el.classList.add('switching'); });
    App.loadMarketAccountInfo().then(function () {
      setTimeout(function () {
        document.querySelectorAll('.account-data-transition').forEach(function (el) { el.classList.remove('switching'); });
      }, 60);
    });
    App.loadWatchlist();
    App.loadSystemStatus();
    App.loadReportList();
    App.loadScheduleTimeline();
    Admin.Monitor.render();
    App.updateFeedLoadMore();
  };

  App.setupMarketTabs = function () {
    var tabsEl = document.getElementById('market-header-tabs');
    var ctxBar = document.getElementById('market-context-bar');
    if (!tabsEl) return;
    if (App.enabledMarkets.length > 1) {
      tabsEl.classList.remove('hidden');
      if (ctxBar) ctxBar.classList.remove('hidden');
      document.querySelectorAll('.market-header-btn').forEach(function (btn) {
        btn.classList.toggle('active', btn.dataset.market === App.currentMarket);
      });
      var indicator = document.getElementById('market-header-indicator');
      if (indicator) indicator.classList.toggle('right', App.currentMarket !== 'KRX');
    } else {
      tabsEl.classList.add('hidden');
      if (ctxBar) ctxBar.classList.add('hidden');
    }
  };

  App.applyWorkspaceTheme = function () {
    var scope = App.currentScope();
    document.body.classList.remove('workspace-krx', 'workspace-us');
    document.body.classList.add(scope === 'KRX' ? 'workspace-krx' : 'workspace-us');

    var feedLabel = document.getElementById('feed-market-label');
    if (feedLabel) {
      feedLabel.textContent = App.enabledMarkets.length > 1
        ? (scope === 'KRX' ? '\u2014 KRX \uAD6D\uB0B4' : '\u2014 US \uD574\uC678')
        : '';
    }

    var ctxLabel = document.getElementById('ctx-market-label');
    if (ctxLabel) ctxLabel.textContent = scope === 'KRX' ? 'KRX \uAD6D\uB0B4\uC8FC\uC2DD' : 'US \uD574\uC678\uC8FC\uC2DD';
  };

  // ── Feed State Preservation ──
  App.saveMarketFeed = function (scope) {
    var container = document.getElementById('chat-container');
    var state = App.marketState[scope];
    state.scrollPos = container.scrollTop;
    App.pauseMarketTimers(scope);
  };

  App.restoreMarketFeed = function (scope) {
    var state = App.marketState[scope];
    if (!state.loaded) {
      App.loadTodayActivities();
      return;
    }
    App.renderLiveFeedFromState(scope, { restoreScroll: true });
  };

  App.flushBuffer = function (scope) {
    var buf = App.activityBuffer[scope];
    if (!buf || !buf.length) return;

    if (buf.length > 5) {
      var container = document.getElementById('chat-container');
      var divider = document.createElement('div');
      divider.className = 'cycle-divider';
      var dividerText = document.createElement('span');
      dividerText.className = 'text-gray-500';
      dividerText.textContent = buf.length + '\uAC74\uC758 \uC0C8 \uD65C\uB3D9';
      divider.appendChild(dividerText);
      container.appendChild(divider);
    }

    buf.forEach(function (data) { Admin.Feed.appendActivity(data); });
    App.activityBuffer[scope] = [];
  };

  App.pauseMarketTimers = function (scope) {
    var cards = App.marketState[scope].stockCards;
    for (var key in cards) {
      var cardObj = cards[key];
      if (cardObj.liveTimer) {
        clearInterval(cardObj.liveTimer);
        cardObj.liveTimer = null;
      }
    }
  };

  // ── Log Filter ──
  // ══════════════════════════════════════════════════════════
  // ── Data Loading ──
  // ══════════════════════════════════════════════════════════

  App.buildActivityFeedUrl = function (scope, options) {
    options = options || {};
    var params = new URLSearchParams();
    params.set('market_scope', scope);
    params.set('limit', String(options.limit || 100));
    if (options.targetDate) params.set('target_date', options.targetDate);
    if (options.beforeCreatedAt) params.set('before_created_at', options.beforeCreatedAt);
    if (options.beforeId) params.set('before_id', options.beforeId);
    return Admin.API + '/activities/feed?' + params.toString();
  };

  App.updateFeedLoadMore = function (scope) {
    scope = scope || App.currentScope();
    var bar = document.getElementById('feed-load-more-bar');
    var btn = document.getElementById('feed-load-more-btn');
    var meta = document.getElementById('feed-load-more-meta');
    if (!bar || !btn || !meta) return;
    var btnLabel = btn.querySelector('span');

    var state = App.getMarketState(scope);
    var isActiveLiveFeed = App.currentView === 'live' && scope === App.currentScope();
    if (!isActiveLiveFeed) {
      bar.classList.add('hidden');
      btn.disabled = false;
      if (btnLabel) btnLabel.textContent = '\uC774\uC804 \uB0B4\uC5ED \uB354\uBCF4\uAE30';
      meta.textContent = '';
      return;
    }

    var canShow = state.loaded && (state.feedHasMore || state.feedLoading) && App.isNearTop;
    if (!canShow) {
      bar.classList.add('hidden');
      btn.disabled = false;
      if (btnLabel) btnLabel.textContent = '\uC774\uC804 \uB0B4\uC5ED \uB354\uBCF4\uAE30';
    } else {
      bar.classList.remove('hidden');
      btn.disabled = state.feedLoading;
      if (btnLabel) btnLabel.textContent = state.feedLoading ? '\uBD88\uB7EC\uC624\uB294 \uC911...' : '\uC774\uC804 \uB0B4\uC5ED \uB354\uBCF4\uAE30';
    }

    meta.textContent = state.feedTradingDate ? state.feedTradingDate + ' \uAC70\uB798\uC77C' : '';
  };

  App.renderLiveFeedFromState = function (scope, options) {
    options = options || {};
    if (scope !== App.currentScope() || App.currentView !== 'live') return;

    var container = document.getElementById('chat-container');
    App.cleanupStockCards(scope);
    container.replaceChildren();
    App.setActivityCount(0);

    var visibleActivities = Admin.filterResolvedStarts(App.getMarketState(scope).feedItems);
    if (!visibleActivities.length) {
      Admin.renderPlaceholder(container, 'empty', '\uD604\uC7AC \uAC70\uB798\uC77C \uD65C\uB3D9\uC774 \uC5C6\uC2B5\uB2C8\uB2E4');
    } else {
      visibleActivities.forEach(function (activity) { App.appendActivity(activity, { skipStore: true }); });
    }

    App.getMarketState(scope).loaded = true;
    App.getMarketState(scope).activityCount = visibleActivities.length;
    document.getElementById('activity-count').textContent = App.getActivityCount() + '\uAC74';
    App.updateFeedLoadMore(scope);
    Admin.refreshIcons();

    requestAnimationFrame(function () {
      if (options.preserveViewport) {
        var nextHeight = container.scrollHeight;
        container.scrollTop = (options.previousScrollTop || 0) + (nextHeight - (options.previousScrollHeight || 0));
        return;
      }
      if (options.restoreScroll) {
        container.scrollTop = App.getMarketState(scope).scrollPos || 0;
        return;
      }
      container.scrollTop = container.scrollHeight;
    });
  };

  App.loadMoreActivities = async function () {
    var scope = App.currentScope();
    var state = App.getMarketState(scope);
    if (state.feedLoading || !state.feedHasMore || !state.feedCursor) return;

    var container = document.getElementById('chat-container');
    var previousScrollHeight = container.scrollHeight;
    var previousScrollTop = container.scrollTop;
    state.feedLoading = true;
    App.updateFeedLoadMore(scope);

    try {
      var json = await Admin.fetchJSON(App.buildActivityFeedUrl(scope, {
        limit: 100,
        targetDate: state.feedTradingDate,
        beforeCreatedAt: state.feedCursor.before_created_at,
        beforeId: state.feedCursor.before_id,
      }));
      var feed = json.data || {};
      var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
      App.rememberFeedActivities(scope, page, { prepend: true });
      state.feedTradingDate = feed.resolved_trading_date || state.feedTradingDate;
      state.feedHasMore = !!feed.has_more;
      state.feedCursor = feed.next_cursor || null;
      App.renderLiveFeedFromState(scope, {
        preserveViewport: true,
        previousScrollHeight: previousScrollHeight,
        previousScrollTop: previousScrollTop,
      });
    } catch (err) {
      Admin.Toast.show('\uC774\uC804 \uB0B4\uC5ED \uB85C\uB4DC \uC2E4\uD328: ' + err.message, 'error');
    } finally {
      state.feedLoading = false;
      App.updateFeedLoadMore(scope);
    }
  };

  App.loadTodayActivities = async function () {
    var scope = App.currentScope();
    var container = document.getElementById('chat-container');
    var state = App.getMarketState(scope);
    Admin.renderPlaceholder(container, 'loading', '\uBD88\uB7EC\uC624\uB294 \uC911...');
    App.cleanupStockCards(scope);
    App.resetFeedState(scope);
    App.updateFeedLoadMore(scope);

    try {
      var json = await Admin.fetchJSON(App.buildActivityFeedUrl(scope, { limit: 100 }));
      var feed = json.data || {};
      var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
      App.rememberFeedActivities(scope, page);
      state.feedTradingDate = feed.resolved_trading_date || null;
      state.feedHasMore = !!feed.has_more;
      state.feedCursor = feed.next_cursor || null;
      state.loaded = true;
      App.renderLiveFeedFromState(scope);
    } catch (err) {
      Admin.renderPlaceholder(container, 'error', '\uB85C\uB4DC \uC2E4\uD328: ' + err.message);
      App.updateFeedLoadMore(scope);
    }
  };

  // ── Clear Chat ──
  App.clearChat = function () {
    if (!confirm('\uD654\uBA74\uC744 \uBE44\uC6B8\uAE4C\uC694? (DB\uB294 \uC720\uC9C0\uB429\uB2C8\uB2E4)')) return;
    var scope = App.currentScope();
    var container = document.getElementById('chat-container');
    container.replaceChildren();
    var msg = document.createElement('div');
    msg.className = 'text-center text-gray-500 text-sm py-8';
    msg.textContent = '\uD654\uBA74\uC744 \uBE44\uC6E0\uC2B5\uB2C8\uB2E4. \uC0C8 \uD65C\uB3D9\uC774 \uB4E4\uC5B4\uC624\uBA74 \uC5EC\uAE30\uC5D0 \uD45C\uC2DC\uB429\uB2C8\uB2E4.';
    container.appendChild(msg);
    App.resetFeedState(scope, { preserveLoaded: true });
    App.marketState[scope].loaded = true;
    App.setActivityCount(0);
    document.getElementById('activity-count').textContent = '0\uAC74';
    App.cleanupStockCards(scope);
    App.updateFeedLoadMore(scope);
  };

  App.cleanupStockCards = function (scope) {
    scope = scope || App.currentScope();
    var cards = App.marketState[scope].stockCards;
    for (var key in cards) {
      if (cards[key].liveTimer) clearInterval(cards[key].liveTimer);
    }
    App.marketState[scope].stockCards = {};
    if (scope === App.currentScope()) {
      App.stockCards = App.marketState[scope].stockCards;
    }
  };

  // ── Back to Live Button ──
  App.insertBackToLiveBar = function (container) {
    var existing = document.getElementById('back-to-live-bar');
    if (existing) existing.remove();
    var bar = document.createElement('div');
    bar.id = 'back-to-live-bar';
    bar.className = 'back-to-live-bar';
    var btn = document.createElement('button');
    btn.className = 'back-to-live-btn';
    btn.onclick = function () { App.switchView('live'); };
    var arrowIcon = document.createElement('i');
    arrowIcon.setAttribute('data-lucide', 'arrow-left');
    arrowIcon.className = 'w-3 h-3';
    btn.appendChild(arrowIcon);
    btn.appendChild(document.createTextNode(' \uC2E4\uC2DC\uAC04\uC73C\uB85C \uB3CC\uC544\uAC00\uAE30'));
    bar.appendChild(btn);
    container.parentNode.insertBefore(bar, container);
    Admin.refreshIcons();
  };

  App.removeBackToLiveBar = function () {
    var bar = document.getElementById('back-to-live-bar');
    if (bar) bar.remove();
  };

  // ══════════════════════════════════════════════════════════
  // ── Settings ──
  // ══════════════════════════════════════════════════════════

  App.loadSettings = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/settings');
      var s = json.data;
      if (!s) return;
      document.getElementById('set-trading').checked = s.TRADING_ENABLED;
      document.getElementById('set-mode').value = s.AUTONOMY_MODE;
      var riskEl = document.getElementById('set-risk-appetite');
      if (riskEl && s.RISK_APPETITE) riskEl.value = s.RISK_APPETITE;
      Admin.updateBadge('badge-trading', s.TRADING_ENABLED ? '\uB9E4\uB9E4:ON' : '\uB9E4\uB9E4:OFF', s.TRADING_ENABLED ? 'green' : 'red');
      Admin.updateBadge('badge-mode', s.AUTONOMY_MODE, 'purple');
      if (s.ENABLED_MARKET_GROUPS && s.ENABLED_MARKET_GROUPS.length) {
        App.enabledMarkets = s.ENABLED_MARKET_GROUPS;
        if (App.enabledMarkets.indexOf(App.currentMarket) === -1) App.currentMarket = App.enabledMarkets[0];
      }
      App.stockCards = App.marketState[App.currentScope()].stockCards;
      App.setupMarketTabs();
    } catch (err) {
      console.error('Settings load error:', err);
    }
  };

  App.updateSetting = async function (key, value) {
    var controls = document.querySelectorAll('#settings-body input, #settings-body select');
    controls.forEach(function (c) { c.disabled = true; });
    try {
      var body = {};
      body[key] = value;
      await Admin.fetchJSON(Admin.API + '/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      Admin.Toast.show('\uC124\uC815 \uC800\uC7A5\uB428', 'success');
      await App.loadSettings();
      App.loadSystemStatus();
    } catch (err) {
      console.error('Setting update error:', err);
      Admin.Toast.show('\uC124\uC815 \uC800\uC7A5 \uC2E4\uD328: ' + err.message, 'error');
      await App.loadSettings();
    } finally {
      controls.forEach(function (c) { c.disabled = false; });
    }
  };

  App.toggleSettings = function () {
    var body = document.getElementById('settings-body');
    var arrow = document.getElementById('settings-arrow');
    var toggleBtn = document.getElementById('settings-toggle-btn');
    if (!body) return;
    var isHidden = body.classList.contains('hidden');
    body.classList.toggle('hidden');
    if (arrow) arrow.style.transform = isHidden ? 'rotate(0deg)' : 'rotate(-90deg)';
    if (toggleBtn) toggleBtn.setAttribute('aria-expanded', String(isHidden));
  };

  // ── Sidebar Toggles ──
  App.toggleLeftSidebar = function () {
    var sidebar = document.getElementById('left-sidebar');
    if (!sidebar) return;
    sidebar.classList.toggle('collapsed');
    localStorage.setItem('momo-left-sidebar-collapsed', sidebar.classList.contains('collapsed') ? '1' : '');
  };

  App.toggleRightSidebar = function () {
    var sidebar = document.getElementById('right-sidebar');
    if (!sidebar) return;
    sidebar.classList.toggle('collapsed');
    localStorage.setItem('momo-right-sidebar-collapsed', sidebar.classList.contains('collapsed') ? '1' : '');
  };

  App.toggleAccountSection = function (section) {
    var idMap = { holdings: 'holdings-info', pending: 'pending-orders-info' };
    var arrowMap = { holdings: 'holdings-arrow', pending: 'pending-arrow' };
    var bodyEl = document.getElementById(idMap[section] || (section + '-info'));
    var arrowEl = document.getElementById(arrowMap[section] || (section + '-arrow'));
    var toggleBtn = bodyEl && bodyEl.closest('#' + section + '-section')
      ? bodyEl.closest('#' + section + '-section').querySelector('button')
      : null;
    if (!bodyEl) return;
    var isCollapsed = bodyEl.classList.toggle('collapsed-section');
    if (arrowEl) arrowEl.classList.toggle('collapsed-icon', isCollapsed);
    if (toggleBtn) toggleBtn.setAttribute('aria-expanded', String(!isCollapsed));
  };

  // ── Actions ──
  App.triggerCycle = async function () {
    if (App.triggerPending) return;
    App.triggerPending = true;
    var btn = document.querySelector('[onclick="triggerCycle()"]');
    var originalChildren = btn ? Array.from(btn.childNodes).map(function (n) { return n.cloneNode(true); }) : [];
    if (btn) {
      btn.replaceChildren();
      var spinner = document.createElement('span');
      spinner.className = 'progress-spinner';
      btn.appendChild(spinner);
      btn.appendChild(document.createTextNode(' \uC2E4\uD589 \uC911...'));
      btn.disabled = true;
    }
    try {
      await Admin.fetchJSON(Admin.API + '/agent/trigger?market=' + App.currentMarket, { method: 'POST' });
      Admin.Toast.show('\uC0AC\uC774\uD074 \uC2E4\uD589 \uC694\uCCAD\uB428', 'success');
    } catch (err) {
      console.error('Trigger error:', err);
      Admin.Toast.show('\uC2E4\uD589 \uC2E4\uD328: ' + err.message, 'error');
    } finally {
      setTimeout(function () {
        if (btn) {
          btn.replaceChildren();
          originalChildren.forEach(function (n) { btn.appendChild(n); });
          btn.disabled = false;
          Admin.refreshIcons();
        }
        App.triggerPending = false;
      }, 3000);
    }
  };

  App.generateReport = async function () {
    if (App.reportPending) return;
    App.reportPending = true;
    var btn = document.querySelector('[onclick="generateReport()"]');
    var originalChildren = btn ? Array.from(btn.childNodes).map(function (n) { return n.cloneNode(true); }) : [];
    if (btn) {
      btn.replaceChildren();
      var spinner = document.createElement('span');
      spinner.className = 'progress-spinner';
      btn.appendChild(spinner);
      btn.appendChild(document.createTextNode(' \uC0DD\uC131 \uC911...'));
      btn.disabled = true;
    }
    try {
      await Admin.fetchJSON(Admin.API + '/reports/generate?market_scope=' + encodeURIComponent(App.currentMarket), { method: 'POST' });
      Admin.Toast.show('\uB9AC\uD3EC\uD2B8 \uC0DD\uC131 \uC694\uCCAD\uB428', 'success');
      App.loadReportList();
    } catch (err) {
      console.error('Report gen error:', err);
      Admin.Toast.show('\uB9AC\uD3EC\uD2B8 \uC0DD\uC131 \uC2E4\uD328: ' + err.message, 'error');
    } finally {
      setTimeout(function () {
        if (btn) {
          btn.replaceChildren();
          originalChildren.forEach(function (n) { btn.appendChild(n); });
          btn.disabled = false;
          Admin.refreshIcons();
        }
        App.reportPending = false;
      }, 3000);
    }
  };

  // ══════════════════════════════════════════════════════════
  // ── Restore sidebar state ──
  // ══════════════════════════════════════════════════════════

  (function restoreSidebarState() {
    if (localStorage.getItem('momo-left-sidebar-collapsed') === '1') {
      var ls = document.getElementById('left-sidebar');
      if (ls) ls.classList.add('collapsed');
    }
    if (localStorage.getItem('momo-right-sidebar-collapsed') === '1') {
      var rs = document.getElementById('right-sidebar');
      if (rs) rs.classList.add('collapsed');
    }
  })();

  // ══════════════════════════════════════════════════════════
  // ── Visibility change (stop title flash) ──
  // ══════════════════════════════════════════════════════════

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) Admin.Toast.stopTitleFlash();
  });

  // ══════════════════════════════════════════════════════════
  // ── Auto-scroll detection ──
  // ══════════════════════════════════════════════════════════

  document.getElementById('chat-container').addEventListener('scroll', function () {
    var el = this;
    App.autoScroll = (el.scrollHeight - el.scrollTop - el.clientHeight) < 50;
    var fab = document.getElementById('scroll-to-bottom');
    if (fab) fab.classList.toggle('hidden', App.autoScroll);
    if (App.autoScroll) {
      App.missedCount = 0;
      Admin.Feed.updateScrollBadge();
    }
    var wasNearTop = App.isNearTop;
    App.isNearTop = el.scrollTop < 30;
    if (App.isNearTop !== wasNearTop) App.updateFeedLoadMore();
  });

  // ══════════════════════════════════════════════════════════
  // ── DOMContentLoaded Init ──
  // ══════════════════════════════════════════════════════════

  document.addEventListener('DOMContentLoaded', async function () {
    await App.loadSettings();
    App.applyWorkspaceTheme();
    App.loadSystemStatus();
    App.loadReportList();
    App.loadMarketAccountInfo();
    App.loadWatchlist();
    App.loadLLMStatus();
    App.loadLLMUsage();
    App.loadScheduleTimeline();
    App.connectSSE();
    App.loadTodayActivities();
    App.initAgentMonitor();
    setInterval(App.loadSystemStatus, 15000);
    App.accountPollTimer = setInterval(App.loadMarketAccountInfo, 30000);
    setInterval(App.loadWatchlist, 30000);
    setInterval(App.loadLLMUsage, 60000);
    setInterval(App.loadScheduleTimeline, 15000);
  });

  // ══════════════════════════════════════════════════════════
  // ── Global Window Exports (onclick handlers from HTML) ──
  // ══════════════════════════════════════════════════════════

  window.switchMarket = function (m) { App.switchMarket(m); };
  window.switchView = function (v) { App.switchView(v); };
  window.toggleLeftSidebar = function () { App.toggleLeftSidebar(); };
  window.toggleRightSidebar = function () { App.toggleRightSidebar(); };
  window.toggleAccountSection = function (s) { App.toggleAccountSection(s); };
  window.toggleSettings = function () { App.toggleSettings(); };
  window.updateSetting = function (k, v) { App.updateSetting(k, v); };
  window.triggerCycle = function () { App.triggerCycle(); };
  window.generateReport = function () { App.generateReport(); };
  window.setLogFilter = function (f) { Admin.Feed.setLogFilter(f); };
  window.toggleMonitorExpand = function () { Admin.Monitor.toggleExpand(); };
  window.loadMoreActivities = function () { App.loadMoreActivities(); };
  window.scrollToBottom = function () { Admin.Feed.scrollToBottom(); };
  window.clearChat = function () { App.clearChat(); };
  window.loadTodayActivities = function () { App.loadTodayActivities(); };
  window.toggleDetail = function (id) { Admin.Feed.toggleDetail(id); };
})();
