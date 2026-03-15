// ── coin-main.js — Entry point: init, view switching, window.* exports ──
//
// Security note: all dynamic content is escaped via escapeHtml()
// before DOM insertion. This mirrors the original coin-app.js exactly.

import {
  state, API, getStockCards, getActivityCount, setActivityCount,
  rememberFeedActivities, resetFeedState,
} from './coin-state.js';
import {
  formatDuration, formatDateTime,
  truncateText, setStatusText, setTextContent,
} from './coin-utils.js';
import { connectSSE, registerSSEDeps } from './coin-sse.js';
import { loadBalance } from './coin-account.js';
import { loadRecommendations, approveRecommendation, rejectRecommendation, updateRecCountdowns } from './coin-recommendations.js';
import { loadSystemStatus, loadAgentState, renderScanScheduleSummary, loadWatchlist, registerSystemDeps } from './coin-system.js';
import { loadLatestReport, loadReportList, loadCurrentReportView, loadSettings, updateSetting, registerReportDeps, registerSettingsDeps } from './coin-reports.js';
import { triggerScan, generateManualReport, renderActionButtons, registerActionDeps } from './coin-actions.js';
import * as Toast from '../shared/admin-toast.js';
import * as Monitor from '../shared/admin-monitor.js';
import * as Feed from '../shared/admin-feed.js';
import { refreshIcons, fetchJSON, filterResolvedStarts, renderPlaceholder } from '../shared/admin-core.js';

// ── Wire up late-bound cross-module dependencies ──

registerSSEDeps({
  loadAgentState: loadAgentState,
  loadWatchlist: loadWatchlist,
  loadLatestReport: loadLatestReport,
  loadReportList: loadReportList,
  loadSystemStatus: loadSystemStatus,
  scheduleAccountOverviewRefresh: scheduleAccountOverviewRefresh,
});

registerSystemDeps({
  updateScanTimeline: updateScanTimeline,
  renderScanScheduleSummary: renderScanScheduleSummary,
  renderActionButtons: renderActionButtons,
});

registerReportDeps({
  cleanupStockCards: cleanupStockCards,
  updateNavigationState: updateNavigationState,
});

registerSettingsDeps({
  loadSystemStatus: loadSystemStatus,
});

registerActionDeps({
  loadAgentState: loadAgentState,
  loadSystemStatus: loadSystemStatus,
  updateScanTimeline: updateScanTimeline,
  loadLatestReport: loadLatestReport,
  loadReportList: loadReportList,
  loadCurrentReportView: loadCurrentReportView,
});

// ── View switching ──

export function switchView(view) {
  if (view === 'live') {
    state.currentView = 'live';
    state.currentReportSelection = { mode: null, id: null };
    updateNavigationState();
    updateMainPanelChrome();
    if (state.feedState.loaded) {
      renderLiveFeedFromState({ restoreScroll: true });
    } else {
      loadTodayActivities();
    }
    return;
  }

  state.currentView = 'report';
  state.currentReportSelection = {
    mode: view === 'latest-report' ? 'latest' : state.currentReportSelection.mode,
    id: view === 'latest-report' ? null : state.currentReportSelection.id,
  };
  updateNavigationState();
  updateMainPanelChrome();
  loadCurrentReportView();
}

export function switchToReport(reportId) {
  state.currentView = 'report';
  state.currentReportSelection = { mode: 'id', id: reportId };
  updateNavigationState();
  updateMainPanelChrome();
  loadCurrentReportView();
}

export function handleMainClear() {
  if (state.currentView === 'live') {
    clearChat();
    return;
  }
  switchView('live');
}

export function refreshCurrentView() {
  if (state.currentView === 'live') {
    loadTodayActivities();
    return;
  }
  loadCurrentReportView({ forceLatest: state.currentReportSelection.mode === 'latest' });
}

// ── Log filter ──

export function setLogFilter(filter) {
  if (state.currentLogFilter === filter) return;
  state.currentLogFilter = filter;
  document.querySelectorAll('.log-filter-btn').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });
  Object.values(getStockCards()).forEach(function (card) {
    applyFilterToCard(card);
  });
  document.querySelectorAll('.chat-bubble').forEach(function (el) {
    if (filter === 'SIGNAL') {
      el.style.display = (el.textContent.indexOf('매수') !== -1 || el.textContent.indexOf('매도') !== -1 || el.textContent.indexOf('BUY') !== -1 || el.textContent.indexOf('SELL') !== -1)
        ? ''
        : 'none';
    } else {
      el.style.display = '';
    }
  });
  if (state.autoScroll) {
    var container = document.getElementById('chat-container');
    if (container) container.scrollTop = container.scrollHeight;
  }
}

