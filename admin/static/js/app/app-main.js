// ── app-main.js — Entry point: init, view switching, window.* exports (ES module) ──

import {
  state, currentScope, API,
  getActivityCount, setActivityCount,
  getMarketState,
  rememberFeedActivities, resetFeedState,
} from './app-state.js';
import { connectSSE, initAgentMonitor, updateBackgroundBadge } from './app-sse.js';
import { loadMarketAccountInfo, loadWatchlist } from './app-account.js';
import { loadLLMStatus, loadLLMUsage } from './app-llm.js';
import { loadScheduleTimeline, loadSystemStatus } from './app-schedule.js';
import { loadReportList, loadReport, createReportCard, renderReportComparison } from './app-reports.js';
import * as Toast from '../shared/admin-toast.js';
import * as Monitor from '../shared/admin-monitor.js';
import * as Feed from '../shared/admin-feed.js';
import { refreshIcons, filterResolvedStarts, renderPlaceholder, updateBadge, fetchJSON } from '../shared/admin-core.js';

// ── Wire up lazy callbacks for cross-module references ──
// (app-sse.js needs these but they are defined in app-account.js / this file)
state._loadMarketAccountInfo = loadMarketAccountInfo;
state._loadWatchlist = loadWatchlist;
state._insertBackToLiveBar = insertBackToLiveBar;
state._cleanupStockCards = cleanupStockCards;

// ══════════════════════════════════════════════════════════
// ── View Switching ──
// ══════════════════════════════════════════════════════════

export function switchView(view) {
  var wasLive = state.currentView === 'live';
  if (wasLive && view !== 'live') {
    saveMarketFeed(currentScope());
  }
  state.currentView = view;
  document.querySelectorAll('.nav-btn').forEach(function (b) {
    b.className = 'nav-btn w-full text-left px-3 py-2 rounded-lg text-sm text-gray-400 hover:bg-dark-700';
  });
  var activeBtn = document.getElementById('nav-' + view);
  if (activeBtn) {
    activeBtn.className = 'nav-btn w-full text-left px-3 py-2 rounded-lg text-sm font-medium bg-blue-900/30 text-blue-300';
  }
  if (view === 'live') {
    removeBackToLiveBar();
    restoreMarketFeed(currentScope());
    flushBuffer(currentScope());
  } else if (view === 'today') {
    loadReport('today');
  }
  updateFeedLoadMore();
}

// ── Market Switching ──
export function switchMarket(market) {
  if (market === state.currentMarket) return;
  var oldScope = currentScope();

  if (state.currentView === 'live') {
    saveMarketFeed(oldScope);
  }
  pauseMarketTimers(oldScope);

  state.currentMarket = market;
  var newScope = currentScope();

  state.stockCards = state.marketState[newScope].stockCards;
  applyWorkspaceTheme();

  var indicator = document.getElementById('market-header-indicator');
  document.querySelectorAll('.market-header-btn').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.market === market);
  });
  if (indicator) indicator.classList.toggle('right', market !== 'KRX');

  if (state.currentView === 'live') {
    restoreMarketFeed(newScope);
  }

  flushBuffer(newScope);
  updateBackgroundBadge(newScope, 0);
  document.getElementById('activity-count').textContent = getActivityCount() + '\uAC74';

  document.querySelectorAll('.account-data-transition').forEach(function (el) { el.classList.add('switching'); });
  loadMarketAccountInfo().then(function () {
    setTimeout(function () {
      document.querySelectorAll('.account-data-transition').forEach(function (el) { el.classList.remove('switching'); });
    }, 60);
  });
  loadWatchlist();
  loadSystemStatus();
  loadReportList();
  loadScheduleTimeline();
  Monitor.render();
  updateFeedLoadMore();
}

