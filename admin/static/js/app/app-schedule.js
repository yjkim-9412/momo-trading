// ── app-schedule.js — Schedule timeline and system status (ES module) ──

import { state, currentScope, API } from './app-state.js';
import { fetchJSON, updateBadge, formatTime, refreshIcons } from '../shared/admin-core.js';

// ── Schedule Timeline ──
export async function loadScheduleTimeline() {
  try {
    var json = await fetchJSON(API + '/schedule/timeline?market=' + state.currentMarket);
    var d = json.data;
    if (!d) return;
    renderAdaptivePanel(d.adaptive);
    renderFixedTimeline(d.fixed_jobs);
  } catch (err) {
    console.error('Schedule timeline error:', err);
  }
}

export function renderAdaptivePanel(a) {
  var el = document.getElementById('schedule-adaptive');
  if (!el || !a) return;

  var badgeCls = a.enabled ? 'sched-on' : 'sched-off';
  var badgeText = a.enabled ? 'ON' : 'OFF';

  // Build budget dots
  var budgetContainer = document.createElement('div');
  budgetContainer.className = 'schedule-budget';
  for (var i = 0; i < a.cycles_max; i++) {
    var dot = document.createElement('span');
    dot.className = 'schedule-budget-dot' + (i < a.cycles_used ? ' used' : '');
    budgetContainer.appendChild(dot);
  }
  var budgetLabel = document.createElement('span');
  budgetLabel.className = 'schedule-budget-label';
  budgetLabel.textContent = a.cycles_used + '/' + a.cycles_max + ' \uC0AC\uC6A9';
  budgetContainer.appendChild(budgetLabel);

  // Next scan section
  var nextDiv = document.createElement('div');
  if (!a.enabled) {
    nextDiv.className = 'schedule-exhausted';
    nextDiv.textContent = '\uB3D9\uC801 \uC7AC\uC2A4\uCE94 \uBE44\uD65C\uC131';
  } else if (a.cycles_remaining <= 0 && !a.next_run_at) {
    nextDiv.className = 'schedule-exhausted';
    nextDiv.textContent = '\uC608\uC0B0 \uC18C\uC9C4 \u2014 \uCD94\uAC00 \uC2A4\uCE94 \uC5C6\uC74C';
  } else if (a.next_run_at) {
    nextDiv.className = 'schedule-next-scan';
    var t = new Date(a.next_run_at);
    var timeStr = t.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
    var minLabel = a.next_run_in_minutes != null ? '(' + a.next_run_in_minutes + '\uBD84 \uD6C4)' : '';
    var clockIcon = document.createElement('i');
    clockIcon.setAttribute('data-lucide', 'clock');
    clockIcon.className = 'w-3 h-3 text-gray-500';
    nextDiv.appendChild(clockIcon);
    var nextLabel = document.createElement('span');
    nextLabel.className = 'next-label';
    nextLabel.textContent = '\uB2E4\uC74C \uC2A4\uCE94';
    nextDiv.appendChild(nextLabel);
    var nextTime = document.createElement('span');
    nextTime.className = 'next-time';
    nextTime.textContent = timeStr;
    nextDiv.appendChild(nextTime);
    if (minLabel) {
      var countdown = document.createElement('span');
      countdown.className = 'next-countdown';
      countdown.textContent = minLabel;
      nextDiv.appendChild(countdown);
    }
  } else if (a.last_hint && a.last_hint.action === 'STOP_SESSION') {
    nextDiv.className = 'schedule-exhausted';
    nextDiv.textContent = '\uC138\uC158 \uC885\uB8CC \u2014 ' + (a.last_hint.reason || 'AI \uD310\uB2E8');
  } else {
    nextDiv.className = 'schedule-exhausted';
    nextDiv.textContent = '\uB300\uAE30 \uC911';
  }

  // Hint reason
  var hintDiv = null;
  if (a.last_hint && a.last_hint.reason) {
    hintDiv = document.createElement('div');
    hintDiv.className = 'schedule-hint-reason';
    hintDiv.textContent = a.last_hint.reason;
  }

  // Assemble
  el.replaceChildren();

  // Header row
  var headerRow = document.createElement('div');
  headerRow.className = 'schedule-adaptive-header';
  var titleSpan = document.createElement('span');
  titleSpan.className = 'schedule-adaptive-title';
  var botIcon = document.createElement('i');
  botIcon.setAttribute('data-lucide', 'bot');
  botIcon.className = 'w-3.5 h-3.5';
  titleSpan.appendChild(botIcon);
  titleSpan.appendChild(document.createTextNode(' AI \uB3D9\uC801 \uC7AC\uC2A4\uCE94'));
  var badgeSpan = document.createElement('span');
  badgeSpan.className = 'schedule-adaptive-badge ' + badgeCls;
  badgeSpan.textContent = badgeText;
  headerRow.appendChild(titleSpan);
  headerRow.appendChild(badgeSpan);

  el.appendChild(headerRow);
  el.appendChild(budgetContainer);
  el.appendChild(nextDiv);
  if (hintDiv) el.appendChild(hintDiv);

  refreshIcons();
}