function applyFilterToCard(card) {
  if (state.currentLogFilter === 'ALL') {
    card.element.style.display = '';
    return;
  }
  card.element.style.display = ['buy', 'sell', 'hold'].indexOf(card.outcome) !== -1 ? '' : 'none';
}

// ── Feed management ──

function clearChat() {
  if (!confirm('화면을 비울까요? (DB는 유지됩니다)')) return;
  var container = document.getElementById('chat-container');
  if (!container) return;
  container.textContent = '';
  var msg = document.createElement('div');
  msg.className = 'text-center text-gray-500 text-sm py-8';
  msg.textContent = '화면을 비웠습니다. 새 활동이 들어오면 여기에 표시됩니다.';
  container.appendChild(msg);
  resetFeedState({ preserveLoaded: true });
  cleanupStockCards();
  state.autoScroll = true;
  state.missedCount = 0;
  state.feedState.loaded = true;
  setActivityCount(0);
  setTextContent('activity-count', '0건');
  updateFeedLoadMore();
  updateScrollFab();
  setStatusText('활동 피드를 비웠습니다', '#9ca3af');
}

// ── Feed data loading ──

async function loadTodayActivities() {
  var container = document.getElementById('chat-container');
  if (!container) return;

  state.autoScroll = true;
  state.missedCount = 0;
  renderPlaceholder(container, 'loading', '불러오는 중...');
  cleanupStockCards();
  resetFeedState();
  updateFeedLoadMore();
  updateScrollFab();

  try {
    var json = await fetchJSON(buildActivityFeedUrl({ limit: 100 }));
    var feed = json.data || {};
    var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
    rememberFeedActivities(page);
    state.feedState.feedTradingDate = feed.resolved_trading_date || null;
    state.feedState.feedHasMore = !!feed.has_more;
    state.feedState.feedCursor = feed.next_cursor || null;
    state.feedState.loaded = true;
    renderLiveFeedFromState();
    setStatusText('현재 거래일 활동을 불러왔습니다', '#9ca3af');
  } catch (err) {
    renderPlaceholder(container, 'error', '로드 실패: ' + err.message);
    updateFeedLoadMore();
    setStatusText('활동 피드 로드 실패 · ' + truncateText(err.message, 48), '#f87171');
  }
}

async function loadMoreActivities() {
  if (state.feedState.feedLoading || !state.feedState.feedHasMore || !state.feedState.feedCursor) return;

  var container = document.getElementById('chat-container');
  if (!container) return;

  var previousScrollHeight = container.scrollHeight;
  var previousScrollTop = container.scrollTop;
  state.feedState.feedLoading = true;
  updateFeedLoadMore();

  try {
    var json = await fetchJSON(buildActivityFeedUrl({
      limit: 100,
      targetDate: state.feedState.feedTradingDate,
      beforeCreatedAt: state.feedState.feedCursor.before_created_at,
      beforeId: state.feedState.feedCursor.before_id,
    }));
    var feed = json.data || {};
    var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
    rememberFeedActivities(page, { prepend: true });
    state.feedState.feedTradingDate = feed.resolved_trading_date || state.feedState.feedTradingDate;
    state.feedState.feedHasMore = !!feed.has_more;
    state.feedState.feedCursor = feed.next_cursor || null;
    renderLiveFeedFromState({
      preserveViewport: true,
      previousScrollHeight: previousScrollHeight,
      previousScrollTop: previousScrollTop,
    });
  } catch (err) {
    Toast.show('이전 내역 로드 실패: ' + err.message, 'error');
  } finally {
    state.feedState.feedLoading = false;
    updateFeedLoadMore();
  }
}

// ── Feed URL builder ──

function buildActivityFeedUrl(options) {
  if (!options) options = {};
  var params = new URLSearchParams();
  params.set('limit', String(options.limit || 100));
  if (options.targetDate) params.set('target_date', options.targetDate);
  if (options.beforeCreatedAt) params.set('before_created_at', options.beforeCreatedAt);
  if (options.beforeId) params.set('before_id', options.beforeId);
  return API + '/activities/feed?' + params.toString();
}

// ── Feed load more bar ──

