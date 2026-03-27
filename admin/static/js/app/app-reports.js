// ── app-reports.js — Report loading and rendering (ES module) ──

import { state, API, formatSignedAmount } from './app-state.js';
import { fetchJSON, escapeHtml, refreshIcons } from '../shared/admin-core.js';
import * as Toast from '../shared/admin-toast.js';
import * as Feed from '../shared/admin-feed.js';

// ── Report List (left sidebar) ──
export async function loadReportList() {
  try {
    var json = await fetchJSON(API + '/reports?limit=10&market_scope=' + encodeURIComponent(state.currentMarket));
    var listEl = document.getElementById('report-list');
    listEl.replaceChildren();
    if (json.data && json.data.length) {
      json.data.forEach(function (r) {
        var btn = document.createElement('button');
        btn.className = 'w-full text-left px-3 py-1 text-xs text-gray-400 hover:bg-dark-700 rounded';
        btn.textContent = r.report_date + ' (' + (r.market_scope || state.currentMarket) + ', ' + _reportCurrency(r) + ')';
        btn.onclick = function () { switchToReport(r.report_date); };
        listEl.appendChild(btn);
      });
    } else {
      var noReports = document.createElement('div');
      noReports.className = 'px-3 text-xs text-gray-500';
      noReports.textContent = '\uB9AC\uD3EC\uD2B8 \uC5C6\uC74C';
      listEl.appendChild(noReports);
    }
  } catch (err) {
    console.error('Report list error:', err);
  }
}

export function switchToReport(dateStr) {
  state.currentView = 'report';
  loadReport(dateStr);
}

// ── Load Report ──
export async function loadReport(dateStr) {
  var container = document.getElementById('chat-container');
  container.replaceChildren();
  var loadingDiv = document.createElement('div');
  loadingDiv.className = 'text-center text-gray-500 text-sm py-4';
  loadingDiv.textContent = '\uB9AC\uD3EC\uD2B8 \uBD88\uB7EC\uC624\uB294 \uC911...';
  container.appendChild(loadingDiv);
  if (state._insertBackToLiveBar) state._insertBackToLiveBar(container);
  if (state._cleanupStockCards) state._cleanupStockCards();

  try {
    var url = API + '/reports/latest';
    if (dateStr && dateStr !== 'today') url = API + '/reports/' + dateStr;
    url += (url.indexOf('?') !== -1 ? '&' : '?') + 'market_scope=' + encodeURIComponent(state.currentMarket);
    var json = await fetchJSON(url);
    var report = json.data;

    if (!report) {
      container.replaceChildren();
      var noReport = document.createElement('div');
      noReport.className = 'text-center text-gray-500 text-sm py-8';
      noReport.textContent = '\uD574\uB2F9 \uB0A0\uC9DC\uC758 \uB9AC\uD3EC\uD2B8\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4';
      container.appendChild(noReport);
      if (dateStr && dateStr !== 'today') await loadDateActivities(dateStr, container);
      return;
    }
    container.replaceChildren();
    container.appendChild(createReportCard(report));
    if (report.report_date) await loadDateActivities(report.report_date, container);
    refreshIcons();
  } catch (err) {
    container.replaceChildren();
    var errDiv = document.createElement('div');
    errDiv.className = 'text-center text-red-400 text-sm py-8';
    errDiv.textContent = '\uB9AC\uD3EC\uD2B8 \uB85C\uB4DC \uC2E4\uD328: ' + err.message;
    container.appendChild(errDiv);
  }
}

