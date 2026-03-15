/**
 * app-reports.js — Report loading and rendering
 *
 * Handles report list navigation, individual report display,
 * date-based activity log loading, and report card creation.
 */
(function () {
  'use strict';

  var App = Admin.App;

  // ── Report List (left sidebar) ──
  App.loadReportList = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/reports?limit=10&market_scope=' + encodeURIComponent(App.currentMarket));
      var listEl = document.getElementById('report-list');
      listEl.replaceChildren();
      if (json.data && json.data.length) {
        json.data.forEach(function (r) {
          var btn = document.createElement('button');
          btn.className = 'w-full text-left px-3 py-1 text-xs text-gray-400 hover:bg-dark-700 rounded';
          btn.textContent = r.report_date + ' (' + (r.market_scope || App.currentMarket) + ')';
          btn.onclick = function () { App.switchToReport(r.report_date); };
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
  };

  App.switchToReport = function (dateStr) {
    App.currentView = 'report';
    App.loadReport(dateStr);
  };

  // ── Load Report ──
  App.loadReport = async function (dateStr) {
    var container = document.getElementById('chat-container');
    container.replaceChildren();
    var loadingDiv = document.createElement('div');
    loadingDiv.className = 'text-center text-gray-500 text-sm py-4';
    loadingDiv.textContent = '\uB9AC\uD3EC\uD2B8 \uBD88\uB7EC\uC624\uB294 \uC911...';
    container.appendChild(loadingDiv);
    App.insertBackToLiveBar(container);
    App.cleanupStockCards();

    try {
      var url = Admin.API + '/reports/latest';
      if (dateStr && dateStr !== 'today') url = Admin.API + '/reports/' + dateStr;
      url += (url.indexOf('?') !== -1 ? '&' : '?') + 'market_scope=' + encodeURIComponent(App.currentMarket);
      var json = await Admin.fetchJSON(url);
      var report = json.data;

      if (!report) {
        container.replaceChildren();
        var noReport = document.createElement('div');
        noReport.className = 'text-center text-gray-500 text-sm py-8';
        noReport.textContent = '\uD574\uB2F9 \uB0A0\uC9DC\uC758 \uB9AC\uD3EC\uD2B8\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4';
        container.appendChild(noReport);
        if (dateStr && dateStr !== 'today') await App.loadDateActivities(dateStr, container);
        return;
      }
      container.replaceChildren();
      container.appendChild(App.createReportCard(report));
      if (report.report_date) await App.loadDateActivities(report.report_date, container);
      Admin.refreshIcons();
    } catch (err) {
      container.replaceChildren();
      var errDiv = document.createElement('div');
      errDiv.className = 'text-center text-red-400 text-sm py-8';
      errDiv.textContent = '\uB9AC\uD3EC\uD2B8 \uB85C\uB4DC \uC2E4\uD328: ' + err.message;
      container.appendChild(errDiv);
    }
  };

  // ── Load date activities (expandable log under report) ──
  App.loadDateActivities = async function (dateStr, container) {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/activities?target_date=' + dateStr + '&limit=500&market_scope=' + encodeURIComponent(App.currentMarket));
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
        json.data.forEach(function (a) { logContainer.appendChild(Admin.Feed.createBubble(a)); });

        toggleBtn.onclick = function () {
          var isHidden = logContainer.classList.contains('hidden');
          logContainer.classList.toggle('hidden');
          toggleIcon.replaceChildren();
          var newIcon = document.createElement('i');
          newIcon.setAttribute('data-lucide', isHidden ? 'chevron-down' : 'chevron-right');
          newIcon.className = 'w-3 h-3 inline-block';
          toggleIcon.appendChild(newIcon);
          Admin.refreshIcons();
        };
        section.appendChild(toggleBtn);
        section.appendChild(logContainer);
        container.appendChild(section);
      }
    } catch (err) {
      console.error('Activities load error:', err);
    }
  };

  // ── Create Report Card ──
  App.createReportCard = function (report) {
    var div = document.createElement('div');
    div.className = 'bg-dark-700 rounded-xl p-5 border border-gray-600 mx-2 chat-bubble';
    var winRate = (report.win_count + report.loss_count) > 0
      ? ((report.win_count / (report.win_count + report.loss_count)) * 100).toFixed(1)
      : '-';
    var realizedPnlColor = report.total_pnl >= 0 ? 'text-green-400' : 'text-red-400';
    var unrealizedPnl = report.unrealized_pnl || 0;
    var unrealizedPnlColor = unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400';
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
    scopeSpan.textContent = '(' + (report.market_scope || App.currentMarket) + ')';
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
    pnlGrid.appendChild(_pnlCell(App.formatSignedAmount(report.total_pnl, 'KRW'), '\uC2E4\uD604 \uC190\uC775 (\uC2B9\uB960 ' + winRate + '%)', realizedPnlColor));
    pnlGrid.appendChild(_pnlCell(App.formatSignedAmount(unrealizedPnl, 'KRW'), '\uBBF8\uC2E4\uD604 \uC190\uC775', unrealizedPnlColor));
    div.appendChild(pnlGrid);

    // Text sections
    if (report.market_summary) _appendTextSection(div, 'edit-3', '\uC624\uB298 \uB9AC\uBDF0', report.market_summary);
    if (report.performance_review) _appendTextSection(div, 'pie-chart', '\uD3EC\uD2B8\uD3F4\uB9AC\uC624 \uC9C4\uB2E8', report.performance_review);
    if (report.lessons_learned) _appendTextSection(div, 'compass', '\uB0B4\uC77C \uC804\uB9DD', report.lessons_learned);
    if (report.next_day_plan) _appendTextSection(div, 'target', '\uC561\uC158 \uD50C\uB79C', report.next_day_plan);
    if (topPicks) _appendTextSection(div, 'crosshairs', '\uAD00\uC2EC \uC885\uBAA9', topPicks);

    return div;
  };

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
})();