function updateFeedLoadMore() {
  var bar = document.getElementById('feed-load-more-bar');
  var btn = document.getElementById('feed-load-more-btn');
  var meta = document.getElementById('feed-load-more-meta');
  if (!bar || !btn || !meta) return;

  var label = btn.querySelector('span');
  if (state.currentView !== 'live') {
    bar.classList.add('hidden');
    btn.disabled = false;
    if (label) label.textContent = '이전 내역 더보기';
    meta.textContent = '';
    return;
  }

  var canShow = state.feedState.loaded && (state.feedState.feedHasMore || state.feedState.feedLoading) && state.isNearTop;
  if (!canShow) {
    bar.classList.add('hidden');
    btn.disabled = false;
    if (label) label.textContent = '이전 내역 더보기';
  } else {
    bar.classList.remove('hidden');
    btn.disabled = state.feedState.feedLoading;
    if (label) label.textContent = state.feedState.feedLoading ? '불러오는 중...' : '이전 내역 더보기';
  }

  meta.textContent = state.feedState.feedTradingDate ? state.feedState.feedTradingDate + ' 거래일' : '';
}

// ── Render live feed from state ──

function renderLiveFeedFromState(options) {
  if (!options) options = {};
  var restoreScroll = options.restoreScroll || false;
  var preserveViewport = options.preserveViewport || false;
  var previousScrollHeight = options.previousScrollHeight || 0;
  var previousScrollTop = options.previousScrollTop || 0;

  if (state.currentView !== 'live') return;
  var container = document.getElementById('chat-container');
  if (!container) return;

  cleanupStockCards();
  container.textContent = '';
  setActivityCount(0);

  var visibleActivities = filterResolvedStarts(state.feedState.feedItems);
  if (!visibleActivities.length) {
    renderPlaceholder(container, 'empty', '현재 거래일 활동이 없습니다');
  } else {
    visibleActivities.forEach(function (activity) {
      Feed.appendActivity(activity, { skipStore: true });
    });
  }

  state.feedState.loaded = true;
  state.feedState.activityCount = visibleActivities.length;
  var countEl = document.getElementById('activity-count');
  if (countEl) countEl.textContent = getActivityCount() + '건';
  updateFeedLoadMore();
  refreshIcons();

  requestAnimationFrame(function () {
    if (preserveViewport) {
      var nextHeight = container.scrollHeight;
      container.scrollTop = previousScrollTop + (nextHeight - previousScrollHeight);
      return;
    }
    if (restoreScroll) {
      container.scrollTop = state.feedState.scrollPos || 0;
      return;
    }
    container.scrollTop = container.scrollHeight;
  });
}

// ── Scroll management ──

function setupFeedScroll() {
  var container = document.getElementById('chat-container');
  if (!container) return;

  container.addEventListener('scroll', function () {
    state.feedState.scrollPos = container.scrollTop;
    state.isNearTop = container.scrollTop < 64;
    var distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distFromBottom < 40) {
      state.autoScroll = true;
      state.missedCount = 0;
      updateScrollFab();
    } else {
      state.autoScroll = false;
    }
    updateFeedLoadMore();
  });
}

function updateScrollFab() {
  var fab = document.getElementById('scroll-to-bottom');
  var badge = document.getElementById('scroll-fab-badge');
  if (!fab || !badge) return;
  if (state.missedCount > 0) {
    badge.textContent = state.missedCount > 99 ? '99+' : String(state.missedCount);
    badge.classList.add('visible');
    fab.classList.remove('hidden');
  } else {
    badge.classList.remove('visible');
    fab.classList.add('hidden');
  }
}

// ── Timer cleanup ──

function cleanupStockCards() {
  Object.values(state.feedState.stockCards).forEach(function (card) {
    if (card.liveTimer) clearInterval(card.liveTimer);
  });
  state.feedState.stockCards = {};
}

// ── Sidebar / section toggles ──

export function toggleLeftSidebar() {
  var sb = document.getElementById('left-sidebar');
  if (sb) sb.classList.toggle('collapsed');
}

export function toggleRightSidebar() {
  var sb = document.getElementById('right-sidebar');
  if (sb) sb.classList.toggle('collapsed');
}

export function toggleSection(name) {
  var arrow = document.getElementById(name + '-arrow');
  var body = document.getElementById(name + '-list');
  if (!body) return;
  body.classList.toggle('collapsed-section');
  if (arrow) arrow.classList.toggle('collapsed-icon');
}