export function renderFixedTimeline(jobs) {
  var el = document.getElementById('schedule-fixed');
  if (!el || !jobs || !jobs.length) return;

  el.replaceChildren();
  jobs.forEach(function (j) {
    var item = document.createElement('div');
    item.className = 'sched-item sched-' + j.status;
    var dot = document.createElement('span');
    dot.className = 'sched-dot';
    var time = document.createElement('span');
    time.className = 'sched-time';
    time.textContent = j.time;
    var label = document.createElement('span');
    label.className = 'sched-label';
    label.textContent = j.name;
    item.appendChild(dot);
    item.appendChild(time);
    item.appendChild(label);
    el.appendChild(item);
  });
}

// ── System Status ──
export async function loadSystemStatus() {
  try {
    var json = await fetchJSON(API + '/system/status?market=' + state.currentMarket);
    var s = json.data;
    if (!s) return;
    updateBadge('badge-trading', s.trading_enabled ? '\uB9E4\uB9E4:ON' : '\uB9E4\uB9E4:OFF', s.trading_enabled ? 'green' : 'red');
    updateBadge('badge-mcp', s.mcp_connected ? 'MCP:ON' : 'MCP:OFF', s.mcp_connected ? 'green' : 'red');

    var statusEl = document.getElementById('sys-status');
    var isHoliday = !!s.market_holiday;
    var session = s.market_session || 'CLOSED';
    var sessionLabel = getSessionLabel(session);
    var marketLabel = isHoliday ? '\uD734\uC7A5 (' + s.market_holiday + ')' : (session === 'CLOSED' ? '\uC7A5\uC678' : sessionLabel);
    var marketColor = s.market_open ? 'bg-green-400' : (isHoliday ? 'bg-yellow-400' : 'bg-gray-500');
    var marketExtra = s.market_open ? '' : ' (\uB2E4\uC74C: ' + (s.next_market_open || '') + ')';
    var tzInfo = s.market_tz && s.market_tz !== 'KST' ? ' (' + s.market_tz + (s.dst_active ? ' \uC368\uBA38\uD0C0\uC784' : '') + ')' : '';

    statusEl.replaceChildren();

    // Market status
    _appendStatusRow(statusEl, marketColor, marketLabel + tzInfo + marketExtra, true);
    // MCP
    _appendStatusRow(statusEl, s.mcp_connected ? 'bg-green-400' : 'bg-red-400', 'MCP: ' + (s.mcp_connected ? '\uC5F0\uACB0' : '\uB04A\uAE40'));
    // Scheduler
    _appendStatusRow(statusEl, s.scheduler_running ? 'bg-green-400' : 'bg-yellow-400', '\uC2A4\uCF00\uC904\uB7EC: ' + (s.scheduler_running ? '\uB3D9\uC791' : '\uC911\uC9C0'));
    // Agent
    _appendStatusRow(statusEl, s.agent_running ? 'bg-green-400' : 'bg-yellow-400', '\uC5D0\uC774\uC804\uD2B8: ' + (s.agent_running ? '\uB3D9\uC791' : '\uC911\uC9C0'));
    // Last cycle
    if (s.last_cycle_time) {
      var lastDiv = document.createElement('div');
      lastDiv.className = 'text-gray-500';
      lastDiv.textContent = '\uB9C8\uC9C0\uB9C9: ' + formatTime(s.last_cycle_time);
      statusEl.appendChild(lastDiv);
    }
    // SSE clients
    var sseDiv = document.createElement('div');
    sseDiv.className = 'text-gray-500';
    sseDiv.textContent = 'SSE: ' + s.sse_clients + '\uBA85';
    statusEl.appendChild(sseDiv);

    // Update market context bar
    updateMarketContextBar(s);

    var scope = currentScope();
    var scopeLabel = scope === 'KRX' ? 'KRX' : 'US';
    var triggerBtn = document.querySelector('[onclick="triggerCycle()"]');
    if (triggerBtn) {
      var action = s.market_open ? '\uB9E4\uB9E4 \uC0AC\uC774\uD074 \uC2E4\uD589' : '\uC7A5\uB9C8\uAC10 \uB9AC\uBDF0 \uC2E4\uD589';
      // Re-build button content via DOM
      triggerBtn.replaceChildren();
      var playIcon = document.createElement('i');
      playIcon.setAttribute('data-lucide', 'play');
      playIcon.className = 'w-4 h-4 fill-current';
      triggerBtn.appendChild(playIcon);
      triggerBtn.appendChild(document.createTextNode(' ' + (state.enabledMarkets.length > 1 ? scopeLabel + ' ' : '') + action));
      refreshIcons();
    }
    var reportBtn = document.querySelector('[onclick="generateReport()"]');
    if (reportBtn && state.enabledMarkets.length > 1) {
      reportBtn.replaceChildren();
      var fileIcon = document.createElement('i');
      fileIcon.setAttribute('data-lucide', 'file-text');
      fileIcon.className = 'w-4 h-4';
      reportBtn.appendChild(fileIcon);
      reportBtn.appendChild(document.createTextNode(' ' + scopeLabel + ' \uB9AC\uD3EC\uD2B8 \uC0DD\uC131'));
      refreshIcons();
    }
  } catch (err) {
    console.error('Status load error:', err);
  }
}