export function setupMarketTabs() {
  var tabsEl = document.getElementById('market-header-tabs');
  var ctxBar = document.getElementById('market-context-bar');
  if (!tabsEl) return;
  if (state.enabledMarkets.length > 1) {
    tabsEl.classList.remove('hidden');
    if (ctxBar) ctxBar.classList.remove('hidden');
    document.querySelectorAll('.market-header-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.market === state.currentMarket);
    });
    var indicator = document.getElementById('market-header-indicator');
    if (indicator) indicator.classList.toggle('right', state.currentMarket !== 'KRX');
  } else {
    tabsEl.classList.add('hidden');
    if (ctxBar) ctxBar.classList.add('hidden');
  }
}

export function applyWorkspaceTheme() {
  var scope = currentScope();
  document.body.classList.remove('workspace-krx', 'workspace-us');
  document.body.classList.add(scope === 'KRX' ? 'workspace-krx' : 'workspace-us');

  var feedLabel = document.getElementById('feed-market-label');
  if (feedLabel) {
    feedLabel.textContent = state.enabledMarkets.length > 1
      ? (scope === 'KRX' ? '\u2014 KRX \uAD6D\uB0B4' : '\u2014 US \uD574\uC678')
      : '';
  }

  var ctxLabel = document.getElementById('ctx-market-label');
  if (ctxLabel) ctxLabel.textContent = scope === 'KRX' ? 'KRX \uAD6D\uB0B4\uC8FC\uC2DD' : 'US \uD574\uC678\uC8FC\uC2DD';
}

// ── Feed State Preservation ──
export function saveMarketFeed(scope) {
  var container = document.getElementById('chat-container');
  var ms = state.marketState[scope];
  ms.scrollPos = container.scrollTop;
  pauseMarketTimers(scope);
}

export function restoreMarketFeed(scope) {
  var ms = state.marketState[scope];
  if (!ms.loaded) {
    loadTodayActivities();
    return;
  }
  renderLiveFeedFromState(scope, { restoreScroll: true });
}

export function flushBuffer(scope) {
  var buf = state.activityBuffer[scope];
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

  buf.forEach(function (data) { Feed.appendActivity(data); });
  state.activityBuffer[scope] = [];
}

export function pauseMarketTimers(scope) {
  var cards = state.marketState[scope].stockCards;
  for (var key in cards) {
    var cardObj = cards[key];
    if (cardObj.liveTimer) {
      clearInterval(cardObj.liveTimer);
      cardObj.liveTimer = null;
    }
  }
}

// ── Data Loading ──
// ══════════════════════════════════════════════════════════

export function buildActivityFeedUrl(scope, options) {
  options = options || {};
  var params = new URLSearchParams();
  params.set('market_scope', scope);
  params.set('limit', String(options.limit || 100));
  if (options.targetDate) params.set('target_date', options.targetDate);
  if (options.beforeCreatedAt) params.set('before_created_at', options.beforeCreatedAt);
  if (options.beforeId) params.set('before_id', options.beforeId);
  return API + '/activities/feed?' + params.toString();
}

export function updateFeedLoadMore(scope) {
  scope = scope || currentScope();
  var bar = document.getElementById('feed-load-more-bar');
  var btn = document.getElementById('feed-load-more-btn');
  var meta = document.getElementById('feed-load-more-meta');
  if (!bar || !btn || !meta) return;
  var btnLabel = btn.querySelector('span');

  var ms = getMarketState(scope);
  var isActiveLiveFeed = state.currentView === 'live' && scope === currentScope();
  if (!isActiveLiveFeed) {
    bar.classList.add('hidden');
    btn.disabled = false;
    if (btnLabel) btnLabel.textContent = '\uC774\uC804 \uB0B4\uC5ED \uB354\uBCF4\uAE30';
    meta.textContent = '';
    return;
  }

  var canShow = ms.loaded && (ms.feedHasMore || ms.feedLoading) && state.isNearTop;
  if (!canShow) {
    bar.classList.add('hidden');
    btn.disabled = false;
    if (btnLabel) btnLabel.textContent = '\uC774\uC804 \uB0B4\uC5ED \uB354\uBCF4\uAE30';
  } else {
    bar.classList.remove('hidden');
    btn.disabled = ms.feedLoading;
    if (btnLabel) btnLabel.textContent = ms.feedLoading ? '\uBD88\uB7EC\uC624\uB294 \uC911...' : '\uC774\uC804 \uB0B4\uC5ED \uB354\uBCF4\uAE30';
  }

  meta.textContent = ms.feedTradingDate ? ms.feedTradingDate + ' \uAC70\uB798\uC77C' : '';
}