// ── Load date activities (expandable log under report) ──
export async function loadDateActivities(dateStr, container) {
  try {
    var json = await fetchJSON(API + '/activities?target_date=' + dateStr + '&limit=500&market_scope=' + encodeURIComponent(state.currentMarket));
    if (json.data && json.data.length) {
      var section = document.createElement('div');
      section.className = 'mt-4 border-t border-gray-800';
      var toggleBtn = document.createElement('button');
      toggleBtn.className = 'w-full text-center text-gray-500 hover:text-gray-300 text-xs py-3 flex items-center justify-center gap-2 transition';
      var toggleIcon = document.createElement('span');
      toggleIcon.className = 'activity-toggle-icon';
      var chevRight = document.createElement('i');
      chevRight.setAttribute('data-lucide', 'chevron-right');
      chevRight.className = 'w-3 h-3 inline-block';
      toggleIcon.appendChild(chevRight);
      toggleBtn.appendChild(toggleIcon);
      toggleBtn.appendChild(document.createTextNode(' ' + dateStr + ' \uD65C\uB3D9 \uB85C\uADF8 (' + json.data.length + '\uAC74)'));

      var logContainer = document.createElement('div');
      logContainer.className = 'hidden';
      logContainer.style.maxHeight = '600px';
      logContainer.style.overflowY = 'auto';
      json.data.forEach(function (a) { logContainer.appendChild(Feed.createBubble(a)); });

      toggleBtn.onclick = function () {
        var isHidden = logContainer.classList.contains('hidden');
        logContainer.classList.toggle('hidden');
        toggleIcon.replaceChildren();
        var newIcon = document.createElement('i');
        newIcon.setAttribute('data-lucide', isHidden ? 'chevron-down' : 'chevron-right');
        newIcon.className = 'w-3 h-3 inline-block';
        toggleIcon.appendChild(newIcon);
        refreshIcons();
      };
      section.appendChild(toggleBtn);
      section.appendChild(logContainer);
      container.appendChild(section);
    }
  } catch (err) {
    console.error('Activities load error:', err);
  }
}

// ── Create Report Card ──
export function createReportCard(report) {
  var div = document.createElement('div');
  div.className = 'bg-dark-700 rounded-xl p-5 border border-gray-600 mx-2 chat-bubble';
  var winRate = (report.win_count + report.loss_count) > 0
    ? ((report.win_count / (report.win_count + report.loss_count)) * 100).toFixed(1)
    : '-';
  var realizedPnlColor = report.total_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  var unrealizedPnl = report.unrealized_pnl || 0;
  var unrealizedPnlColor = unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400';
  var reportCurrency = _reportCurrency(report);
  var buyCount = report.buy_count || 0;
  var sellCount = report.sell_count || 0;
  var openCount = report.open_position_count || 0;
  var topPicks = '';
  try {
    var picks = JSON.parse(report.top_picks || '[]');
    topPicks = picks.map(function (p) { return typeof p === 'string' ? p : (p.name || '') + '(' + (p.symbol || '') + ')'; }).filter(Boolean).join(', ');
  } catch (e) {}

  // Build the report card using DOM methods
  // Header
  var header = document.createElement('div');
  header.className = 'flex items-center gap-2 text-lg font-bold text-white mb-4';
  var headerIcon = document.createElement('i');
  headerIcon.setAttribute('data-lucide', 'clipboard-list');
  headerIcon.className = 'w-5 h-5';
  header.appendChild(headerIcon);
  header.appendChild(document.createTextNode(' ' + report.report_date + ' \uC77C\uC77C \uB9AC\uD3EC\uD2B8 '));
  var scopeSpan = document.createElement('span');
  scopeSpan.className = 'text-xs text-gray-500';
  scopeSpan.textContent = '(' + (report.market_scope || state.currentMarket) + ' · ' + reportCurrency + ')';
  header.appendChild(scopeSpan);
  div.appendChild(header);

  // Stats grid (3 cols)
  var statsGrid = document.createElement('div');
  statsGrid.className = 'grid grid-cols-3 gap-3 mb-3';
  statsGrid.appendChild(_statCell(String(report.total_cycles), '\uC0AC\uC774\uD074', 'text-blue-400'));
  statsGrid.appendChild(_statCell(String(report.total_analyses), '\uBD84\uC11D', 'text-purple-400'));
  var tradeCell = _statCell('', '\uC8FC\uBB38 (\uBCF4\uC720 ' + openCount + '\uC885\uBAA9)', 'text-yellow-400');
  var tradeValue = tradeCell.querySelector('.text-2xl');
  tradeValue.textContent = '';
  tradeValue.appendChild(document.createTextNode(String(buyCount)));
  var buySuffix = document.createElement('span');
  buySuffix.className = 'text-xs text-gray-500';
  buySuffix.textContent = '\uB9E4\uC218';
  tradeValue.appendChild(buySuffix);
  tradeValue.appendChild(document.createTextNode(' / ' + sellCount));
  var sellSuffix = document.createElement('span');
  sellSuffix.className = 'text-xs text-gray-500';
  sellSuffix.textContent = '\uB9E4\uB3C4';
  tradeValue.appendChild(sellSuffix);
  statsGrid.appendChild(tradeCell);
  div.appendChild(statsGrid);

  // PnL grid (2 cols)
  var pnlGrid = document.createElement('div');
  pnlGrid.className = 'grid grid-cols-2 gap-3 mb-4';
  pnlGrid.appendChild(_pnlCell(formatSignedAmount(report.total_pnl, reportCurrency), '\uC2E4\uD604 \uC190\uC775 (\uC2B9\uB960 ' + winRate + '%)', realizedPnlColor));
  pnlGrid.appendChild(_pnlCell(formatSignedAmount(unrealizedPnl, reportCurrency), '\uBBF8\uC2E4\uD604 \uC190\uC775', unrealizedPnlColor));
  div.appendChild(pnlGrid);

  // Text sections
  if (report.market_summary) _appendTextSection(div, 'edit-3', '\uC624\uB298 \uB9AC\uBDF0', report.market_summary);
  if (report.performance_review) _appendTextSection(div, 'pie-chart', '\uD3EC\uD2B8\uD3F4\uB9AC\uC624 \uC9C4\uB2E8', report.performance_review);
  if (report.lessons_learned) _appendTextSection(div, 'compass', '\uB0B4\uC77C \uC804\uB9DD', report.lessons_learned);
  if (report.next_day_plan) _appendTextSection(div, 'target', '\uC561\uC158 \uD50C\uB79C', report.next_day_plan);
  if (topPicks) _appendTextSection(div, 'crosshairs', '\uAD00\uC2EC \uC885\uBAA9', topPicks);

  return div;
}