function _appendStatusRow(parent, dotColor, text, isBold) {
  var row = document.createElement('div');
  row.className = 'flex items-center gap-1.5';
  var dot = document.createElement('span');
  dot.className = 'status-dot w-1.5 h-1.5 rounded-full ' + dotColor;
  row.appendChild(dot);
  if (isBold) {
    var strong = document.createElement('strong');
    strong.textContent = text;
    row.appendChild(strong);
  } else {
    row.appendChild(document.createTextNode(text));
  }
  parent.appendChild(row);
}

function getSessionLabel(session) {
  var labels = {
    NXT_PRE: 'NXT \uD504\uB9AC',
    KRX_NXT: '\uC815\uADDC\uC7A5',
    KRX_CLOSE: '\uB3D9\uC2DC\uD638\uAC00',
    NXT_AFTER: 'NXT \uC560\uD504\uD130',
    US_PRE: '\uD504\uB9AC\uB9C8\uCF13',
    US_REGULAR: '\uC815\uADDC\uC7A5',
    US_AFTER: '\uC560\uD504\uD130\uB9C8\uCF13',
    CLOSED: '\uC7A5\uC678',
  };
  return labels[session] || session;
}

function updateMarketContextBar(statusData) {
  if (!statusData) return;
  var isHoliday = !!statusData.market_holiday;
  var session = statusData.market_session || 'CLOSED';
  var sessionLabel = getSessionLabel(session);
  var statusText = isHoliday ? '\uD734\uC7A5' : (session === 'CLOSED' ? '\uC7A5\uC678' : sessionLabel);
  var statusEl = document.getElementById('ctx-market-status');
  if (statusEl) statusEl.textContent = statusText;

  var dateEl = document.getElementById('ctx-trading-date');
  if (dateEl) {
    var today = new Date().toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul' });
    dateEl.textContent = today;
  }

  var lastEl = document.getElementById('ctx-last-cycle');
  if (lastEl) {
    lastEl.textContent = statusData.last_cycle_time
      ? '\uB9C8\uC9C0\uB9C9 \uC0AC\uC774\uD074: ' + formatTime(statusData.last_cycle_time)
      : '';
  }

  // Session schedule pills
  var scheduleEl = document.getElementById('ctx-session-schedule');
  if (scheduleEl && statusData.market_sessions) {
    var isUS = currentScope() !== 'KRX';
    scheduleEl.replaceChildren();
    statusData.market_sessions.forEach(function (s) {
      var active = s.key === session;
      var pill = document.createElement('span');
      pill.className = 'session-pill' + (active ? ' session-active' : '');
      var timeStr = isUS
        ? s.open + '-' + s.close + ' ' + s.tz
        : s.open + '-' + s.close;
      pill.textContent = s.label + ' ' + timeStr;
      if (isUS && s.open_kst && s.close_kst) {
        var kstSpan = document.createElement('span');
        kstSpan.className = 'session-pill-time';
        kstSpan.textContent = ' (' + s.open_kst + '-' + s.close_kst + ' KST)';
        pill.appendChild(kstSpan);
      }
      scheduleEl.appendChild(pill);
    });
  }

  // DST badge
  var dstEl = document.getElementById('ctx-dst-badge');
  if (dstEl) {
    dstEl.replaceChildren();
    if (statusData.dst_active) {
      var dstBadge = document.createElement('span');
      dstBadge.className = 'dst-badge';
      var sunIcon = document.createElement('i');
      sunIcon.setAttribute('data-lucide', 'sun');
      sunIcon.className = 'w-3 h-3';
      dstBadge.appendChild(sunIcon);
      dstBadge.appendChild(document.createTextNode(' \uC368\uBA38\uD0C0\uC784'));
      dstEl.appendChild(dstBadge);
      refreshIcons();
    } else if (statusData.market_tz === 'EST') {
      var estBadge = document.createElement('span');
      estBadge.className = 'dst-badge';
      estBadge.style.background = 'rgba(96,165,250,0.1)';
      estBadge.style.borderColor = 'rgba(96,165,250,0.2)';
      estBadge.style.color = '#93c5fd';
      var moonIcon = document.createElement('i');
      moonIcon.setAttribute('data-lucide', 'moon');
      moonIcon.className = 'w-3 h-3';
      estBadge.appendChild(moonIcon);
      estBadge.appendChild(document.createTextNode(' \uD45C\uC900\uC2DC'));
      dstEl.appendChild(estBadge);
      refreshIcons();
    }
  }
}