export function renderLiveFeedFromState(scope, options) {
  options = options || {};
  if (scope !== currentScope() || state.currentView !== 'live') return;

  var container = document.getElementById('chat-container');
  cleanupStockCards(scope);
  container.replaceChildren();
  setActivityCount(0);

  var visibleActivities = filterResolvedStarts(getMarketState(scope).feedItems);
  if (!visibleActivities.length) {
    renderPlaceholder(container, 'empty', '\uD604\uC7AC \uAC70\uB798\uC77C \uD65C\uB3D9\uC774 \uC5C6\uC2B5\uB2C8\uB2E4');
  } else {
    visibleActivities.forEach(function (activity) { Feed.appendActivity(activity, { skipStore: true }); });
  }

  getMarketState(scope).loaded = true;
  getMarketState(scope).activityCount = visibleActivities.length;
  document.getElementById('activity-count').textContent = getActivityCount() + '\uAC74';
  updateFeedLoadMore(scope);
  refreshIcons();

  requestAnimationFrame(function () {
    if (options.preserveViewport) {
      var nextHeight = container.scrollHeight;
      container.scrollTop = (options.previousScrollTop || 0) + (nextHeight - (options.previousScrollHeight || 0));
      return;
    }
    if (options.restoreScroll) {
      container.scrollTop = getMarketState(scope).scrollPos || 0;
      return;
    }
    container.scrollTop = container.scrollHeight;
  });
}

export async function loadMoreActivities() {
  var scope = currentScope();
  var ms = getMarketState(scope);
  if (ms.feedLoading || !ms.feedHasMore || !ms.feedCursor) return;

  var container = document.getElementById('chat-container');
  var previousScrollHeight = container.scrollHeight;
  var previousScrollTop = container.scrollTop;
  ms.feedLoading = true;
  updateFeedLoadMore(scope);

  try {
    var json = await fetchJSON(buildActivityFeedUrl(scope, {
      limit: 100,
      targetDate: ms.feedTradingDate,
      beforeCreatedAt: ms.feedCursor.before_created_at,
      beforeId: ms.feedCursor.before_id,
    }));
    var feed = json.data || {};
    var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
    rememberFeedActivities(scope, page, { prepend: true });
    ms.feedTradingDate = feed.resolved_trading_date || ms.feedTradingDate;
    ms.feedHasMore = !!feed.has_more;
    ms.feedCursor = feed.next_cursor || null;
    renderLiveFeedFromState(scope, {
      preserveViewport: true,
      previousScrollHeight: previousScrollHeight,
      previousScrollTop: previousScrollTop,
    });
  } catch (err) {
    Toast.show('\uC774\uC804 \uB0B4\uC5ED \uB85C\uB4DC \uC2E4\uD328: ' + err.message, 'error');
  } finally {
    ms.feedLoading = false;
    updateFeedLoadMore(scope);
  }
}