function _statCell(value, label, colorClass) {
  var cell = document.createElement('div');
  cell.className = 'bg-dark-900 rounded-lg p-3 text-center';
  var valEl = document.createElement('div');
  valEl.className = 'text-2xl font-bold ' + colorClass;
  valEl.textContent = value;
  var lblEl = document.createElement('div');
  lblEl.className = 'text-xs text-gray-500';
  lblEl.textContent = label;
  cell.appendChild(valEl);
  cell.appendChild(lblEl);
  return cell;
}

function _pnlCell(value, label, colorClass) {
  var cell = document.createElement('div');
  cell.className = 'bg-dark-900 rounded-lg p-3 text-center';
  var valEl = document.createElement('div');
  valEl.className = 'text-xl font-bold ' + colorClass;
  valEl.textContent = value;
  var lblEl = document.createElement('div');
  lblEl.className = 'text-xs text-gray-500';
  lblEl.textContent = label;
  cell.appendChild(valEl);
  cell.appendChild(lblEl);
  return cell;
}

function _appendTextSection(parent, iconName, title, content) {
  var section = document.createElement('div');
  section.className = 'mb-3';
  var heading = document.createElement('div');
  heading.className = 'flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1';
  var icon = document.createElement('i');
  icon.setAttribute('data-lucide', iconName);
  icon.className = 'w-4 h-4';
  heading.appendChild(icon);
  heading.appendChild(document.createTextNode(' ' + title));
  var body = document.createElement('div');
  body.className = 'text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap';
  body.textContent = content;
  section.appendChild(heading);
  section.appendChild(body);
  parent.appendChild(section);
}

// ── Report Comparison ──

