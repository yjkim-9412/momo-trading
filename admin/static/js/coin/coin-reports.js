// ── coin-reports.js — Report management ──
//
// Security note: all dynamic content is escaped via escapeHtml()
// before DOM insertion. This is an admin-only dashboard page with
// no user-generated input. All dynamic values are server-sourced
// and pre-escaped. This mirrors the original coin-app.js exactly.

import {
  escapeHtml, formatKRW, formatTime, formatAdminAgentText,
  refreshIcons, renderPlaceholder, fetchJSON,
} from '../shared/admin-core.js';
import * as Feed from '../shared/admin-feed.js';
import { state, API, setActivityCount } from './coin-state.js';
import {
  formatSignedKRW, formatDateTime, truncateText,
  safeJsonParse, getReportHeadline, getReportOriginLabel,
  getReportSourceLabel, getReportTriggerReasonLabel, resolveReportTimestamp,
  setReportCaches, getSelectedCachedReport, setStatusText,
} from './coin-utils.js';

// Late-bound references
let _cleanupStockCards = null;
let _updateNavigationState = null;

export function registerReportDeps(deps) {
  _cleanupStockCards = deps.cleanupStockCards;
  _updateNavigationState = deps.updateNavigationState;
}

// -- Load latest report --

export async function loadLatestReport() {
  try {
    var json = await fetchJSON(API + '/reports/latest');
    state.latestReportCache = json.data || null;
    if (state.latestReportCache && state.latestReportCache.id) {
      state.reportCacheById.set(state.latestReportCache.id, state.latestReportCache);
    }
    renderLatestReport(state.latestReportCache);
  } catch (err) {
    console.error('[coin] latest report error:', err);
    state.latestReportCache = null;
    renderLatestReport(null);
  }
}

// -- Render latest report sidebar --
// All dynamic values below are escaped via escapeHtml() (admin-only page).

export function renderLatestReport(report) {
  var container = document.getElementById('latest-report');
  if (!container) return;
  if (!report) {
    container.textContent = '';
    var empty = document.createElement('div');
    empty.className = 'text-gray-500 text-xs py-1';
    empty.textContent = '최근 리포트 없음';
    container.appendChild(empty);
    return;
  }

  var pnl = Number(report.total_pnl ?? 0);
  var pnlColor = pnl >= 0 ? '#34d399' : '#f87171';
  var summary = truncateText(report.market_summary || report.performance_review || report.lessons_learned || '요약 없음', 120);
  var reportSource = getReportOriginLabel(report.report_source, report.trigger_reason);
  var reportTs = resolveReportTimestamp(report);
  var reportTsText = reportTs ? formatDateTime(reportTs) : '--';

  // Build DOM nodes for the latest report sidebar
  var wrapper = document.createElement('div');
  wrapper.className = 'space-y-2';

  var row1 = document.createElement('div');
  row1.className = 'flex items-center justify-between gap-2';
  var dateSpan = document.createElement('span');
  dateSpan.className = 'text-gray-300';
  dateSpan.textContent = report.report_date || '--';
  var pnlSpan = document.createElement('span');
  pnlSpan.style.color = pnlColor;
  pnlSpan.textContent = formatKRW(pnl);
  row1.appendChild(dateSpan);
  row1.appendChild(pnlSpan);

  var row2 = document.createElement('div');
  row2.className = 'flex items-center justify-between gap-2 text-[10px]';
  var srcSpan = document.createElement('span');
  srcSpan.className = 'text-blue-300 truncate';
  srcSpan.textContent = reportSource;
  srcSpan.title = reportSource;
  var tsSpan = document.createElement('span');
  tsSpan.className = 'text-gray-500';
  tsSpan.textContent = reportTsText;
  row2.appendChild(srcSpan);
  row2.appendChild(tsSpan);

  var summaryDiv = document.createElement('div');
  summaryDiv.className = 'text-[11px] text-gray-500 whitespace-pre-wrap';
  summaryDiv.textContent = summary;

  var detailBtn = document.createElement('button');
  detailBtn.type = 'button';
  detailBtn.className = 'w-full text-left text-[11px] text-coin-purple hover:text-blue-300 transition';
  detailBtn.textContent = '최신 리포트 상세 보기';
  detailBtn.onclick = function () { window.switchView('latest-report'); };

  wrapper.appendChild(row1);
  wrapper.appendChild(row2);
  wrapper.appendChild(summaryDiv);
  wrapper.appendChild(detailBtn);

  container.textContent = '';
  container.appendChild(wrapper);
}