export async function loadTodayActivities() {
  var scope = currentScope();
  var container = document.getElementById('chat-container');
  var ms = getMarketState(scope);
  renderPlaceholder(container, 'loading', '\uBD88\uB7EC\uC624\uB294 \uC911...');
  cleanupStockCards(scope);
  resetFeedState(scope);
  updateFeedLoadMore(scope);

  try {
    var json = await fetchJSON(buildActivityFeedUrl(scope, { limit: 100 }));
    var feed = json.data || {};
    var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
    rememberFeedActivities(scope, page);
    ms.feedTradingDate = feed.resolved_trading_date || null;
    ms.feedHasMore = !!feed.has_more;
    ms.feedCursor = feed.next_cursor || null;
    ms.loaded = true;
    renderLiveFeedFromState(scope);
  } catch (err) {
    renderPlaceholder(container, 'error', '\uB85C\uB4DC \uC2E4\uD328: ' + err.message);
    updateFeedLoadMore(scope);
  }
}

// ── Clear Chat ──
export function clearChat() {
  if (!confirm('\uD654\uBA74\uC744 \uBE44\uC6B8\uAE4C\uC694? (DB\uB294 \uC720\uC9C0\uB429\uB2C8\uB2E4)')) return;
  var scope = currentScope();
  var container = document.getElementById('chat-container');
  container.replaceChildren();
  var msg = document.createElement('div');
  msg.className = 'text-center text-gray-500 text-sm py-8';
  msg.textContent = '\uD654\uBA74\uC744 \uBE44\uC6E0\uC2B5\uB2C8\uB2E4. \uC0C8 \uD65C\uB3D9\uC774 \uB4E4\uC5B4\uC624\uBA74 \uC5EC\uAE30\uC5D0 \uD45C\uC2DC\uB429\uB2C8\uB2E4.';
  container.appendChild(msg);
  resetFeedState(scope, { preserveLoaded: true });
  state.marketState[scope].loaded = true;
  setActivityCount(0);
  document.getElementById('activity-count').textContent = '0\uAC74';
  cleanupStockCards(scope);
  updateFeedLoadMore(scope);
}

export function cleanupStockCards(scope) {
  scope = scope || currentScope();
  var cards = state.marketState[scope].stockCards;
  for (var key in cards) {
    if (cards[key].liveTimer) clearInterval(cards[key].liveTimer);
  }
  state.marketState[scope].stockCards = {};
  if (scope === currentScope()) {
    state.stockCards = state.marketState[scope].stockCards;
  }
}

// ── Back to Live Button ──
export function insertBackToLiveBar(container) {
  var existing = document.getElementById('back-to-live-bar');
  if (existing) existing.remove();
  var bar = document.createElement('div');
  bar.id = 'back-to-live-bar';
  bar.className = 'back-to-live-bar';
  var btn = document.createElement('button');
  btn.className = 'back-to-live-btn';
  btn.onclick = function () { switchView('live'); };
  var arrowIcon = document.createElement('i');
  arrowIcon.setAttribute('data-lucide', 'arrow-left');
  arrowIcon.className = 'w-3 h-3';
  btn.appendChild(arrowIcon);
  btn.appendChild(document.createTextNode(' \uC2E4\uC2DC\uAC04\uC73C\uB85C \uB3CC\uC544\uAC00\uAE30'));
  bar.appendChild(btn);
  container.parentNode.insertBefore(bar, container);
  refreshIcons();
}

export function removeBackToLiveBar() {
  var bar = document.getElementById('back-to-live-bar');
  if (bar) bar.remove();
}

// ══════════════════════════════════════════════════════════
// ── Settings ──
// ══════════════════════════════════════════════════════════