export function renderReportComparison(data) {
  var wrapper = document.createElement('div');
  wrapper.className = 'chat-bubble mx-2';

  // Header: "리포트 비교 — {date} ({scope})" + recommendation badge
  var header = document.createElement('div');
  header.className = 'flex items-center gap-2 text-lg font-bold text-white mb-4';
  var headerIcon = document.createElement('i');
  headerIcon.setAttribute('data-lucide', 'git-compare');
  headerIcon.className = 'w-5 h-5';
  header.appendChild(headerIcon);

  var refreshed = data.refreshed || {};
  var dateStr = refreshed.report_date || '';
  var scope = refreshed.market_scope || state.currentMarket;
  header.appendChild(document.createTextNode(' \uB9AC\uD3EC\uD2B8 \uBE44\uAD50 \u2014 ' + escapeHtml(dateStr) + ' (' + escapeHtml(scope) + ' · ' + escapeHtml(_reportCurrency(refreshed)) + ') '));

  var recBadge = document.createElement('span');
  recBadge.className = 'text-xs font-medium px-2 py-0.5 rounded';
  if (data.recommendation === 'refreshed') {
    recBadge.className += ' bg-blue-900/50 text-blue-300';
    recBadge.textContent = '\uC0C8 \uB9AC\uD3EC\uD2B8 \uAD8C\uC7A5';
  } else {
    recBadge.className += ' bg-gray-800 text-gray-400';
    recBadge.textContent = '\uAE30\uC874 \uC720\uC9C0 \uAD8C\uC7A5';
  }
  header.appendChild(recBadge);
  wrapper.appendChild(header);

  // Two-column comparison grid
  var grid = document.createElement('div');
  grid.className = 'report-comparison';

  var existingReport = data.existing;
  var refreshedReport = data.refreshed;
  var comparison = data.comparison || {};

  grid.appendChild(_buildComparisonColumn('\uAE30\uC874 \uB9AC\uD3EC\uD2B8', existingReport, comparison, 'existing', data.recommendation !== 'refreshed'));
  grid.appendChild(_buildComparisonColumn('\uC0C8 \uB9AC\uD3EC\uD2B8', refreshedReport, comparison, 'refreshed', data.recommendation === 'refreshed'));
  wrapper.appendChild(grid);

  // Action buttons
  var actions = document.createElement('div');
  actions.className = 'report-comparison-actions';

  var keepBtn = document.createElement('button');
  keepBtn.className = 'bg-gray-700 text-gray-300 hover:bg-gray-600';
  keepBtn.textContent = '\uAE30\uC874 \uC720\uC9C0';
  keepBtn.addEventListener('click', function () {
    _dismissComparison(wrapper, existingReport);
  });

  var applyBtn = document.createElement('button');
  applyBtn.textContent = '\uC0C8 \uB9AC\uD3EC\uD2B8 \uC801\uC6A9';
  if (data.recommendation === 'refreshed') {
    applyBtn.className = 'bg-blue-600 text-white hover:bg-blue-500';
  } else {
    applyBtn.className = 'bg-gray-600 text-gray-200 hover:bg-gray-500';
  }
  applyBtn.addEventListener('click', function () {
    _confirmRefresh(wrapper, refreshedReport);
  });

  actions.appendChild(keepBtn);
  actions.appendChild(applyBtn);
  wrapper.appendChild(actions);

  return wrapper;
}