// -- Load report list --

export async function loadReportList() {
  try {
    var json = await fetchJSON(API + '/reports?limit=30');
    setReportCaches(json.data || []);
    renderReportList();
  } catch (err) {
    console.error('[coin] report list error:', err);
    var listEl = document.getElementById('report-list');
    if (listEl) {
      listEl.textContent = '';
      var errDiv = document.createElement('div');
      errDiv.className = 'px-3 text-xs text-red-400';
      errDiv.textContent = '리포트 목록 조회 실패';
      listEl.appendChild(errDiv);
    }
  }
}

// -- Render report list sidebar --
// All dynamic values below are escaped via escapeHtml() (admin-only page).

export function renderReportList() {
  var listEl = document.getElementById('report-list');
  if (!listEl) return;

  if (!state.reportListCache.length) {
    listEl.textContent = '';
    var emptyDiv = document.createElement('div');
    emptyDiv.className = 'px-3 text-xs text-gray-500';
    emptyDiv.textContent = '리포트 없음';
    listEl.appendChild(emptyDiv);
    return;
  }

  // Build report list using DOM methods for safety (admin-only page, server-sourced data)
  listEl.textContent = '';
  state.reportListCache.forEach(function (report) {
    var reportId = String(report.id || '');
    var ts = resolveReportTimestamp(report);
    var title = ts ? formatDateTime(ts).replace(/\.\s*/g, '/') : String(report.report_date || '--');
    var source = getReportOriginLabel(report.report_source, report.trigger_reason);
    var pnl = formatSignedKRW(report.total_pnl);
    var active = state.currentView === 'report'
      && state.currentReportSelection.mode === 'id'
      && state.currentReportSelection.id === reportId;
    var classes = active
      ? 'w-full text-left px-3 py-2 rounded-lg text-xs bg-coin-purple/20 border border-coin-purple/30 text-coin-purple'
      : 'w-full text-left px-3 py-2 rounded-lg text-xs text-gray-400 hover:bg-dark-700 transition';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.dataset.reportId = reportId;
    btn.className = classes;
    btn.onclick = function () { window.switchToReport(reportId); };

    var topRow = document.createElement('div');
    topRow.className = 'flex items-center justify-between gap-2';
    var titleSpan = document.createElement('span');
    titleSpan.className = 'truncate';
    titleSpan.textContent = title;
    var pnlSpan = document.createElement('span');
    pnlSpan.className = 'text-[10px] ' + (Number(report.total_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400');
    pnlSpan.textContent = pnl;
    topRow.appendChild(titleSpan);
    topRow.appendChild(pnlSpan);

    var bottomRow = document.createElement('div');
    bottomRow.className = 'mt-1 flex items-center justify-between gap-2 text-[10px]';
    var srcSpan = document.createElement('span');
    srcSpan.className = 'text-blue-300 truncate';
    srcSpan.textContent = source;
    srcSpan.title = source;
    var regimeSpan = document.createElement('span');
    regimeSpan.className = 'text-gray-500';
    regimeSpan.textContent = String(report.market_regime || '');
    bottomRow.appendChild(srcSpan);
    bottomRow.appendChild(regimeSpan);

    btn.appendChild(topRow);
    btn.appendChild(bottomRow);
    listEl.appendChild(btn);
  });

  if (_updateNavigationState) _updateNavigationState();
}

// -- Load and render selected report --

export async function loadCurrentReportView(options) {
  var forceLatest = options && options.forceLatest;
  var container = document.getElementById('chat-container');
  if (!container) return;

  if (_cleanupStockCards) _cleanupStockCards();
  setActivityCount(0);
  var countEl = document.getElementById('activity-count');
  if (countEl) countEl.textContent = '0건';
  renderPlaceholder(container, 'loading', '리포트 불러오는 중...');

  try {
    var report = null;
    if (state.currentReportSelection.mode === 'latest') {
      if (forceLatest || !state.latestReportCache) {
        await loadLatestReport();
      }
      report = state.latestReportCache || state.reportListCache[0] || null;
    } else if (state.currentReportSelection.mode === 'id') {
      report = getSelectedCachedReport();
      if (!report) {
        await loadReportList();
        report = getSelectedCachedReport();
      }
    }

    if (!report) {
      renderPlaceholder(container, 'empty', '표시할 코인 리포트가 없습니다');
      setStatusText('코인 리포트가 없습니다', '#9ca3af');
      if (_updateNavigationState) _updateNavigationState();
      return;
    }

    if (report.id) {
      state.reportCacheById.set(report.id, report);
    }
    container.textContent = '';
    container.appendChild(createReportCard(report));
    await loadReportActivities(report, container);
    setStatusText('코인 리포트를 불러왔습니다', '#93c5fd');
    if (_updateNavigationState) _updateNavigationState();
    refreshIcons();
  } catch (err) {
    console.error('[coin] report view error:', err);
    renderPlaceholder(container, 'error', '리포트 로드 실패: ' + err.message);
    setStatusText('리포트 로드 실패 · ' + truncateText(err.message, 80), '#f87171');
  }
}

// -- Create comprehensive report card --
// All dynamic values below are pre-escaped via escapeHtml().
// This is an admin-only dashboard with server-sourced data only.

export function createReportCard(report) {
  var div = document.createElement('div');
  div.className = 'bg-dark-700 rounded-xl p-5 border border-[#3d3350] mx-2 chat-bubble animate-fade-in';

  var stats = safeJsonParse(report.strategy_stats, {}) || {};
  var tradeEval = stats.trade_evaluation || {};
  var feedback = stats.feedback_for_next_cycle || {};
  var successPatterns = Array.isArray(stats.success_patterns) ? stats.success_patterns : [];
  var failurePatterns = Array.isArray(stats.failure_patterns) ? stats.failure_patterns : [];
  var riskAlerts = Array.isArray(stats.risk_alerts) ? stats.risk_alerts : [];
  var topPicksRaw = safeJsonParse(report.top_picks, []) || [];
  var topPicks = Array.isArray(topPicksRaw)
    ? topPicksRaw.map(function (item) {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') return item.name || item.symbol || '';
        return '';
      }).filter(Boolean)
    : [];

  var reportSource = getReportSourceLabel(report.report_source);
  var triggerReason = getReportTriggerReasonLabel(report.trigger_reason);
  var reportHeadline = getReportHeadline(report.report_source);
  var periodStarted = report.period_started_at ? formatDateTime(report.period_started_at) : '--';
  var periodEnded = report.period_ended_at ? formatDateTime(report.period_ended_at) : '--';
  var totalPnl = Number(report.total_pnl ?? 0);
  var unrealizedPnl = Number(report.unrealized_pnl ?? 0);
  var realizedPnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
  var unrealizedPnlColor = unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400';
  var closedTrades = Number(report.win_count || 0) + Number(report.loss_count || 0);
  var winRate = closedTrades > 0 ? ((Number(report.win_count || 0) / closedTrades) * 100).toFixed(1) + '%' : '-';
  var btcDominance = report.btc_dominance != null ? Number(report.btc_dominance).toFixed(2) + '%' : '--';

  var riskHtml = riskAlerts.length
    ? '<div class="mt-3">'
        + '<div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">'
        + '<i data-lucide="shield-alert" class="w-4 h-4"></i> 리스크 알림'
        + '</div>'
        + '<div class="space-y-1">'
        + riskAlerts.map(function (item) { return '<div class="text-xs text-amber-200 bg-amber-900/20 border border-amber-400/10 rounded px-2 py-1">' + escapeHtml(String(item)) + '</div>'; }).join('')
        + '</div>'
      + '</div>'
    : '';

  var patternHtml = (successPatterns.length || failurePatterns.length)
    ? '<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">'
        + '<div class="bg-dark-900 rounded-lg p-3">'
        + '<div class="text-sm text-gray-300 mb-2 flex items-center gap-1.5">'
        + '<i data-lucide="trending-up" class="w-4 h-4"></i> 성공 패턴'
        + '</div>'
        + '<div class="space-y-1">' + (successPatterns.length
          ? successPatterns.map(function (item) { return '<div class="text-xs text-green-300">' + escapeHtml(String(item)) + '</div>'; }).join('')
          : '<div class="text-xs text-gray-500">기록 없음</div>')
        + '</div>'
        + '</div>'
        + '<div class="bg-dark-900 rounded-lg p-3">'
        + '<div class="text-sm text-gray-300 mb-2 flex items-center gap-1.5">'
        + '<i data-lucide="trending-down" class="w-4 h-4"></i> 실패 패턴'
        + '</div>'
        + '<div class="space-y-1">' + (failurePatterns.length
          ? failurePatterns.map(function (item) { return '<div class="text-xs text-red-300">' + escapeHtml(String(item)) + '</div>'; }).join('')
          : '<div class="text-xs text-gray-500">기록 없음</div>')
        + '</div>'
        + '</div>'
      + '</div>'
    : '';

  var feedbackEntries = Object.entries(feedback).filter(function (entry) { return entry[1]; });
  var feedbackHtml = feedbackEntries.length
    ? '<div class="mt-3">'
        + '<div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">'
        + '<i data-lucide="target" class="w-4 h-4"></i> 다음 운영 피드백'
        + '</div>'
        + '<div class="grid grid-cols-1 md:grid-cols-2 gap-2">'
        + feedbackEntries.map(function (entry) {
            return '<div class="bg-dark-900 rounded p-2">'
              + '<div class="text-[11px] text-gray-500 mb-1">' + escapeHtml(String(entry[0])) + '</div>'
              + '<div class="text-xs text-gray-300 whitespace-pre-wrap">' + escapeHtml(String(entry[1])) + '</div>'
              + '</div>';
          }).join('')
        + '</div>'
      + '</div>'
    : '';

  // Build the report card HTML. All dynamic values are pre-escaped via escapeHtml().
  // This is an admin-only dashboard page with no user-generated input.
  var cardHtml = ''
  + '<div class="flex flex-wrap items-center justify-between gap-3 mb-4">'
  + '<div>'
  + '<div class="flex items-center gap-2 text-lg font-bold text-white">'
  + '<i data-lucide="clipboard-list" class="w-5 h-5 text-coin-purple"></i>'
  + ' ' + escapeHtml(reportHeadline)
  + '</div>'
  + '<div class="mt-1 text-xs text-gray-500">'
  + escapeHtml(periodStarted) + ' ~ ' + escapeHtml(periodEnded)
  + '</div>'
  + '</div>'
  + '<div class="flex flex-wrap items-center gap-2 text-xs">'
  + '<span class="px-2 py-1 rounded-full bg-blue-900/30 text-blue-300">' + escapeHtml(reportSource) + '</span>'
  + (triggerReason
    ? '<span class="px-2 py-1 rounded-full bg-violet-900/20 text-violet-200">' + escapeHtml(triggerReason) + '</span>'
    : '')
  + '<span class="px-2 py-1 rounded-full bg-dark-900 text-gray-300">' + escapeHtml(String(report.market_regime || 'UNKNOWN')) + '</span>'
  + '<span class="px-2 py-1 rounded-full bg-dark-900 text-gray-500">' + escapeHtml(String(report.report_date || '--')) + '</span>'
  + '</div>'
  + '</div>'

  + '<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">'
  + '<div class="bg-dark-900 rounded-lg p-3 text-center"><div class="text-xl font-bold text-blue-300">' + Number(report.total_cycles || 0) + '</div><div class="text-xs text-gray-500">완료 사이클</div></div>'
  + '<div class="bg-dark-900 rounded-lg p-3 text-center"><div class="text-xl font-bold text-purple-300">' + Number(report.total_analyses || 0) + '</div><div class="text-xs text-gray-500">분석 완료</div></div>'
  + '<div class="bg-dark-900 rounded-lg p-3 text-center"><div class="text-xl font-bold text-yellow-300">' + Number(report.buy_count || 0) + ' / ' + Number(report.sell_count || 0) + '</div><div class="text-xs text-gray-500">진입 / 청산</div></div>'
  + '<div class="bg-dark-900 rounded-lg p-3 text-center"><div class="text-xl font-bold text-coin-gold">' + escapeHtml(btcDominance) + '</div><div class="text-xs text-gray-500">BTC 비중</div></div>'
  + '</div>'

  + '<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">'
  + '<div class="bg-dark-900 rounded-lg p-3 text-center"><div class="text-xl font-bold ' + realizedPnlColor + '">' + escapeHtml(formatSignedKRW(totalPnl)) + '</div><div class="text-xs text-gray-500">실현 손익 · 승률 ' + escapeHtml(winRate) + '</div></div>'
  + '<div class="bg-dark-900 rounded-lg p-3 text-center"><div class="text-xl font-bold ' + unrealizedPnlColor + '">' + escapeHtml(formatSignedKRW(unrealizedPnl)) + '</div><div class="text-xs text-gray-500">미실현 손익 · 보유 ' + Number(report.open_position_count || 0) + '개</div></div>'
  + '</div>'

  + '<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">'
  + '<div class="bg-dark-900 rounded-lg p-3"><div class="text-xs text-gray-500 mb-1">전체 24h 거래대금</div><div class="text-sm font-medium text-gray-200">' + escapeHtml(formatKRW(report.total_24h_volume || 0)) + '</div></div>'
  + '<div class="bg-dark-900 rounded-lg p-3"><div class="text-xs text-gray-500 mb-1">거래 평가</div><div class="text-sm font-medium text-gray-200">총 ' + Number(tradeEval.total_trades || 0) + '건 / 수익 ' + Number(tradeEval.profitable_trades || 0) + '건 / 손실 ' + Number(tradeEval.loss_trades || 0) + '건</div></div>'
  + '</div>';

  if (report.market_summary) {
    cardHtml += '<div class="mb-3"><div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="waves" class="w-4 h-4"></i> 시장 요약</div><div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">' + escapeHtml(report.market_summary) + '</div></div>';
  }
  if (report.performance_review) {
    cardHtml += '<div class="mb-3"><div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="pie-chart" class="w-4 h-4"></i> 성과 평가</div><div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">' + escapeHtml(report.performance_review) + '</div></div>';
  }
  if (report.lessons_learned) {
    cardHtml += '<div class="mb-3"><div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="book-open" class="w-4 h-4"></i> 학습 포인트</div><div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">' + escapeHtml(report.lessons_learned) + '</div></div>';
  }
  if (report.next_day_plan) {
    cardHtml += '<div class="mb-3"><div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="compass" class="w-4 h-4"></i> 다음 운영 계획</div><div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">' + escapeHtml(report.next_day_plan) + '</div></div>';
  }
  if (topPicks.length) {
    cardHtml += '<div class="mb-3"><div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="crosshair" class="w-4 h-4"></i> 관심 코인</div><div class="flex flex-wrap gap-2">'
      + topPicks.map(function (item) { return '<span class="px-2 py-1 rounded-full bg-coin-purple/20 text-coin-purple text-xs">' + escapeHtml(String(item)) + '</span>'; }).join('')
      + '</div></div>';
  }

  cardHtml += feedbackHtml + patternHtml + riskHtml;

  div.insertAdjacentHTML('beforeend', cardHtml);
  return div;
}

// -- Load report period activities --

export async function loadReportActivities(report, container) {
  try {
    var json = await fetchJSON(API + '/reports/' + encodeURIComponent(report.id) + '/activities?limit=500');
    var items = Array.isArray(json.data) ? json.data : [];
    var countEl = document.getElementById('activity-count');
    if (countEl) countEl.textContent = items.length + '건';

    var section = document.createElement('div');
    section.className = 'mt-4 border-t border-[#3d3350]';

    var toggleBtn = document.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.className = 'w-full text-center text-gray-500 hover:text-gray-300 text-xs py-3 flex items-center justify-center gap-2 transition';

    var toggleIcon = document.createElement('span');
    toggleIcon.className = 'activity-toggle-icon';
    var chevronIcon = document.createElement('i');
    chevronIcon.setAttribute('data-lucide', 'chevron-right');
    chevronIcon.className = 'w-3 h-3 inline-block';
    toggleIcon.appendChild(chevronIcon);
    toggleBtn.appendChild(toggleIcon);
    toggleBtn.appendChild(document.createTextNode(' 리포트 구간 활동 로그 (' + items.length + '건)'));

    var logContainer = document.createElement('div');
    logContainer.className = 'hidden';
    logContainer.style.maxHeight = '600px';
    logContainer.style.overflowY = 'auto';

    if (items.length) {
      [].concat(items).reverse().forEach(function (activity) {
        logContainer.appendChild(Feed.createBubble
          ? Feed.createBubble(activity)
          : _createSimpleBubble(activity));
      });
    } else {
      var emptyMsg = document.createElement('div');
      emptyMsg.className = 'text-center text-gray-500 text-xs py-4';
      emptyMsg.textContent = '해당 리포트 구간의 활동 로그가 없습니다';
      logContainer.appendChild(emptyMsg);
    }

    toggleBtn.onclick = function () {
      var isHidden = logContainer.classList.contains('hidden');
      logContainer.classList.toggle('hidden');
      var iconEl = toggleBtn.querySelector('.activity-toggle-icon');
      if (iconEl) {
        iconEl.textContent = '';
        var newIcon = document.createElement('i');
        newIcon.setAttribute('data-lucide', isHidden ? 'chevron-down' : 'chevron-right');
        newIcon.className = 'w-3 h-3 inline-block';
        iconEl.appendChild(newIcon);
      }
      refreshIcons();
    };

    section.appendChild(toggleBtn);
    section.appendChild(logContainer);
    container.appendChild(section);
    refreshIcons();
  } catch (err) {
    console.error('[coin] report activities error:', err);
    var error = document.createElement('div');
    error.className = 'mx-2 mt-4 text-xs text-red-400';
    error.textContent = '리포트 구간 활동 로그 로드 실패: ' + err.message;
    container.appendChild(error);
  }
}

// Fallback bubble creator if Feed doesn't expose createBubble
function _createSimpleBubble(data) {
  var div = document.createElement('div');
  div.className = 'chat-bubble';
  var time = formatTime(data.created_at);
  var row = document.createElement('div');
  row.className = 'flex items-start gap-2 px-3 py-1.5 rounded-lg hover:bg-dark-700/50 transition group';
  var timeSpan = document.createElement('span');
  timeSpan.className = 'text-xs text-gray-500 mt-0.5 shrink-0 w-14';
  timeSpan.textContent = time;
  var bodyDiv = document.createElement('div');
  bodyDiv.className = 'flex-1 min-w-0';
  var textDiv = document.createElement('div');
  textDiv.className = 'text-sm whitespace-pre-wrap';
  textDiv.textContent = formatAdminAgentText(data.summary);
  bodyDiv.appendChild(textDiv);
  row.appendChild(timeSpan);
  row.appendChild(bodyDiv);
  div.appendChild(row);
  return div;
}

// -- Settings --

export async function loadSettings() {
  try {
    var json = await fetchJSON(API + '/settings');
    var data = json.data;
    if (!data) return;
    renderSettings(data);
  } catch (err) {
    console.error('[coin] settings load error:', err);
  }
}

export function renderSettings(settings) {
  var el = function (id) { return document.getElementById(id); };
  if (settings.CRYPTO_TRADING_ENABLED != null && el('set-trading')) el('set-trading').checked = !!settings.CRYPTO_TRADING_ENABLED;
  if (settings.CRYPTO_ENABLED != null && el('set-auto-scan')) el('set-auto-scan').checked = !!settings.CRYPTO_ENABLED;
  if (settings.CRYPTO_AUTONOMY_MODE && el('set-mode')) el('set-mode').value = settings.CRYPTO_AUTONOMY_MODE;
  if (settings.CRYPTO_TRADING_STYLE_MODE && el('set-trading-style')) el('set-trading-style').value = settings.CRYPTO_TRADING_STYLE_MODE;
  if (settings.CRYPTO_SCAN_INTERVAL_HOURS != null && el('set-risk')) el('set-risk').value = String(settings.CRYPTO_SCAN_INTERVAL_HOURS);
  if (settings.CRYPTO_TIMEBOX_HOURS != null && el('set-timebox')) el('set-timebox').value = String(settings.CRYPTO_TIMEBOX_HOURS);
}

export async function updateSetting(key, value) {
  try {
    await fetchJSON(API + '/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
    loadSettings();
    if (_loadSystemStatus) _loadSystemStatus();
  } catch (err) {
    console.error('[coin] setting update error:', err);
  }
}

// Late-bound loadSystemStatus reference
let _loadSystemStatus = null;
export function registerSettingsDeps(deps) {
  _loadSystemStatus = deps.loadSystemStatus;
}