export async function loadSettings() {
  try {
    var json = await fetchJSON(API + '/settings');
    var s = json.data;
    if (!s) return;
    document.getElementById('set-trading').checked = s.TRADING_ENABLED;
    document.getElementById('set-mode').value = s.AUTONOMY_MODE;
    var riskEl = document.getElementById('set-risk-appetite');
    if (riskEl && s.RISK_APPETITE) riskEl.value = s.RISK_APPETITE;
    updateBadge('badge-trading', s.TRADING_ENABLED ? '\uB9E4\uB9E4:ON' : '\uB9E4\uB9E4:OFF', s.TRADING_ENABLED ? 'green' : 'red');
    updateBadge('badge-mode', s.AUTONOMY_MODE, 'purple');
    if (s.ENABLED_MARKET_GROUPS && s.ENABLED_MARKET_GROUPS.length) {
      state.enabledMarkets = s.ENABLED_MARKET_GROUPS;
      if (state.enabledMarkets.indexOf(state.currentMarket) === -1) state.currentMarket = state.enabledMarkets[0];
    }
    state.stockCards = state.marketState[currentScope()].stockCards;
    setupMarketTabs();
  } catch (err) {
    console.error('Settings load error:', err);
  }
}

export async function updateSetting(key, value) {
  var controls = document.querySelectorAll('#settings-body input, #settings-body select');
  controls.forEach(function (c) { c.disabled = true; });
  try {
    var body = {};
    body[key] = value;
    await fetchJSON(API + '/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    Toast.show('\uC124\uC815 \uC800\uC7A5\uB428', 'success');
    await loadSettings();
    loadSystemStatus();
  } catch (err) {
    console.error('Setting update error:', err);
    Toast.show('\uC124\uC815 \uC800\uC7A5 \uC2E4\uD328: ' + err.message, 'error');
    await loadSettings();
  } finally {
    controls.forEach(function (c) { c.disabled = false; });
  }
}

export function toggleSettings() {
  var body = document.getElementById('settings-body');
  var arrow = document.getElementById('settings-arrow');
  var toggleBtn = document.getElementById('settings-toggle-btn');
  if (!body) return;
  var isHidden = body.classList.contains('hidden');
  body.classList.toggle('hidden');
  if (arrow) arrow.style.transform = isHidden ? 'rotate(0deg)' : 'rotate(-90deg)';
  if (toggleBtn) toggleBtn.setAttribute('aria-expanded', String(isHidden));
}

// ── Sidebar Toggles ──
export function toggleLeftSidebar() {
  var sidebar = document.getElementById('left-sidebar');
  if (!sidebar) return;
  sidebar.classList.toggle('collapsed');
  localStorage.setItem('momo-left-sidebar-collapsed', sidebar.classList.contains('collapsed') ? '1' : '');
}

export function toggleRightSidebar() {
  var sidebar = document.getElementById('right-sidebar');
  if (!sidebar) return;
  sidebar.classList.toggle('collapsed');
  localStorage.setItem('momo-right-sidebar-collapsed', sidebar.classList.contains('collapsed') ? '1' : '');
}

export function toggleAccountSection(section) {
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
}

// ── Actions ──
export async function triggerCycle() {
  if (state.triggerPending) return;
  state.triggerPending = true;
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
    await fetchJSON(API + '/agent/trigger?market=' + state.currentMarket, { method: 'POST' });
    Toast.show('\uC0AC\uC774\uD074 \uC2E4\uD589 \uC694\uCCAD\uB428', 'success');
  } catch (err) {
    console.error('Trigger error:', err);
    Toast.show('\uC2E4\uD589 \uC2E4\uD328: ' + err.message, 'error');
  } finally {
    setTimeout(function () {
      if (btn) {
        btn.replaceChildren();
        originalChildren.forEach(function (n) { btn.appendChild(n); });
        btn.disabled = false;
        refreshIcons();
      }
      state.triggerPending = false;
    }, 3000);
  }
}