export function toggleSettings() {
  var body = document.getElementById('settings-body');
  var arrow = document.getElementById('settings-arrow');
  if (!body) return;
  var hidden = body.classList.toggle('hidden');
  if (arrow) arrow.style.transform = hidden ? 'rotate(-90deg)' : 'rotate(0deg)';
}

// ── Navigation state ──

function updateNavigationState() {
  var navLive = document.getElementById('nav-live');
  var navLatest = document.getElementById('nav-latest-report');
  var baseClass = 'w-full text-left px-3 py-2 rounded-lg text-sm text-gray-400 hover:bg-dark-700 flex items-center gap-2 transition';
  var activeClass = 'w-full text-left px-3 py-2 rounded-lg text-sm font-medium bg-blue-900/30 text-blue-300 flex items-center gap-2';

  if (navLive) navLive.className = state.currentView === 'live' ? activeClass : baseClass;
  if (navLatest) {
    navLatest.className = (state.currentView === 'report' && state.currentReportSelection.mode === 'latest')
      ? activeClass
      : baseClass;
  }

  document.querySelectorAll('#report-list [data-report-id]').forEach(function (item) {
    var isActive = state.currentView === 'report'
      && state.currentReportSelection.mode === 'id'
      && item.dataset.reportId === state.currentReportSelection.id;
    item.className = isActive
      ? 'w-full text-left px-3 py-2 rounded-lg text-xs bg-coin-purple/20 border border-coin-purple/30 text-coin-purple'
      : 'w-full text-left px-3 py-2 rounded-lg text-xs text-gray-400 hover:bg-dark-700 transition';
  });
}

function updateMainPanelChrome() {
  var titleEl = document.getElementById('main-panel-title');
  var filterEl = document.getElementById('feed-filter-track');
  var clearBtn = document.getElementById('btn-main-clear');
  var clearIcon = document.getElementById('main-clear-icon');
  var clearLabel = document.getElementById('main-clear-label');
  var refreshBtn = document.getElementById('btn-main-refresh');
  var refreshIcon = document.getElementById('main-refresh-icon');
  var refreshLabel = document.getElementById('main-refresh-label');

  if (titleEl) {
    titleEl.textContent = '';
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', state.currentView === 'live' ? 'activity' : 'clipboard-list');
    icon.className = 'w-3.5 h-3.5 text-coin-purple';
    titleEl.appendChild(icon);
    titleEl.appendChild(document.createTextNode(state.currentView === 'live' ? ' Activity Feed' : ' Coin Reports'));
  }
  if (filterEl) filterEl.classList.toggle('hidden', state.currentView !== 'live');

  if (clearBtn && clearIcon && clearLabel) {
    clearBtn.title = state.currentView === 'live' ? '화면 비우기' : '실시간 모니터링으로 복귀';
    clearBtn.setAttribute('aria-label', clearBtn.title);
    clearIcon.setAttribute('data-lucide', state.currentView === 'live' ? 'trash-2' : 'activity');
    clearLabel.textContent = state.currentView === 'live' ? '클리어' : '라이브 복귀';
  }
  if (refreshBtn && refreshIcon && refreshLabel) {
    refreshBtn.title = state.currentView === 'live' ? '새로고침' : '현재 리포트 다시 불러오기';
    refreshBtn.setAttribute('aria-label', refreshBtn.title);
    refreshIcon.setAttribute('data-lucide', 'refresh-cw');
    refreshLabel.textContent = state.currentView === 'live' ? '새로고침' : '리포트 새로고침';
  }

  refreshIcons();
}

// ── Scan timeline ──