function _buildComparisonColumn(title, report, comparison, side, isRecommended) {
  var col = document.createElement('div');
  col.className = 'report-col';
  if (isRecommended) col.className += ' recommended';

  // Column header
  var colHeader = document.createElement('div');
  colHeader.className = 'report-col-header';
  colHeader.textContent = title;
  if (isRecommended) {
    var recTag = document.createElement('span');
    recTag.className = 'text-xs px-1.5 py-0.5 rounded bg-blue-900/50 text-blue-300';
    recTag.textContent = '\uAD8C\uC7A5';
    colHeader.appendChild(recTag);
  }
  col.appendChild(colHeader);

  if (!report) {
    var empty = document.createElement('div');
    empty.className = 'text-xs text-gray-500 py-4 text-center';
    empty.textContent = '\uB9AC\uD3EC\uD2B8 \uC5C6\uC74C';
    col.appendChild(empty);
    return col;
  }

  // Metric rows
  var reportCurrency = _reportCurrency(report);
  var comparisonCurrency = comparison.report_currency || null;
  var metrics = [
    { key: 'total_cycles', label: '\uC0AC\uC774\uD074', value: String(report.total_cycles || 0) },
    { key: 'total_analyses', label: '\uBD84\uC11D', value: String(report.total_analyses || 0) },
    { key: 'buy_count', label: '\uB9E4\uC218 \uC8FC\uBB38', value: String(report.buy_count || 0) },
    { key: 'open_position_count', label: '\uBCF4\uC720\uC885\uBAA9', value: String(report.open_position_count || 0) },
    { key: 'report_currency', label: '\uAE30\uC900 \uD1B5\uD654', value: reportCurrency },
    { key: 'total_pnl', label: '\uC2E4\uD604\uC190\uC775', value: formatSignedAmount(report.total_pnl, reportCurrency) },
    { key: 'unrealized_pnl', label: '\uBBF8\uC2E4\uD604\uC190\uC775', value: formatSignedAmount(report.unrealized_pnl || 0, reportCurrency) },
  ];

  metrics.forEach(function (m) {
    var row = document.createElement('div');
    row.className = 'report-diff-row';

    var label = document.createElement('span');
    label.className = 'text-gray-500';
    label.textContent = m.label;

    var valueWrap = document.createElement('span');
    valueWrap.className = 'text-gray-300';
    valueWrap.textContent = m.value;

    // Check if this field differs and this side is better
    if (comparison[m.key]) {
      var cmp = comparison[m.key];
      if (m.key !== 'report_currency') {
        var sameCurrency = !comparisonCurrency || cmp.existing === cmp.refreshed;
        var existVal = Number(cmp.existing) || 0;
        var refreshVal = Number(cmp.refreshed) || 0;
        if (sameCurrency && existVal !== refreshVal) {
          var betterSide = refreshVal > existVal ? 'refreshed' : 'existing';
          if (betterSide === side) {
            var badge = document.createElement('span');
            badge.className = 'report-diff-badge';
            badge.textContent = '\uAC1C\uC120';
            valueWrap.appendChild(badge);
          }
        }
      }
    }

    row.appendChild(label);
    row.appendChild(valueWrap);
    col.appendChild(row);
  });

  return col;
}

function _reportCurrency(report) {
  return report && report.report_currency ? report.report_currency : 'KRW';
}

function _dismissComparison(wrapperEl, existingReport) {
  var container = document.getElementById('chat-container');
  container.replaceChildren();
  if (existingReport) {
    container.appendChild(createReportCard(existingReport));
    if (existingReport.report_date) loadDateActivities(existingReport.report_date, container);
  } else {
    var msg = document.createElement('div');
    msg.className = 'text-center text-gray-500 text-sm py-8';
    msg.textContent = '\uAE30\uC874 \uB9AC\uD3EC\uD2B8\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4';
    container.appendChild(msg);
  }
  refreshIcons();
}

async function _confirmRefresh(wrapperEl, refreshedReport) {
  try {
    var dateStr = refreshedReport && refreshedReport.report_date;
    var marketScope = refreshedReport && refreshedReport.market_scope
      ? refreshedReport.market_scope
      : state.currentMarket;
    await fetchJSON(API + '/reports/confirm-refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        report_date: dateStr,
        market_scope: marketScope,
        choice: 'refreshed',
      }),
    });
    Toast.show('\uC0C8 \uB9AC\uD3EC\uD2B8 \uC801\uC6A9\uB428', 'success');
    // Reload the report to show the confirmed version
    if (dateStr) {
      loadReport(dateStr);
    } else {
      loadReport('today');
    }
    loadReportList();
  } catch (err) {
    Toast.show('\uB9AC\uD3EC\uD2B8 \uC801\uC6A9 \uC2E4\uD328: ' + err.message, 'error');
  }
}