export async function generateReport() {
  if (state.reportPending) return;
  state.reportPending = true;
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
    var json = await fetchJSON(
      API + '/reports/generate?market_scope=' + encodeURIComponent(state.currentMarket) + '&refresh=true',
      { method: 'POST' },
    );
    var result = json.data || json;

    if (result.refreshed) {
      // Comparison response — show side-by-side UI
      var container = document.getElementById('chat-container');
      container.replaceChildren();
      container.appendChild(renderReportComparison(result));
      if (state._insertBackToLiveBar) state._insertBackToLiveBar(container);
      state.currentView = 'report';
      refreshIcons();
    } else {
      // New report, no existing — display normally
      var report = result.report || result;
      if (report && report.report_date) {
        var container2 = document.getElementById('chat-container');
        container2.replaceChildren();
        container2.appendChild(createReportCard(report));
        if (state._insertBackToLiveBar) state._insertBackToLiveBar(container2);
        state.currentView = 'report';
        refreshIcons();
      }
      Toast.show('\uB9AC\uD3EC\uD2B8 \uC0DD\uC131 \uC694\uCCAD\uB428', 'success');
    }
    loadReportList();
  } catch (err) {
    console.error('Report gen error:', err);
    Toast.show('\uB9AC\uD3EC\uD2B8 \uC0DD\uC131 \uC2E4\uD328: ' + err.message, 'error');
  } finally {
    setTimeout(function () {
      if (btn) {
        btn.replaceChildren();
        originalChildren.forEach(function (n) { btn.appendChild(n); });
        btn.disabled = false;
        refreshIcons();
      }
      state.reportPending = false;
    }, 3000);
  }
}

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
  if (!document.hidden) Toast.stopTitleFlash();
});

// ══════════════════════════════════════════════════════════
// ── Auto-scroll detection ──
// ══════════════════════════════════════════════════════════

document.getElementById('chat-container').addEventListener('scroll', function () {
  var el = this;
  state.autoScroll = (el.scrollHeight - el.scrollTop - el.clientHeight) < 50;
  var fab = document.getElementById('scroll-to-bottom');
  if (fab) fab.classList.toggle('hidden', state.autoScroll);
  if (state.autoScroll) {
    state.missedCount = 0;
    Feed.updateScrollBadge();
  }
  var wasNearTop = state.isNearTop;
  state.isNearTop = el.scrollTop < 30;
  if (state.isNearTop !== wasNearTop) updateFeedLoadMore();
});

// ══════════════════════════════════════════════════════════
// ── DOMContentLoaded Init ──
// ══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', async function () {
  await loadSettings();
  applyWorkspaceTheme();
  loadSystemStatus();
  loadReportList();
  loadMarketAccountInfo();
  loadWatchlist();
  loadLLMStatus();
  loadLLMUsage();
  loadScheduleTimeline();
  connectSSE();
  loadTodayActivities();
  initAgentMonitor();
  setInterval(loadSystemStatus, 15000);
  state.accountPollTimer = setInterval(loadMarketAccountInfo, 30000);
  setInterval(loadWatchlist, 30000);
  setInterval(loadLLMUsage, 60000);
  setInterval(loadScheduleTimeline, 15000);
});

// ══════════════════════════════════════════════════════════
// ── Global Window Exports (onclick handlers from HTML) ──
// ══════════════════════════════════════════════════════════

window.switchMarket = function (m) { switchMarket(m); };
window.switchView = function (v) { switchView(v); };
window.toggleLeftSidebar = function () { toggleLeftSidebar(); };
window.toggleRightSidebar = function () { toggleRightSidebar(); };
window.toggleAccountSection = function (s) { toggleAccountSection(s); };
window.toggleSettings = function () { toggleSettings(); };
window.updateSetting = function (k, v) { updateSetting(k, v); };
window.triggerCycle = function () { triggerCycle(); };
window.generateReport = function () { generateReport(); };
window.setLogFilter = function (f) { Feed.setLogFilter(f); };
window.toggleMonitorExpand = function () { Monitor.toggleExpand(); };
window.loadMoreActivities = function () { loadMoreActivities(); };
window.scrollToBottom = function () { Feed.scrollToBottom(); };
window.clearChat = function () { clearChat(); };
window.loadTodayActivities = function () { loadTodayActivities(); };
window.toggleDetail = function (id) { Feed.toggleDetail(id); };