function updateScanTimeline() {
  var progressBar = document.getElementById('scan-progress-fill');
  var countdown = document.getElementById('scan-countdown');
  var lastTimeEl = document.getElementById('scan-last-time');
  var nextTimeEl = document.getElementById('scan-next-time');
  if (!progressBar && !countdown) return;

  if (!state.lastCycleTime || !state.scanIntervalHours) {
    if (countdown) countdown.textContent = '스캔 정보 없음';
    if (progressBar) progressBar.style.width = '0%';
    if (lastTimeEl) lastTimeEl.textContent = '마지막: --';
    if (nextTimeEl) nextTimeEl.textContent = '다음: --';
    return;
  }

  var intervalMs = state.scanIntervalHours * 3600 * 1000;
  var nextScanTime = state.lastCycleTime + intervalMs;
  var now = Date.now();
  var elapsed = now - state.lastCycleTime;
  var remaining = Math.max(0, nextScanTime - now);
  var progress = Math.min(100, (elapsed / intervalMs) * 100);

  if (progressBar) {
    progressBar.style.width = progress + '%';
    if (progress < 50) {
      progressBar.style.background = '#34d399';
    } else if (progress < 80) {
      progressBar.style.background = '#fbbf24';
    } else {
      progressBar.style.background = '#f87171';
    }
  }

  if (countdown) {
    if (remaining <= 0) {
      countdown.textContent = '스캔 예정 시간 경과 (곧 실행)';
      countdown.style.color = '#f87171';
    } else {
      countdown.textContent = '다음 스캔 ' + formatDuration(remaining / 1000) + ' 후';
      countdown.style.color = '#d1d5db';
    }
  }

  if (lastTimeEl) lastTimeEl.textContent = '마지막: ' + formatDateTime(state.lastCycleTime);
  if (nextTimeEl) nextTimeEl.textContent = '다음: ' + formatDateTime(nextScanTime);
}

function startScanCountdown() {
  if (state.scanCountdownTimer) clearInterval(state.scanCountdownTimer);
  state.scanCountdownTimer = setInterval(function () {
    updateScanTimeline();
    updateRecCountdowns();
  }, 1000);
}

// ── Metric helpers ──

function scheduleAccountOverviewRefresh(delay) {
  if (delay == null) delay = 250;
  if (state.accountRefreshTimer) clearTimeout(state.accountRefreshTimer);
  state.accountRefreshTimer = setTimeout(function () {
    state.accountRefreshTimer = null;
    loadBalance();
  }, delay);
}

// ================================================================
// -- Global window exports (onclick handlers from coin.html) --
// ================================================================

window.switchView = function (v) { switchView(v); };
window.toggleLeftSidebar = function () { toggleLeftSidebar(); };
window.toggleRightSidebar = function () { toggleRightSidebar(); };
window.toggleSection = function (n) { toggleSection(n); };
window.toggleSettings = function () { toggleSettings(); };
window.updateSetting = function (k, v) { updateSetting(k, v); };
window.setLogFilter = function (f) { setLogFilter(f); };
window.toggleMonitorExpand = function () { Monitor.toggleExpand(); };
window.loadMoreActivities = function () { loadMoreActivities(); };
window.scrollToBottom = function () { Feed.scrollToBottom(); };
window.handleMainClear = function () { handleMainClear(); };
window.refreshCurrentView = function () { refreshCurrentView(); };
window.toggleDetail = function (id) { Feed.toggleDetail(id); };
window.approveRecommendation = function (id) { approveRecommendation(id); };
window.rejectRecommendation = function (id) { rejectRecommendation(id); };
window.switchToReport = function (id) { switchToReport(id); };

// ================================================================
// -- DOMContentLoaded --
// ================================================================

document.addEventListener('DOMContentLoaded', function () {
  // Setup
  setupFeedScroll();
  renderActionButtons();
  updateNavigationState();
  updateMainPanelChrome();
  var fab = document.getElementById('scroll-to-bottom');
  if (fab) fab.addEventListener('click', function () { Feed.scrollToBottom(); });
  var scanBtn = document.getElementById('btn-trigger-scan');
  if (scanBtn) scanBtn.addEventListener('click', triggerScan);
  var reportBtn = document.getElementById('btn-generate-report');
  if (reportBtn) reportBtn.addEventListener('click', generateManualReport);

  // SSE
  connectSSE();

  // Initial loads
  loadSystemStatus();
  loadAgentState({ quiet: true }).then(function () { Monitor.render(); });
  loadBalance();
  loadRecommendations();
  loadWatchlist();
  loadSettings();
  loadTodayActivities();
  loadLatestReport();
  loadReportList();

  // Polling intervals
  setInterval(loadBalance, 30000);           // 30s
  setInterval(loadRecommendations, 10000);   // 10s
  setInterval(loadSystemStatus, 60000);      // 60s
  setInterval(loadAgentState, 15000);        // 15s
  setInterval(loadWatchlist, 120000);         // 2min
  setInterval(loadLatestReport, 300000);      // 5min
  setInterval(loadReportList, 300000);        // 5min

  // Countdown ticker (scan timeline + recommendation expiry)
  startScanCountdown();
});
