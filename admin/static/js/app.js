/**
 * MOMO Trading Admin Dashboard — SSE + Stock-Grouped Chat UI
 */
const API = '/api/v1/admin';
let currentView = 'live';
let autoScroll = true;
let missedCount = 0;
let accountPollTimer = null;
let currentMarket = 'KRX';
let enabledMarkets = ['KRX'];
let currentLogFilter = 'ALL';
const LLM_AGENT_DEFAULTS = {
  tier1: {
    display_name: '후보 분석 에이전트',
    short_label: '후보 분석',
    description: '차트·시장 컨텍스트를 바탕으로 매수 후보와 목표/손절을 1차 판단',
    icon: 'search',
  },
  tier2: {
    display_name: '최종 검토 에이전트',
    short_label: '최종 검토',
    description: '1차 분석 결과를 리스크·포트폴리오 관점에서 재검증해 주문 승인 여부를 결정',
    icon: 'shield-check',
  },
};
const ADMIN_AGENT_LABELS = {
  TIER1: LLM_AGENT_DEFAULTS.tier1.display_name,
  TIER2: LLM_AGENT_DEFAULTS.tier2.display_name,
};

function refreshIcons() {
  if (typeof lucide !== 'undefined') {
    requestAnimationFrame(() => lucide.createIcons());
  }
}

function formatAdminAgentText(text) {
  if (text == null) return '';

  return String(text)
    .replace(/\[TIER1\]/g, `[${ADMIN_AGENT_LABELS.TIER1}]`)
    .replace(/\[TIER2\]/g, `[${ADMIN_AGENT_LABELS.TIER2}]`)
    .replace(/\bTIER1\b/g, ADMIN_AGENT_LABELS.TIER1)
    .replace(/\bTIER2\b/g, ADMIN_AGENT_LABELS.TIER2)
    .replace(/\bTier 1\b/g, ADMIN_AGENT_LABELS.TIER1)
    .replace(/\bTier1\b/g, ADMIN_AGENT_LABELS.TIER1)
    .replace(/\bTier 2\b/g, ADMIN_AGENT_LABELS.TIER2)
    .replace(/\bTier2\b/g, ADMIN_AGENT_LABELS.TIER2);
}

function transformAdminAgentValue(value) {
  if (typeof value === 'string') return formatAdminAgentText(value);
  if (Array.isArray(value)) return value.map(transformAdminAgentValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, entryValue]) => [key, transformAdminAgentValue(entryValue)])
    );
  }
  return value;
}

// ── Per-market state management ──
function currentScope() {
  return currentMarket === 'KRX' ? 'KRX' : 'US';
}
let marketState = {
  KRX: { stockCards: {}, activityCount: 0, loaded: false, fragment: null, scrollPos: 0 },
  US:  { stockCards: {}, activityCount: 0, loaded: false, fragment: null, scrollPos: 0 },
};
let activityBuffer = { KRX: [], US: [] };
const BUFFER_MAX = 300;

// Convenience accessors for current market's stockCards
function getStockCards() { return marketState[currentScope()].stockCards; }
function getActivityCount() { return marketState[currentScope()].activityCount; }
function setActivityCount(v) { marketState[currentScope()].activityCount = v; }
function incActivityCount() { marketState[currentScope()].activityCount++; }

// Legacy alias — many functions reference this directly
let stockCards = marketState.KRX.stockCards;

// ── Agent Pipeline Monitor State ──
const PIPELINE_STEPS = ['data', 'tier1', 'tier2', 'strategy', 'decision'];
const PIPELINE_LABELS = { data: '조회', tier1: 'Tier1', tier2: 'Tier2', strategy: '전략', decision: '결정' };
let monitorState = {
  KRX: { cycleActive: false, cycleId: null, startedAt: null, scannedCount: 0, analyzedCount: 0, slots: {}, completed: [], lastCycleSummary: null },
  US:  { cycleActive: false, cycleId: null, startedAt: null, scannedCount: 0, analyzedCount: 0, slots: {}, completed: [], lastCycleSummary: null },
};
let monitorUpdateTimer = null;
let monitorElapsedTimer = null;
let monitorExpanded = true;

function getMonitorState() { return monitorState[currentScope()]; }

function scheduleMonitorRender() {
  if (monitorUpdateTimer) return;
  monitorUpdateTimer = setTimeout(() => {
    monitorUpdateTimer = null;
    renderAgentMonitor();
  }, 80);
}

function updateMonitorFromActivity(data) {
  const eventScope = (data.market_scope || 'KRX') === 'KRX' ? 'KRX' : 'US';
  const ms = monitorState[eventScope];
  if (!ms) return;
  const { activity_type, phase, symbol, summary, confidence } = data;

  switch (activity_type) {
    case 'CYCLE':
      if (phase === 'START') {
        ms.cycleActive = true;
        ms.cycleId = data.cycle_id;
        ms.startedAt = Date.now();
        ms.scannedCount = 0;
        ms.analyzedCount = 0;
        ms.slots = {};
        ms.completed = [];
      } else if (phase === 'COMPLETE' || phase === 'ERROR') {
        ms.lastCycleSummary = {
          analyzedCount: ms.analyzedCount,
          scannedCount: ms.scannedCount,
          elapsed: ms.startedAt ? Math.round((Date.now() - ms.startedAt) / 1000) : 0,
          time: new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          error: phase === 'ERROR',
        };
        ms.cycleActive = false;
        ms.slots = {};
      }
      break;
    case 'SCAN':
      if (phase === 'COMPLETE' && data.detail) {
        try {
          const d = typeof data.detail === 'string' ? JSON.parse(data.detail) : data.detail;
          ms.scannedCount = (d.selected || []).length || ms.scannedCount;
        } catch {}
      }
      break;
    case 'TIER1_ANALYSIS':
      if (phase === 'START' && symbol) {
        ms.slots[symbol] = { symbol, name: _extractName(summary, symbol), step: 'tier1', startedAt: Date.now() };
      } else if (phase === 'COMPLETE' && symbol) {
        if (ms.slots[symbol]) ms.slots[symbol].step = 'tier1_done';
        _tryResolveSlot(ms, symbol, data);
      } else if (phase === 'ERROR' && symbol) {
        _finishSlot(ms, symbol, 'error', summary);
      } else if (phase === 'SKIP' && symbol) {
        _finishSlot(ms, symbol, 'skip', summary);
      }
      break;
    case 'TIER2_REVIEW':
      if (symbol && ms.slots[symbol]) {
        ms.slots[symbol].step = phase === 'COMPLETE' ? 'tier2_done' : 'tier2';
      }
      break;
    case 'STRATEGY_EVAL':
      if (symbol && ms.slots[symbol]) {
        ms.slots[symbol].step = 'strategy';
      }
      break;
    case 'RISK_GATE':
      if (phase === 'SKIP' && symbol) {
        _finishSlot(ms, symbol, 'skip', summary);
      }
      break;
    case 'DECISION':
      if ((phase === 'COMPLETE' || phase === 'ERROR') && symbol) {
        const outcome = _detectOutcome(summary, data);
        _finishSlot(ms, symbol, outcome, summary);
      } else if (symbol && ms.slots[symbol]) {
        ms.slots[symbol].step = 'decision';
      }
      break;
    case 'TRADE_RESULT':
      if (symbol) {
        // Update completed badge if already finished
        const existing = ms.completed.find(c => c.symbol === symbol);
        if (existing) {
          const outcome = _detectOutcome(summary, data);
          if (outcome !== 'hold') existing.outcome = outcome;
        }
      }
      break;
  }

  if (eventScope === currentScope()) scheduleMonitorRender();
}

function _extractName(summary, symbol) {
  // Try to extract name from summary like "[삼성전자] ..."
  const m = summary && summary.match(/\[([^\]]+)\]/);
  return m ? m[1] : symbol;
}

function _detectOutcome(summary, data) {
  if (!summary) return 'hold';
  const s = summary.toLowerCase();
  if (s.includes('매수') || s.includes('buy')) return 'buy';
  if (s.includes('매도') || s.includes('sell')) return 'sell';
  if (s.includes('오류') || s.includes('error') || s.includes('실패')) return 'error';
  if (s.includes('스킵') || s.includes('skip') || s.includes('차단')) return 'skip';
  return 'hold';
}

function _tryResolveSlot(ms, symbol, data) {
  // For TIER1 COMPLETE without TIER2 or subsequent steps, check if it's a terminal hold/skip
  const summary = data.summary || '';
  if (summary.includes('HOLD') || summary.includes('관망') || summary.includes('보류')) {
    _finishSlot(ms, symbol, 'hold', summary);
  }
}

function _finishSlot(ms, symbol, outcome, summary) {
  const slot = ms.slots[symbol];
  const elapsed = slot ? Math.round((Date.now() - slot.startedAt) / 1000) : 0;
  ms.completed.push({ symbol, name: slot ? slot.name : symbol, outcome, elapsed });
  delete ms.slots[symbol];
  ms.analyzedCount = ms.completed.length;
}

function handleAgentStateEvent(data) {
  const scope = (data.market_scope || 'KRX') === 'KRX' ? 'KRX' : 'US';
  const ms = monitorState[scope];
  if (data.cycle_active) {
    ms.cycleActive = true;
    ms.cycleId = data.cycle_id;
    ms.startedAt = data.started_at ? new Date(data.started_at).getTime() : Date.now();
    ms.scannedCount = data.scanned_count || 0;
    ms.analyzedCount = data.analyzed_count || 0;
    ms.slots = ms.slots || {};
  } else {
    if (ms.cycleActive) {
      ms.lastCycleSummary = {
        analyzedCount: data.analyzed_count || ms.analyzedCount,
        scannedCount: data.scanned_count || ms.scannedCount,
        elapsed: ms.startedAt ? Math.round((Date.now() - ms.startedAt) / 1000) : 0,
        time: new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      };
    }
    ms.cycleActive = false;
    ms.slots = {};
  }
  if (scope === currentScope()) scheduleMonitorRender();
}

function renderAgentMonitor() {
  const ms = getMonitorState();
  const iconEl = document.getElementById('monitor-status-icon');
  const statusEl = document.getElementById('monitor-status');
  const elapsedEl = document.getElementById('monitor-elapsed');
  const progressWrap = document.getElementById('monitor-progress-wrap');
  const progressBar = document.getElementById('monitor-progress-bar');
  const body = document.getElementById('monitor-body');
  const slotsEl = document.getElementById('monitor-slots');
  const completedWrap = document.getElementById('monitor-completed');
  const completedList = document.getElementById('monitor-completed-list');

  if (!iconEl) return;

  if (ms.cycleActive) {
    iconEl.classList.add('active');
    const scopeLabel = currentScope() === 'KRX' ? '국내' : '해외';
    const total = ms.scannedCount || 1;
    const done = ms.analyzedCount;
    statusEl.textContent = `${scopeLabel} 사이클 진행 중`;
    statusEl.style.color = '#34d399';

    // Progress bar
    progressWrap.style.display = '';
    const pct = Math.min(100, Math.round((done / total) * 100));
    progressBar.style.width = `${pct}%`;

    // Elapsed timer
    if (ms.startedAt) {
      const sec = Math.round((Date.now() - ms.startedAt) / 1000);
      elapsedEl.textContent = `${sec}s`;
      _startElapsedTimer();
    }

    // Body
    if (monitorExpanded) {
      body.style.display = '';
      // Render slots
      const slotSymbols = Object.keys(ms.slots);
      let slotsHTML = '';
      for (const sym of slotSymbols) {
        const slot = ms.slots[sym];
        const stepIdx = PIPELINE_STEPS.indexOf(slot.step.replace('_done', ''));
        const isDone = slot.step.endsWith('_done');
        const slotSec = Math.round((Date.now() - slot.startedAt) / 1000);

        slotsHTML += `<div class="monitor-slot slot-active">
          <div class="monitor-slot-symbol">${_escHtml(slot.symbol)}</div>
          <div class="monitor-slot-name">${_escHtml(slot.name)}</div>
          <div class="pipeline-steps">${_renderPipeline(stepIdx, isDone)}</div>
          <div class="monitor-slot-timer">${slotSec}s · ${PIPELINE_LABELS[slot.step.replace('_done', '')] || slot.step}</div>
        </div>`;
      }
      // Empty slot placeholders
      const emptySlots = Math.max(0, 3 - slotSymbols.length);
      if (slotSymbols.length === 0 && ms.completed.length === 0) {
        slotsHTML += `<div class="monitor-slot" style="opacity:0.3; grid-column: 1/-1; text-align: center;">
          <div class="text-xs text-gray-600">스캔 완료 · 분석 대기 중...</div>
        </div>`;
      }
      slotsEl.innerHTML = slotsHTML;

      // Render completed
      if (ms.completed.length > 0) {
        completedWrap.style.display = '';
        let cHTML = '';
        for (const c of ms.completed) {
          const cls = `badge-${c.outcome}`;
          const outcomeLabel = { buy: '매수', sell: '매도', hold: '관망', error: '오류', skip: '스킵' }[c.outcome] || c.outcome;
          cHTML += `<span class="monitor-completed-badge ${cls}" title="${_escHtml(c.name)} (${c.elapsed}s)">
            ${_escHtml(c.symbol)} <span style="opacity:0.7">${outcomeLabel}</span>
          </span>`;
        }
        completedList.innerHTML = cHTML;
      } else {
        completedWrap.style.display = 'none';
      }
    } else {
      body.style.display = 'none';
    }
  } else {
    // Idle state
    iconEl.classList.remove('active');
    statusEl.style.color = '#6b7280';
    progressWrap.style.display = 'none';
    body.style.display = 'none';
    _stopElapsedTimer();

    if (ms.lastCycleSummary) {
      const s = ms.lastCycleSummary;
      statusEl.textContent = `대기 중`;
      elapsedEl.textContent = `마지막: ${s.time} (${s.scannedCount}종목, ${s.elapsed}s)`;
    } else {
      statusEl.textContent = '대기 중';
      elapsedEl.textContent = '';
    }
  }

  refreshIcons();
}

function _renderPipeline(activeIdx, isDone) {
  let html = '';
  for (let i = 0; i < PIPELINE_STEPS.length; i++) {
    if (i > 0) {
      const connClass = i <= activeIdx ? 'conn-done' : '';
      html += `<div class="pipeline-connector ${connClass}"></div>`;
    }
    let cls = 'pipeline-step';
    if (i < activeIdx || (i === activeIdx && isDone)) cls += ' step-done';
    else if (i === activeIdx) cls += ' step-active';
    html += `<div class="${cls}" title="${PIPELINE_LABELS[PIPELINE_STEPS[i]]}"></div>`;
  }
  return html;
}

function _escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function toggleMonitorExpand() {
  monitorExpanded = !monitorExpanded;
  renderAgentMonitor();
}

function _startElapsedTimer() {
  if (monitorElapsedTimer) return;
  monitorElapsedTimer = setInterval(() => {
    const ms = getMonitorState();
    if (!ms.cycleActive || !ms.startedAt) { _stopElapsedTimer(); return; }
    const sec = Math.round((Date.now() - ms.startedAt) / 1000);
    const el = document.getElementById('monitor-elapsed');
    if (el) el.textContent = `${sec}s`;
    // Also update slot timers
    for (const sym of Object.keys(ms.slots)) {
      const slot = ms.slots[sym];
      const slotSec = Math.round((Date.now() - slot.startedAt) / 1000);
      const timerEl = document.querySelector(`.monitor-slot .monitor-slot-timer`);
      // Batch re-render is cheaper than individual updates for 3 slots
    }
  }, 1000);
}

function _stopElapsedTimer() {
  if (monitorElapsedTimer) { clearInterval(monitorElapsedTimer); monitorElapsedTimer = null; }
}

async function initAgentMonitor() {
  try {
    const res = await fetch(`${API}/agent/state`);
    const json = await res.json();
    if (json.data) {
      for (const scope of ['KRX', 'US']) {
        const d = json.data[scope];
        if (d && d.cycle_active) {
          monitorState[scope].cycleActive = true;
          monitorState[scope].cycleId = d.cycle_id;
          monitorState[scope].startedAt = d.started_at ? new Date(d.started_at).getTime() : Date.now();
          monitorState[scope].scannedCount = d.scanned_count || 0;
          monitorState[scope].analyzedCount = d.analyzed_count || 0;
        }
      }
    }
  } catch (e) {
    console.warn('Agent monitor bootstrap failed:', e);
  }
  renderAgentMonitor();
}

// ── Init ──
document.addEventListener('DOMContentLoaded', async () => {
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
  accountPollTimer = setInterval(loadMarketAccountInfo, 30000);
  setInterval(loadWatchlist, 30000);
  setInterval(loadLLMUsage, 60000);
  setInterval(loadScheduleTimeline, 15000);
});

// ── Scroll to Bottom ──
function scrollToBottom() {
  const container = document.getElementById('chat-container');
  container.scrollTop = container.scrollHeight;
  autoScroll = true;
  missedCount = 0;
  updateScrollBadge();
}

function updateScrollBadge() {
  const badge = document.getElementById('scroll-fab-badge');
  if (!badge) return;
  if (missedCount > 0) {
    badge.textContent = missedCount > 99 ? '99+' : missedCount;
    badge.classList.add('visible');
  } else {
    badge.classList.remove('visible');
  }
}

// ── Toast Notifications ──
function showToast(message, optsOrType = 'info', legacyDuration) {
  // Backward compat: showToast('msg', 'success', 3000)
  const opts = typeof optsOrType === 'string'
    ? { type: optsOrType, duration: legacyDuration }
    : optsOrType;
  const { type = 'info', level, duration, persistent = false, onClick } = opts;
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  const cls = level === 'CRITICAL' ? 'toast-critical' : level === 'HIGH' ? 'toast-high' : `toast-${type}`;
  toast.className = `toast ${cls}`;

  const dur = persistent ? 0 : (duration || (level === 'HIGH' ? 8000 : 3000));

  if (persistent || onClick) {
    toast.innerHTML = `<div class="flex items-center justify-between gap-2"><span class="flex-1">${escapeHtml(message)}</span>${persistent ? '<button class="toast-dismiss text-white/60 hover:text-white ml-2">\u2715</button>' : ''}</div>`;
  } else {
    toast.textContent = message;
  }

  if (onClick) { toast.style.cursor = 'pointer'; toast.addEventListener('click', (e) => { if (!e.target.closest('.toast-dismiss')) onClick(); }); }
  if (persistent) {
    const btn = toast.querySelector('.toast-dismiss');
    if (btn) btn.addEventListener('click', (e) => { e.stopPropagation(); _removeToast(toast); });
  }

  container.prepend(toast);
  while (container.children.length > 5) _removeToast(container.lastChild);

  if (dur > 0) {
    setTimeout(() => toast.classList.add('toast-fade-out'), dur - 300);
    setTimeout(() => _removeToast(toast), dur);
  }
}
function _removeToast(el) { if (el && el.parentNode) el.remove(); }

// ── Importance Classification ──
function classifyImportance(data) {
  const t = data.activity_type;
  const p = data.phase;
  const s = (data.summary || '').toUpperCase();

  if ((t === 'ORDER' || t === 'TRADE_RESULT') && p === 'COMPLETE')
    return { level: 'CRITICAL', title: (s.includes('매도') || s.includes('SELL')) ? '매도 체결' : '매수 체결', body: `${data.symbol || ''} — ${data.summary || ''}` };

  if (t === 'TIER1_ANALYSIS' && p === 'COMPLETE' && (s.includes('BUY') || s.includes('SELL')))
    return { level: 'HIGH', title: s.includes('BUY') ? '매수 신호' : '매도 신호', body: `${data.symbol || ''} — ${data.summary || ''}` };

  if (t === 'TIER2_REVIEW' && p === 'COMPLETE' && s.includes('미승인'))
    return { level: 'HIGH', title: 'TIER2 미승인', body: `${data.symbol || ''} — ${data.summary || ''}` };

  if (t === 'DECISION' && p === 'COMPLETE')
    return { level: 'HIGH', title: '주문 실행', body: `${data.symbol || ''} — ${data.summary || ''}` };

  return { level: 'NONE' };
}

// ── Browser Notification ──
function sendBrowserNotification(info) {
  if (!document.hidden) return;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  try {
    const n = new Notification(info.title, {
      body: info.body,
      tag: `momo-${info.level}-${Date.now()}`,
      requireInteraction: info.level === 'CRITICAL',
    });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) { /* Notification not supported */ }
}
function requestNotificationPermission() {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

// ── Tab Title Flash ──
let _titleFlashInterval = null;
const ORIGINAL_TITLE = document.title || 'MOMO Trading Admin';
function flashTitle(alertText) {
  if (_titleFlashInterval) return;
  let show = true;
  _titleFlashInterval = setInterval(() => {
    document.title = show ? alertText : ORIGINAL_TITLE;
    show = !show;
  }, 1000);
}
function stopTitleFlash() {
  if (_titleFlashInterval) {
    clearInterval(_titleFlashInterval);
    _titleFlashInterval = null;
    document.title = ORIGINAL_TITLE;
  }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) stopTitleFlash(); });

// ── Card Highlight & Navigation ──
function highlightCard(data) {
  if (!data.symbol) return;
  const cards = getStockCards();
  let card = null;
  for (const c of Object.values(cards)) {
    if (c.symbol === data.symbol) { card = c; break; }
  }
  if (!card || !card.element) return;
  card.element.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.element.classList.add('highlighted');
  setTimeout(() => card.element.classList.remove('highlighted'), 3000);
}
function navigateToCard(data) {
  if (!data) return;
  const eventScope = (data.market_scope || 'KRX') === 'KRX' ? 'KRX' : 'US';
  const myScope = currentScope();
  if (eventScope !== myScope) {
    switchMarket(eventScope === 'KRX' ? 'KRX' : 'NASDAQ');
  }
  setTimeout(() => highlightCard(data), eventScope !== myScope ? 500 : 50);
}

// ── HTTP Helper ──
async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// ── SSE Connection ──
let currentEventSource = null;

window.addEventListener('beforeunload', () => {
  if (currentEventSource) currentEventSource.close();
});

function connectSSE() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  const es = new EventSource(`${API}/stream`);
  currentEventSource = es;

  es.onopen = () => {
    setStatus('connected', 'SSE 연결됨');
    updateBadge('badge-sse', '연결', 'green');
    removeSSEDisconnectBanner();
    requestNotificationPermission();
  };

  es.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'connected') return;

      // Agent pipeline monitor events
      if (msg.type === 'agent_state') {
        handleAgentStateEvent(msg.data);
        return;
      }

      if (msg.type === 'activity') {
        const eventScope = (msg.data && msg.data.market_scope) || 'KRX';
        const scopeKey = eventScope === 'KRX' ? 'KRX' : 'US';
        const myScope = currentScope();

        // ⓪ Agent Monitor routing (all markets)
        updateMonitorFromActivity(msg.data);

        // ① Importance classification (all markets)
        const importance = classifyImportance(msg.data);
        if (importance.level !== 'NONE') {
          const scopeLabel = scopeKey === 'KRX' ? '국내' : '해외';
          const fullTitle = `[${scopeLabel}] ${importance.title}`;

          showToast(`${fullTitle}: ${msg.data.symbol || ''} ${msg.data.summary || ''}`, {
            level: importance.level,
            persistent: importance.level === 'CRITICAL',
            onClick: () => navigateToCard(msg.data),
          });

          if (document.hidden) {
            sendBrowserNotification({ ...importance, title: fullTitle });
            flashTitle(`${importance.level === 'CRITICAL' ? '\uD83D\uDD34' : '\uD83D\uDFE1'} ${fullTitle}`);
          }
        }

        // ② Activity feed routing (existing logic)
        if (currentView === 'live') {
          if (scopeKey === myScope) {
            appendActivity(msg.data);
            if (importance.level === 'CRITICAL' || importance.level === 'HIGH') {
              setTimeout(() => highlightCard(msg.data), 100);
            }
          } else {
            const buf = activityBuffer[scopeKey];
            if (buf) {
              buf.push(msg.data);
              if (buf.length > BUFFER_MAX) buf.splice(0, buf.length - BUFFER_MAX);
              updateBackgroundBadge(scopeKey, buf.length);
            }
          }
        }

        // ③ Account refresh on trade events
        if (msg.data && msg.data.phase === 'COMPLETE' &&
            ['DECISION', 'ORDER', 'TRADE_RESULT'].includes(msg.data.activity_type)) {
          if (scopeKey === myScope) setTimeout(loadMarketAccountInfo, 2000);
        }

        // ④ Watchlist refresh on scan/cycle/decision events
        if (msg.data && msg.data.phase === 'COMPLETE' &&
            ['SCAN', 'CYCLE', 'DECISION'].includes(msg.data.activity_type)) {
          if (scopeKey === myScope) setTimeout(loadWatchlist, 1500);
        }
      }
      if (msg.type === 'account_changed') {
        loadMarketAccountInfo();
        loadWatchlist();
      }
    } catch (err) {
      console.error('SSE parse error', err);
    }
  };

  es.onerror = () => {
    setStatus('disconnected', 'SSE 재연결 중...');
    updateBadge('badge-sse', '끊김', 'red');
    showSSEDisconnectBanner();
    setTimeout(() => {
      if (es.readyState === EventSource.CLOSED) connectSSE();
    }, 3000);
    // Re-bootstrap monitor on reconnect
    setTimeout(initAgentMonitor, 4000);
  };
}

// ── Market Switching ──
function switchMarket(market) {
  if (market === currentMarket) return;
  const oldScope = currentScope();

  // 1. Save current feed state
  if (currentView === 'live') {
    saveMarketFeed(oldScope);
  }

  // 2. Pause live timers for old market
  pauseMarketTimers(oldScope);

  currentMarket = market;
  const newScope = currentScope();

  // 3. Point stockCards alias to new market
  stockCards = marketState[newScope].stockCards;

  // 4. Apply workspace theme
  applyWorkspaceTheme();

  // 5. Update header tabs
  const indicator = document.getElementById('market-header-indicator');
  const btns = document.querySelectorAll('.market-header-btn');
  btns.forEach(btn => btn.classList.toggle('active', btn.dataset.market === market));
  if (indicator) indicator.classList.toggle('right', market !== 'KRX');

  // 6. Restore or load new market feed
  if (currentView === 'live') {
    restoreMarketFeed(newScope);
  }

  // 7. Flush SSE buffer for new market
  flushBuffer(newScope);

  // 8. Reset background badge for new market
  updateBackgroundBadge(newScope, 0);

  // 9. Update activity count display
  document.getElementById('activity-count').textContent = `${getActivityCount()}건`;

  // 10. Fade out data, reload account/status/reports, fade in
  document.querySelectorAll('.account-data-transition').forEach(el => el.classList.add('switching'));
  loadMarketAccountInfo().then(() => {
    setTimeout(() => {
      document.querySelectorAll('.account-data-transition').forEach(el => el.classList.remove('switching'));
    }, 60);
  });
  loadWatchlist();
  loadSystemStatus();
  loadReportList();
  loadScheduleTimeline();

  // 11. Re-render agent monitor for new market
  renderAgentMonitor();
}

function setupMarketTabs() {
  const tabsEl = document.getElementById('market-header-tabs');
  const ctxBar = document.getElementById('market-context-bar');
  if (!tabsEl) return;
  if (enabledMarkets.length > 1) {
    tabsEl.classList.remove('hidden');
    if (ctxBar) ctxBar.classList.remove('hidden');
    const btns = document.querySelectorAll('.market-header-btn');
    btns.forEach(btn => btn.classList.toggle('active', btn.dataset.market === currentMarket));
    const indicator = document.getElementById('market-header-indicator');
    if (indicator) indicator.classList.toggle('right', currentMarket !== 'KRX');
  } else {
    tabsEl.classList.add('hidden');
    if (ctxBar) ctxBar.classList.add('hidden');
  }
}

// ── Workspace Theme ──
function applyWorkspaceTheme() {
  const scope = currentScope();
  const body = document.body;
  body.classList.remove('workspace-krx', 'workspace-us');
  body.classList.add(scope === 'KRX' ? 'workspace-krx' : 'workspace-us');

  // Update feed label
  const feedLabel = document.getElementById('feed-market-label');
  if (feedLabel) {
    if (enabledMarkets.length > 1) {
      feedLabel.textContent = scope === 'KRX' ? '— KRX 국내' : '— US 해외';
    } else {
      feedLabel.textContent = '';
    }
  }

  // Update context bar
  const ctxLabel = document.getElementById('ctx-market-label');
  if (ctxLabel) ctxLabel.textContent = scope === 'KRX' ? 'KRX 국내주식' : 'US 해외주식';
}

// ── Feed State Preservation ──
function saveMarketFeed(scope) {
  const container = document.getElementById('chat-container');
  const state = marketState[scope];
  state.scrollPos = container.scrollTop;
  pauseMarketTimers(scope);
  // Detach all children into a fragment
  const frag = document.createDocumentFragment();
  while (container.firstChild) {
    frag.appendChild(container.firstChild);
  }
  state.fragment = frag;
}

function restoreMarketFeed(scope) {
  const container = document.getElementById('chat-container');
  const state = marketState[scope];

  if (state.fragment) {
    // Restore saved DOM
    container.innerHTML = '';
    container.appendChild(state.fragment);
    state.fragment = null;
    requestAnimationFrame(() => { container.scrollTop = state.scrollPos; });
  } else if (!state.loaded) {
    // First visit — load from API
    loadTodayActivities();
  } else {
    // Loaded but empty
    container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">아직 활동 기록이 없습니다</div>';
  }
}

function flushBuffer(scope) {
  const buf = activityBuffer[scope];
  if (!buf || !buf.length) return;

  // Insert divider if many buffered events
  if (buf.length > 5) {
    const container = document.getElementById('chat-container');
    const divider = document.createElement('div');
    divider.className = 'cycle-divider';
    divider.innerHTML = `<span class="text-gray-500">${buf.length}건의 새 활동</span>`;
    container.appendChild(divider);
  }

  buf.forEach(data => appendActivity(data));
  activityBuffer[scope] = [];
}

function pauseMarketTimers(scope) {
  const cards = marketState[scope].stockCards;
  for (const card of Object.values(cards)) {
    if (card.liveTimer) {
      clearInterval(card.liveTimer);
      card.liveTimer = null;
    }
  }
}

function updateBackgroundBadge(scope, count) {
  const badge = document.getElementById(`market-badge-${scope}`);
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

function updateMarketContextBar(statusData) {
  if (!statusData) return;
  const isHoliday = !!statusData.market_holiday;
  const session = statusData.market_session || 'CLOSED';
  const sessionLabel = getSessionLabel(session);
  const statusText = isHoliday ? '휴장' : (session === 'CLOSED' ? '장외' : sessionLabel);
  const statusEl = document.getElementById('ctx-market-status');
  if (statusEl) statusEl.textContent = statusText;

  const dateEl = document.getElementById('ctx-trading-date');
  if (dateEl) {
    const today = new Date().toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul' });
    dateEl.textContent = today;
  }

  const lastEl = document.getElementById('ctx-last-cycle');
  if (lastEl) {
    lastEl.textContent = statusData.last_cycle_time
      ? `마지막 사이클: ${formatTime(statusData.last_cycle_time)}`
      : '';
  }

  // Session schedule pills
  const scheduleEl = document.getElementById('ctx-session-schedule');
  if (scheduleEl && statusData.market_sessions) {
    const isUS = currentScope() !== 'KRX';
    scheduleEl.innerHTML = statusData.market_sessions.map(s => {
      const active = s.key === session;
      const timeStr = isUS
        ? `${s.open}-${s.close} ${s.tz} <span class="session-pill-time">(${s.open_kst}-${s.close_kst} KST)</span>`
        : `${s.open}-${s.close}`;
      return `<span class="session-pill${active ? ' session-active' : ''}">${s.label} ${timeStr}</span>`;
    }).join('');
  }

  // DST badge
  const dstEl = document.getElementById('ctx-dst-badge');
  if (dstEl) {
    if (statusData.dst_active) {
      dstEl.innerHTML = '<span class="dst-badge"><i data-lucide="sun" class="w-3 h-3"></i> 써머타임</span>';
      refreshIcons();
    } else if (statusData.market_tz === 'EST') {
      dstEl.innerHTML = '<span class="dst-badge" style="background:rgba(96,165,250,0.1);border-color:rgba(96,165,250,0.2);color:#93c5fd"><i data-lucide="moon" class="w-3 h-3"></i> 표준시</span>';
      refreshIcons();
    } else {
      dstEl.innerHTML = '';
    }
  }
}

function getSessionLabel(session) {
  const labels = {
    NXT_PRE: 'NXT 프리',
    KRX_NXT: '정규장',
    KRX_CLOSE: '동시호가',
    NXT_AFTER: 'NXT 애프터',
    US_PRE: '프리마켓',
    US_REGULAR: '정규장',
    US_AFTER: '애프터마켓',
    CLOSED: '장외',
  };
  return labels[session] || session;
}

// ── Account Info ──
async function loadMarketAccountInfo() {
  const market = currentMarket;
  const marketParam = market === 'KRX' ? '' : `?market=${market}`;
  try {
    const json = await fetchJSON(`${API}/account/overview${marketParam}`);
    const overview = json.data || {};
    renderBalance(overview.balance, market);
    renderHoldings(overview.holdings || [], market);
    renderPendingOrders(overview.pending_orders || [], market);
  } catch (err) {
    console.error('Account info error:', err);
    const el = document.getElementById('account-info');
    if (el) el.textContent = '조회 실패';
  }
}

function renderBalanceFailure(el, badgeEl, message) {
  if (badgeEl) badgeEl.classList.add('hidden');
  const row = document.createElement('div');
  row.className = 'text-sm text-red-400';
  row.textContent = message || '계좌 조회 실패';
  el.replaceChildren(row);
}

function hasMeaningfulDiff(left, right, threshold = 1) {
  return Math.abs(Number(left || 0) - Number(right || 0)) >= threshold;
}

function shouldShowRawPnl(data) {
  if (!data || data.pnl_source !== 'HOLDINGS_SUM') return false;
  return hasMeaningfulDiff(data.raw_total_pnl, data.total_pnl, 1)
    || hasMeaningfulDiff(data.raw_total_pnl_rate, data.total_pnl_rate, 0.01);
}

function renderBalance(data, market) {
  const el = document.getElementById('account-info');
  const badgeEl = document.getElementById('cash-source-badge');
  if (!el || !data) {
    if (el) el.textContent = '계좌 미연결';
    if (badgeEl) badgeEl.classList.add('hidden');
    return;
  }
  if (data.is_valid === false) {
    renderBalanceFailure(el, badgeEl, data.status_message);
    return;
  }
  const isUS = market !== 'KRX';
  const exchangeRate = Number(data.exchange_rate_to_krw || 0);
  const effectiveCash = Number(data.effective_cash ?? data.cash ?? 0);
  const rawCash = Number(data.raw_cash ?? data.cash ?? 0);
  const totalPnl = Number(data.total_pnl ?? 0);
  const totalPnlRate = Number(data.total_pnl_rate ?? 0);
  const rawTotalPnl = Number(data.raw_total_pnl ?? totalPnl);
  const rawTotalPnlRate = Number(data.raw_total_pnl_rate ?? totalPnlRate);
  const pnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
  // Cash source badge (US only)
  if (badgeEl) {
    if (isUS) {
      badgeEl.classList.remove('hidden');
      badgeEl.textContent = data.cash_source === 'TOTAL_ASSET_PROXY' ? '총자산 프록시' : '브로커 현금';
      badgeEl.className = data.cash_source === 'TOTAL_ASSET_PROXY'
        ? 'px-2 py-0.5 rounded-full text-[11px] bg-sky-500/15 text-sky-300'
        : 'px-2 py-0.5 rounded-full text-[11px] bg-slate-800 text-gray-300';
    } else {
      badgeEl.classList.add('hidden');
    }
  }
  el.replaceChildren();
  if (isUS) {
    _renderBalanceUS(el, data, effectiveCash, rawCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor, exchangeRate);
  } else {
    _renderBalanceKRX(el, data, effectiveCash, rawCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor);
  }
}
function _renderBalanceUS(el, data, effectiveCash, rawCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor, exchangeRate) {
  const totalAssetUsd = convertKrwToUsd(data.total_asset, exchangeRate);
  const effectiveCashUsd = convertKrwToUsd(effectiveCash, exchangeRate);
  const rawCashUsd = convertKrwToUsd(rawCash, exchangeRate);
  const stockValueUsd = convertKrwToUsd(data.stock_value, exchangeRate);
  const rows = [];
  rows.push(_dualRow('총자산', formatAmount(totalAssetUsd, 'USD'), formatAmount(data.total_asset, 'KRW'), 'text-white font-medium'));
  rows.push(_dualRow('실주문 기준 현금', formatAmount(effectiveCashUsd, 'USD'), formatAmount(effectiveCash, 'KRW'), 'text-sky-300 font-medium'));
  if (Math.abs(effectiveCash - rawCash) >= 1)
    rows.push(_dualRow('브로커 현금', formatAmount(rawCashUsd, 'USD'), formatAmount(rawCash, 'KRW'), 'text-gray-300'));
  rows.push(_dualRow('주식 평가', formatAmount(stockValueUsd, 'USD'), formatAmount(data.stock_value, 'KRW'), 'text-gray-200'));
  rows.push(_dualRow('손익', formatSignedAmount(totalPnlRate, 'PCT'), formatSignedAmount(totalPnl, 'KRW'), pnlColor));
  if (shouldShowRawPnl(data))
    rows.push(_dualRow('브로커 요약 손익', formatSignedAmount(rawTotalPnlRate, 'PCT'), formatSignedAmount(rawTotalPnl, 'KRW'), 'text-gray-400'));
  rows.push(_noteRow(`환율 ${exchangeRate ? exchangeRate.toFixed(2) : '-'} KRW/USD`));
  if (data.pnl_source === 'HOLDINGS_SUM') rows.push(_noteRow('모의투자 손익은 보유종목 기준으로 재계산합니다.'));
  if (data.status_message) rows.push(_noteRow(data.status_message, 'text-gray-500'));
  rows.forEach(r => el.appendChild(r));
}
function _renderBalanceKRX(el, data, effectiveCash, rawCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor) {
  const cashRatio = data.total_asset > 0 ? ((effectiveCash / data.total_asset) * 100).toFixed(1) : '0.0';
  const rows = [];
  rows.push(_singleRow('총자산', formatKRW(data.total_asset), 'text-white font-medium'));
  rows.push(_singleRow('실주문 기준 현금', `${formatAmount(effectiveCash, 'KRW')} (${cashRatio}%)`));
  if (Math.abs(effectiveCash - rawCash) >= 1) rows.push(_singleRow('브로커 현금', formatAmount(rawCash, 'KRW')));
  rows.push(_singleRow('주식', formatKRW(data.stock_value)));
  const pnlText = `${totalPnl >= 0 ? '+' : ''}${formatKRW(totalPnl)} (${totalPnlRate >= 0 ? '+' : ''}${totalPnlRate.toFixed(2)}%)`;
  rows.push(_singleRow('손익', pnlText, pnlColor));
  if (shouldShowRawPnl(data))
    rows.push(_singleRow('브로커 요약 손익', `${formatSignedAmount(rawTotalPnl, 'KRW')} (${formatSignedAmount(rawTotalPnlRate, 'PCT')})`, 'text-gray-500'));
  if (data.cash_source === 'TOTAL_ASSET_PROXY') rows.push(_noteRow('총자산 - 주식평가액으로 주문가능 현금을 추정합니다.'));
  if (data.pnl_source === 'HOLDINGS_SUM') rows.push(_noteRow('손익은 보유종목 기준으로 재계산합니다.'));
  if (data.status_message) rows.push(_noteRow(data.status_message, 'text-gray-500'));
  rows.forEach(r => el.appendChild(r));
}
function _singleRow(label, value, valueClass) {
  const row = document.createElement('div');
  row.className = 'flex justify-between';
  const lbl = document.createElement('span');
  lbl.className = 'text-gray-400';
  lbl.textContent = label;
  const val = document.createElement('span');
  val.className = valueClass || '';
  val.textContent = value;
  row.appendChild(lbl);
  row.appendChild(val);
  return row;
}
function _dualRow(label, primary, secondary, primaryClass) {
  const row = document.createElement('div');
  row.className = 'flex justify-between gap-3';
  const lbl = document.createElement('span');
  lbl.className = 'text-gray-500';
  lbl.textContent = label;
  const right = document.createElement('div');
  right.className = 'text-right';
  const p = document.createElement('div');
  p.className = primaryClass || '';
  p.textContent = primary;
  const s = document.createElement('div');
  s.className = 'text-gray-500 text-[11px]';
  s.textContent = secondary;
  right.appendChild(p);
  right.appendChild(s);
  row.appendChild(lbl);
  row.appendChild(right);
  return row;
}
function _noteRow(text, cls) {
  const row = document.createElement('div');
  row.className = cls || 'text-[11px] leading-4 text-sky-300';
  row.textContent = text;
  return row;
}

function renderHoldings(data, market) {
  const el = document.getElementById('holdings-info');
  const countEl = document.getElementById('holdings-count');
  const sectionEl = document.getElementById('holdings-section');
  if (!el) return;
  if (!data || !data.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    return;
  }
  if (sectionEl) sectionEl.style.display = '';
  if (countEl) countEl.textContent = `${data.length}종목`;
  const isUS = market !== 'KRX';
  el.replaceChildren();
  data.forEach(h => {
    const pnlColor = h.pnl_rate >= 0 ? 'text-green-400' : 'text-red-400';
    const currency = h.currency || (isUS ? 'USD' : 'KRW');
    const evalAmt = h.current_price * h.quantity;
    const card = document.createElement('div');
    if (isUS) {
      card.className = 'sidebar-card sidebar-card-us space-y-1';
      _buildUSHoldingCard(card, h, pnlColor, currency, evalAmt);
    } else {
      card.className = 'sidebar-card space-y-0.5';
      _buildKRXHoldingCard(card, h, pnlColor, currency, evalAmt);
    }
    el.appendChild(card);
  });
}
function _buildUSHoldingCard(card, h, pnlColor, currency, evalAmt) {
  const evalAmtKrw = evalAmt * (h.exchange_rate_to_krw || 0);
  const pnlKrw = h.pnl * (h.exchange_rate_to_krw || 0);
  // Row 1: name + pnl rate
  const r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center gap-2';
  const nameBlock = document.createElement('div');
  nameBlock.className = 'min-w-0';
  const nameEl = document.createElement('div');
  nameEl.className = 'text-gray-100 font-medium truncate';
  nameEl.title = h.symbol;
  nameEl.textContent = h.name;
  const subEl = document.createElement('div');
  subEl.className = 'text-[11px] text-gray-500';
  subEl.textContent = `${h.symbol} \u00b7 ${h.quantity}주`;
  nameBlock.appendChild(nameEl);
  nameBlock.appendChild(subEl);
  const rateEl = document.createElement('span');
  rateEl.className = `${pnlColor} font-medium`;
  rateEl.textContent = formatSignedAmount(h.pnl_rate, 'PCT');
  r1.appendChild(nameBlock);
  r1.appendChild(rateEl);
  card.appendChild(r1);
  // Row 2: avg + current
  const r2 = _flexRow(`평단 ${formatAmount(h.avg_buy_price, currency)}`, `현재 ${formatAmount(h.current_price, currency)}`, 'text-gray-400');
  card.appendChild(r2);
  // Row 3: eval
  card.appendChild(_flexRow(`평가 ${formatAmount(evalAmt, currency)}`, formatAmount(evalAmtKrw, 'KRW'), 'text-gray-500'));
  // Row 4: pnl
  const r4 = document.createElement('div');
  r4.className = 'flex justify-between text-gray-500';
  const p1 = document.createElement('span');
  p1.className = pnlColor;
  p1.textContent = formatSignedAmount(h.pnl, currency);
  const p2 = document.createElement('span');
  p2.className = pnlColor;
  p2.textContent = formatSignedAmount(pnlKrw, 'KRW');
  r4.appendChild(p1);
  r4.appendChild(p2);
  card.appendChild(r4);
}
function _buildKRXHoldingCard(card, h, pnlColor, currency, evalAmt) {
  // Row 1: name + pnl rate
  const r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center';
  const nameEl = document.createElement('span');
  nameEl.className = 'text-gray-200 font-medium truncate';
  nameEl.title = h.symbol;
  nameEl.textContent = h.name;
  const rateEl = document.createElement('span');
  rateEl.className = `${pnlColor} font-medium`;
  rateEl.textContent = `${h.pnl_rate >= 0 ? '+' : ''}${h.pnl_rate.toFixed(2)}%`;
  r1.appendChild(nameEl);
  r1.appendChild(rateEl);
  card.appendChild(r1);
  // Row 2
  card.appendChild(_flexRow(`${h.quantity}주 | 평단 ${formatAmount(h.avg_buy_price, currency)}`, `현재 ${formatAmount(h.current_price, currency)}`, 'text-gray-500'));
  // Row 3
  const r3 = document.createElement('div');
  r3.className = 'flex justify-between text-gray-500';
  const evalEl = document.createElement('span');
  evalEl.textContent = `평가 ${formatAmount(evalAmt, currency)}`;
  if (currency !== 'KRW') {
    const sec = document.createElement('span');
    sec.className = 'text-gray-600 ml-1';
    sec.textContent = formatAmount(evalAmt * (h.exchange_rate_to_krw || 0), 'KRW');
    evalEl.appendChild(sec);
  }
  const pnlEl = document.createElement('span');
  pnlEl.className = pnlColor;
  pnlEl.textContent = formatSignedAmount(h.pnl, currency);
  if (currency !== 'KRW') {
    const sec2 = document.createElement('span');
    sec2.className = 'text-gray-600 ml-1';
    sec2.textContent = formatSignedAmount(h.pnl * (h.exchange_rate_to_krw || 0), 'KRW');
    pnlEl.appendChild(sec2);
  }
  r3.appendChild(evalEl);
  r3.appendChild(pnlEl);
  card.appendChild(r3);
}
function _flexRow(left, right, cls) {
  const row = document.createElement('div');
  row.className = `flex justify-between ${cls || ''}`;
  const l = document.createElement('span');
  l.textContent = left;
  const r = document.createElement('span');
  r.textContent = right;
  row.appendChild(l);
  row.appendChild(r);
  return row;
}

function renderPendingOrders(data, market) {
  const el = document.getElementById('pending-orders-info');
  const countEl = document.getElementById('pending-count');
  const sectionEl = document.getElementById('pending-section');
  if (!el) return;
  if (!data || !data.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    return;
  }
  if (sectionEl) sectionEl.style.display = '';
  const isUS = market !== 'KRX';
  const defaultCurrency = isUS ? 'USD' : 'KRW';
  const totalAmt = data.reduce((s, o) => s + o.order_price * o.remaining_qty, 0);
  if (countEl) countEl.textContent = `${data.length}건 (${formatAmount(totalAmt, data[0]?.currency || defaultCurrency)})`;
  el.replaceChildren();
  data.forEach(o => {
    const sideColor = o.side === '매수' ? 'text-red-400' : 'text-blue-400';
    const currency = o.currency || defaultCurrency;
    const orderAmt = o.order_price * o.remaining_qty;
    const timeStr = o.order_time
      ? `${o.order_time.slice(0, 2)}:${o.order_time.slice(2, 4)}:${o.order_time.slice(4, 6)}` : '';
    const card = document.createElement('div');
    if (isUS) {
      card.className = 'sidebar-card sidebar-card-pending-us space-y-1';
      _buildUSPendingCard(card, o, sideColor, currency, orderAmt, timeStr);
    } else {
      card.className = 'sidebar-card sidebar-card-pending space-y-0.5';
      _buildKRXPendingCard(card, o, sideColor, currency, orderAmt, timeStr);
    }
    el.appendChild(card);
  });
}
function _buildUSPendingCard(card, o, sideColor, currency, orderAmt, timeStr) {
  const orderAmtKrw = orderAmt * (o.exchange_rate_to_krw || 0);
  // Row 1: name + side
  const r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center gap-2';
  const nameBlock = document.createElement('div');
  nameBlock.className = 'min-w-0';
  const nameEl = document.createElement('div');
  nameEl.className = 'text-gray-100 font-medium truncate';
  nameEl.title = o.symbol;
  nameEl.textContent = o.name || o.symbol;
  const subEl = document.createElement('div');
  subEl.className = 'text-[11px] text-gray-500';
  subEl.textContent = o.symbol;
  nameBlock.appendChild(nameEl);
  nameBlock.appendChild(subEl);
  const sideEl = document.createElement('span');
  sideEl.className = `${sideColor} text-xs font-medium`;
  sideEl.textContent = o.side;
  r1.appendChild(nameBlock);
  r1.appendChild(sideEl);
  card.appendChild(r1);
  card.appendChild(_flexRow(`미체결 ${o.remaining_qty}주 / ${o.order_qty}주`, formatAmount(o.order_price, currency), 'text-gray-400'));
  card.appendChild(_flexRow(`${formatAmount(orderAmt, currency)} \u00b7 ${formatAmount(orderAmtKrw, 'KRW')}`, timeStr, 'text-gray-500'));
}
function _buildKRXPendingCard(card, o, sideColor, currency, orderAmt, timeStr) {
  // Row 1: name + side badge
  const r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center';
  const nameEl = document.createElement('span');
  nameEl.className = 'text-gray-200 font-medium truncate';
  nameEl.title = o.symbol;
  nameEl.textContent = o.name;
  const sideEl = document.createElement('span');
  sideEl.className = `${sideColor} font-medium text-xs px-1.5 py-0.5 rounded ${o.side === '매수' ? 'bg-red-900/30' : 'bg-blue-900/30'}`;
  sideEl.textContent = o.side;
  r1.appendChild(nameEl);
  r1.appendChild(sideEl);
  card.appendChild(r1);
  card.appendChild(_flexRow(`미체결 ${o.remaining_qty}주 / ${o.order_qty}주`, formatAmount(o.order_price, currency), 'text-gray-500'));
  // Row 3: amount + time
  const r3Left = formatAmount(orderAmt, currency);
  const converted = currency !== 'KRW' ? ` ${formatAmount(orderAmt * (o.exchange_rate_to_krw || 0), 'KRW')}` : '';
  card.appendChild(_flexRow(r3Left + converted, timeStr, 'text-gray-500'));
}

// ── AI Watchlist ──
async function loadWatchlist() {
  const market = currentMarket;
  const marketParam = market === 'KRX' ? '' : `?market=${market}`;
  try {
    const json = await fetchJSON(`${API}/watchlist${marketParam}`);
    renderWatchlist(json.data);
  } catch (err) {
    console.error('Watchlist load error:', err);
  }
}

function renderWatchlist(data) {
  const el = document.getElementById('watchlist-info');
  const countEl = document.getElementById('watchlist-count');
  const sectionEl = document.getElementById('watchlist-section');
  const dotEl = document.getElementById('watchlist-stream-dot');
  if (!el) return;

  const symbols = (data && data.symbols) || [];
  const stream = data && data.stream_status;

  if (!symbols.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    return;
  }

  if (sectionEl) sectionEl.style.display = '';
  if (countEl) countEl.textContent = `${symbols.length}종목`;
  if (dotEl && stream) {
    dotEl.className = stream.connected
      ? 'w-1.5 h-1.5 rounded-full bg-green-400 status-dot'
      : 'w-1.5 h-1.5 rounded-full bg-red-400';
  }

  el.replaceChildren();

  // WS gauge bar
  if (stream) {
    const bar = document.createElement('div');
    bar.className = 'watchlist-stream-bar';
    const pct = stream.subscription_limit > 0
      ? Math.round((stream.subscription_count / stream.subscription_limit) * 100) : 0;
    bar.innerHTML = `<span class="ws-gauge"><span>WS</span><span class="ws-gauge-track"><span class="ws-gauge-fill" style="width:${pct}%"></span></span><span>${stream.subscription_count}/${stream.subscription_limit}</span></span>`
      + `<span>${stream.connected ? '연결됨' : '끊김'}</span>`;
    el.appendChild(bar);
  }

  const isUS = currentMarket !== 'KRX';

  symbols.forEach(s => {
    const card = document.createElement('div');
    card.className = `watchlist-card ${s.is_holding ? 'wl-holding' : 'wl-watching'}`;

    // Header: symbol + name + badge
    const header = document.createElement('div');
    header.className = 'wl-header';
    const left = document.createElement('div');
    left.className = 'flex items-center gap-1.5 min-w-0';
    const symEl = document.createElement('span');
    symEl.className = 'wl-symbol';
    symEl.textContent = s.symbol;
    left.appendChild(symEl);
    if (s.name) {
      const nameEl = document.createElement('span');
      nameEl.className = 'wl-name';
      nameEl.textContent = s.name;
      nameEl.title = s.name;
      left.appendChild(nameEl);
    }
    const badge = document.createElement('span');
    if (s.is_holding) {
      badge.className = 'wl-badge wl-badge-holding';
      badge.textContent = '보유';
    } else if (!s.is_subscribed) {
      badge.className = 'wl-badge wl-badge-nosub';
      badge.textContent = '대기';
    } else {
      badge.className = 'wl-badge wl-badge-watching';
      badge.textContent = '감시';
    }
    header.appendChild(left);
    header.appendChild(badge);
    card.appendChild(header);

    // Thresholds grid
    const th = s.thresholds;
    if (th) {
      const grid = document.createElement('div');
      grid.className = 'wl-thresholds';
      const fmtPrice = (v) => {
        if (!v || v <= 0) return '-';
        return isUS ? `$${Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`
          : Number(v).toLocaleString('ko-KR') + '원';
      };
      const items = [];
      if (th.surge_pct != null) items.push({ label: '급등', value: `+${th.surge_pct}%`, cls: 'wl-th-val-surge' });
      if (th.drop_pct != null) items.push({ label: '급락', value: `${th.drop_pct}%`, cls: 'wl-th-val-drop' });
      if (th.volume_spike_ratio) items.push({ label: '거래량', value: `x${th.volume_spike_ratio}`, cls: '' });
      if (th.trailing_stop_pct > 0) items.push({ label: 'Trail', value: `${th.trailing_stop_pct}%`, cls: '' });
      if (th.stop_loss > 0) items.push({ label: 'SL', value: fmtPrice(th.stop_loss), cls: 'wl-th-val-sl' });
      if (th.take_profit > 0) items.push({ label: 'TP', value: fmtPrice(th.take_profit), cls: 'wl-th-val-tp' });
      items.forEach(item => {
        const cell = document.createElement('div');
        cell.className = 'wl-th';
        cell.innerHTML = `<span class="wl-th-label">${item.label}</span><span class="${item.cls}">${item.value}</span>`;
        grid.appendChild(cell);
      });
      card.appendChild(grid);
    } else {
      const tag = document.createElement('div');
      tag.className = 'wl-default-tag';
      tag.textContent = '기본 임계값';
      card.appendChild(tag);
    }

    el.appendChild(card);
  });

  refreshIcons();
}

function toggleSettings() {
  const body = document.getElementById('settings-body');
  const arrow = document.getElementById('settings-arrow');
  const toggleBtn = document.getElementById('settings-toggle-btn');
  if (!body) return;
  const isHidden = body.classList.contains('hidden');
  body.classList.toggle('hidden');
  if (arrow) arrow.style.transform = isHidden ? 'rotate(0deg)' : 'rotate(-90deg)';
  if (toggleBtn) toggleBtn.setAttribute('aria-expanded', String(isHidden));
}

function truncateNumber(value, digits = 0) {
  const factor = 10 ** digits;
  if (value >= 0) return Math.floor(value * factor) / factor;
  return Math.ceil(value * factor) / factor;
}

function formatKRW(amount) {
  if (amount == null || Number.isNaN(Number(amount))) return '-';
  const value = Number(amount);
  if (Math.abs(value) >= 100000000) return truncateNumber(value / 100000000, 1).toFixed(1) + '억';
  if (Math.abs(value) >= 10000) return truncateNumber(value / 10000, 0).toFixed(0) + '만';
  return Math.trunc(value).toLocaleString() + '원';
}

function formatAmount(amount, currency = 'KRW') {
  if (amount == null || Number.isNaN(Number(amount))) return '-';
  if (currency === 'PCT') {
    const value = Number(amount);
    return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
  }
  if (currency === 'USD') {
    return `${Number(amount).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} USD`;
  }
  return formatKRW(Number(amount));
}

function formatSignedAmount(amount, currency = 'KRW') {
  if (amount == null || Number.isNaN(Number(amount))) return '-';
  if (currency === 'PCT') {
    return formatAmount(amount, 'PCT');
  }
  const prefix = Number(amount) >= 0 ? '+' : '';
  return `${prefix}${formatAmount(amount, currency)}`;
}

function convertKrwToUsd(amount, exchangeRate) {
  const rate = Number(exchangeRate || 0);
  if (!rate) return null;
  return Number(amount) / rate;
}

// ══════════════════════════════════════════════════════════
// ── Chat Rendering: Stock-Grouped View ──
// ══════════════════════════════════════════════════════════

/**
 * 활동 1건 추가 — 종목별 카드로 라우팅
 */
function appendActivity(data) {
  const container = document.getElementById('chat-container');

  // Remove placeholder
  if (container.children.length === 1 && container.children[0].classList.contains('text-center')) {
    container.innerHTML = '';
  }

  const symbol = data.symbol;
  const isCycleActivity = data.activity_type === 'CYCLE';
  const isDailyPlan = data.activity_type === 'DAILY_PLAN';
  const isLLMCall = data.activity_type === 'LLM_CALL';

  // Non-symbol activities → inline (cycle dividers, daily plan, events without symbol)
  if (!symbol || isCycleActivity || isDailyPlan) {
    if (isCycleActivity && data.phase === 'START') {
      const divider = createCycleDivider(data, true);
      container.appendChild(divider);
    } else if (isCycleActivity && (data.phase === 'COMPLETE' || data.phase === 'ERROR')) {
      // Remove matching START divider spinner
      const startKey = `cycle-start-${data.cycle_id}`;
      const existing = container.querySelector(`[data-cycle-start="${startKey}"]`);
      if (existing) {
        const spinner = existing.querySelector('.progress-spinner');
        if (spinner) spinner.remove();
        existing.querySelector('.cycle-text').textContent += ' → 완료';
      }
      container.appendChild(createCycleDivider(data, false));
    } else if (isLLMCall && !symbol) {
      // LLM calls without symbol → inline
      container.appendChild(createBubble(data));
    } else {
      container.appendChild(createBubble(data));
    }
  } else {
    // Symbol-specific → route to stock card
    const cards = getStockCards();
    const cardKey = `${data.cycle_id || 'ev'}:${symbol}`;
    let card = cards[cardKey];

    // 정확한 키 매칭 실패 시 → 같은 종목의 진행 중인 카드에 합류
    if (!card) {
      for (const [key, existing] of Object.entries(cards)) {
        if (key.endsWith(':' + symbol) && (!existing.outcome || existing.outcome === 'progress')) {
          card = existing;
          cards[cardKey] = card;  // alias 등록
          break;
        }
      }
    }

    if (!card) {
      // LLM_CALL은 서브 단계 → 카드 생성 X, 기존 카드에만 합류
      if (isLLMCall) return;
      card = createStockCard(symbol, data);
      cards[cardKey] = card;
      container.appendChild(card.element);
    }
    addStepToCard(card, data);
    updateCardHeader(card);
  }

  incActivityCount();
  document.getElementById('activity-count').textContent = `${getActivityCount()}건`;

  if (!autoScroll) {
    missedCount++;
    updateScrollBadge();
  }

  if (autoScroll) {
    container.scrollTop = container.scrollHeight;
  }
  refreshIcons();
}

/**
 * 사이클 구분선 생성
 */
function createCycleDivider(data, isStart) {
  const div = document.createElement('div');
  div.className = isStart ? 'cycle-divider cycle-start' : 'cycle-divider cycle-end';
  const summary = formatAdminAgentText(data.summary);
  if (isStart) {
    div.setAttribute('data-cycle-start', `cycle-start-${data.cycle_id}`);
    div.innerHTML = `<span class="progress-spinner"></span><span class="cycle-text">${escapeHtml(summary)}</span>`;
  } else {
    const time = formatTime(data.created_at);
    const elapsed = data.execution_time_ms ? ` (${(data.execution_time_ms / 1000).toFixed(1)}초)` : '';
    div.innerHTML = `<span>${escapeHtml(summary)}${elapsed}</span><span class="text-gray-500">${time}</span>`;
  }
  return div;
}

// ── Product Context Helpers ──
function extractProductContext(data) {
  if (!data.detail) return null;
  try {
    const obj = typeof data.detail === 'string' ? JSON.parse(data.detail) : data.detail;
    return obj.product_context || null;
  } catch { return null; }
}
function renderProductBadge(pc) {
  if (!pc) return '';
  const mult = pc.leverage_multiplier || 1;
  const signed = pc.signed_exposure || mult;
  const label = (pc.is_inverse ? '' : '+') + signed + 'x';
  const restricted = pc.restricted_product;
  if (pc.is_inverse) {
    return `<span class="product-badge product-inverse${restricted ? ' product-restricted' : ''}">${label}</span>`;
  } else if (pc.is_leveraged || mult > 1) {
    return `<span class="product-badge product-leveraged${restricted ? ' product-restricted' : ''}">${label}</span>`;
  }
  return '';
}
function renderProductStrip(pc) {
  if (!pc) return '';
  const mult = pc.leverage_multiplier || 1;
  const signed = pc.signed_exposure || mult;
  const label = (pc.is_inverse ? '' : '+') + signed + 'x';
  const typeLabel = pc.product_type ? pc.product_type.replace(/_/g, ' ') : '';
  const source = pc.classification_source || '';
  const restricted = pc.restricted_product;
  const cls = restricted ? 'product-strip-restricted' : 'product-strip';
  return `<div class="${cls} mb-2"><span class="font-medium">${label}</span><span class="opacity-75">${typeLabel}</span>${restricted ? '<span class="product-restricted-label">제한 상품</span>' : ''}<span class="opacity-50">${source}</span></div>`;
}

/**
 * 종목 카드 생성
 */
function createStockCard(symbol, firstActivity) {
  const el = document.createElement('div');
  el.className = 'stock-card outcome-progress';

  // Extract stock name from summary: [종목명] or [심볼]
  const nameMatch = (firstActivity.summary || '').match(/\[([^\]]+)\]/);
  let stockName = nameMatch ? nameMatch[1] : symbol;
  // 방어: TIER 라벨이 추출된 경우 symbol로 fallback
  if (/^TIER\d/i.test(stockName)) stockName = symbol;

  const header = document.createElement('div');
  header.className = 'stock-card-header';
  header.innerHTML = `
    <i data-lucide="bar-chart-2" class="w-4 h-4 text-gray-400 shrink-0"></i>
    <span class="text-sm font-medium text-white flex-1 truncate">
      ${escapeHtml(stockName)} <span class="text-gray-500 text-xs">${escapeHtml(symbol)}</span>
    </span>
    <span class="stock-product-badge"></span>
    <span class="stock-outcome flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-purple-900/40 text-purple-300">
      <span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:2px"></span>분석 중
    </span>
    <span class="stock-elapsed text-xs text-gray-400"></span>
    <span class="stock-expand text-gray-500 text-xs transition-transform" style="transform:rotate(-90deg)"><i data-lucide="chevron-down" class="w-4 h-4"></i></span>
  `;
  header.onclick = () => toggleCardBody(card);

  // Body
  const body = document.createElement('div');
  body.className = 'stock-card-body'; // default: collapsed

  const steps = document.createElement('div');
  steps.className = 'stock-card-steps';
  body.appendChild(steps);

  el.appendChild(header);
  el.appendChild(body);

  const card = {
    element: el,
    headerEl: header,
    bodyEl: body,
    stepsEl: steps,
    activities: [],
    symbol: symbol,
    stockName: stockName,
    outcome: null,       // BUY, SELL, HOLD, ERROR
    productContext: null,
    confidence: null,
    totalElapsed: 0,
    isOpen: false,
    startTime: Date.now(),
    liveTimer: null,
  };

  // Start live elapsed timer
  card.liveTimer = setInterval(() => {
    if (card.outcome && card.outcome !== 'progress') {
      clearInterval(card.liveTimer);
      card.liveTimer = null;
      return;
    }
    const elapsed = ((Date.now() - card.startTime) / 1000).toFixed(0);
    const elapsedEl = card.headerEl.querySelector('.stock-elapsed');
    if (elapsedEl) elapsedEl.textContent = `${elapsed}초`;
  }, 1000);

  return card;
}

/**
 * 카드에 활동 스텝 추가
 */
function addStepToCard(card, data) {
  card.activities.push(data);

  // Extract product_context from detail (first occurrence wins)
  if (!card.productContext) {
    const pc = extractProductContext(data);
    if (pc) {
      card.productContext = pc;
      const badgeEl = card.headerEl.querySelector('.stock-product-badge');
      if (badgeEl) badgeEl.innerHTML = renderProductBadge(pc);
    }
  }

  const progressKey = getProgressKey(data);

  // START → compact progress indicator
  if (data.phase === 'START') {
    const step = document.createElement('div');
    step.className = 'stock-step';
    step.setAttribute('data-progress-key', progressKey);
    const time = formatTime(data.created_at);
    const label = formatAdminAgentText((data.summary || '').replace(/시작$/, '').trim());
    step.innerHTML = `
      <span class="text-xs text-gray-500 shrink-0 w-14">${time}</span>
      <span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px"></span>
      <span class="text-xs text-gray-400">${escapeHtml(label)}...</span>
    `;
    card.stepsEl.appendChild(step);
    return;
  }

  // COMPLETE/ERROR → remove matching START spinner
  if (data.phase === 'COMPLETE' || data.phase === 'ERROR') {
    const existing = card.stepsEl.querySelector(`[data-progress-key="${progressKey}"]`);
    if (existing) existing.remove();
  }

  // Create step element
  const step = document.createElement('div');
  step.className = 'stock-step';
  const time = formatTime(data.created_at);
  const typeColor = getTypeColor(data.activity_type);
  const elapsed = data.execution_time_ms ? `${(data.execution_time_ms / 1000).toFixed(1)}초` : '';

  let html = `
    <span class="text-xs text-gray-500 shrink-0 w-14">${time}</span>
    <div class="flex-1 min-w-0">
      <div class="text-xs">${escapeHtml(formatAdminAgentText(data.summary))}</div>`;

  // Meta line
  const meta = [];
  if (data.llm_provider) meta.push(`<span class="text-${typeColor}-400">${data.llm_provider}</span>`);
  if (elapsed) meta.push(elapsed);
  if (data.confidence != null) {
    const pct = Math.round(data.confidence * 100);
    const confColor = pct >= 70 ? 'text-green-400' : pct >= 40 ? 'text-yellow-400' : 'text-red-400';
    meta.push(`<span class="${confColor} font-medium">신뢰도 ${pct}%</span>`);
  }
  if (meta.length) {
    html += `<div class="text-xs text-gray-400 mt-0.5">${meta.join(' · ')}</div>`;
  }

  // Detail (expandable)
  if (data.detail) {
    const detailId = 'sd-' + Math.random().toString(36).substr(2, 6);
    const isLLMCall = data.activity_type === 'LLM_CALL';
    html += `
      <button onclick="event.stopPropagation(); toggleDetail('${detailId}')" class="text-xs text-gray-500 hover:text-gray-300 mt-0.5 flex items-center gap-1">
        ${isLLMCall ? '<i data-lucide="message-square" class="w-3 h-3"></i> LLM 대화' : '<i data-lucide="chevron-down" class="w-3 h-3"></i> 상세'}
      </button>
      <div id="${detailId}" class="detail-content mt-1 text-xs bg-dark-900/50 rounded p-2 text-gray-400">
        ${renderProductStrip(extractProductContext(data))}${isLLMCall ? formatLLMConversation(data.detail) : `<div class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">${formatDetail(data.detail, data.activity_type)}</div>`}
      </div>`;
  }

  // Error
  if (data.error_message) {
    html += `<div class="text-xs text-red-400 mt-0.5">${escapeHtml(formatAdminAgentText(data.error_message))}</div>`;
  }

  html += '</div>';
  step.innerHTML = html;
  card.stepsEl.appendChild(step);
}

/**
 * 카드 헤더 업데이트 (최신 활동 기반)
 */
function updateCardHeader(card) {
  const acts = card.activities;
  let outcome = 'progress';
  let outcomeText = '<span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:2px"></span>분석 중';
  let outcomeBg = 'bg-purple-900/40 text-purple-300';
  let totalMs = 0;

  for (const a of acts) {
    if (a.execution_time_ms) totalMs += a.execution_time_ms;

    // Error
    if (a.phase === 'ERROR' || a.error_message) {
      outcome = 'error';
      outcomeText = '<i data-lucide="x-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 오류';
      outcomeBg = 'bg-yellow-900/40 text-yellow-300';
    }

    // SKIP (데이터 부족, 리스크 차단 등) → HOLD 처리
    if (a.phase === 'SKIP' && outcome !== 'error') {
      outcome = 'hold';
      outcomeText = '<i data-lucide="skip-forward" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 스킵';
      outcomeBg = 'bg-gray-700/60 text-gray-400';
    }

    // Tier1 result — 방향 결정
    if (a.activity_type === 'TIER1_ANALYSIS' && a.phase === 'COMPLETE') {
      const summ = a.summary || '';
      if (summ.includes('HOLD') || summ.includes('실패')) {
        outcome = 'hold';
        outcomeText = summ.includes('실패') ? '<i data-lucide="alert-triangle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 분석 실패' : '<i data-lucide="pause-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> HOLD';
        outcomeBg = 'bg-gray-700/60 text-gray-400';
      } else if (summ.includes('BUY')) {
        outcome = 'buy';
        outcomeText = '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매수';
        outcomeBg = 'bg-red-900/40 text-red-300';
      } else if (summ.includes('SELL')) {
        outcome = 'sell';
        outcomeText = '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매도';
        outcomeBg = 'bg-blue-900/40 text-blue-300';
      }
    }

    // Tier2 — 미승인만 뒤집음, 승인은 기존 방향 유지
    if (a.activity_type === 'TIER2_REVIEW' && a.phase === 'COMPLETE') {
      const summ = a.summary || '';
      if (summ.includes('미승인')) {
        outcome = outcome !== 'error' ? 'hold' : outcome;
        outcomeText = '<i data-lucide="minus-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 미승인';
        outcomeBg = 'bg-gray-700/60 text-gray-400';
      }
    }

    // Strategy eval — HOLD/스킵
    if (a.activity_type === 'STRATEGY_EVAL' && a.phase === 'COMPLETE') {
      const summ = a.summary || '';
      if ((summ.includes('HOLD') || summ.includes('스킵')) && outcome !== 'error') {
        outcome = 'hold';
        outcomeText = '<i data-lucide="pause-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> HOLD';
        outcomeBg = 'bg-gray-700/60 text-gray-400';
      }
    }

    // 주문 실행/체결 — 방향 유지, 상태만 갱신
    if (a.activity_type === 'DECISION' || a.activity_type === 'ORDER') {
      const summ = a.summary || '';
      const isSell = outcome === 'sell' || summ.includes('SELL') || summ.includes('매도');
      if (a.phase === 'COMPLETE' && (summ.includes('주문 접수') || summ.includes('체결'))) {
        outcome = isSell ? 'sell' : 'buy';
        outcomeText = isSell ? '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매도 완료' : '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매수 완료';
        outcomeBg = isSell ? 'bg-blue-900/40 text-blue-300' : 'bg-red-900/40 text-red-300';
      } else if (summ.includes('주문 실행')) {
        // 주문 접수 전 — 방향만 표시
        if (outcome !== 'buy' && outcome !== 'sell') {
          outcome = isSell ? 'sell' : 'buy';
          outcomeText = isSell ? '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매도' : '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매수';
          outcomeBg = isSell ? 'bg-blue-900/40 text-blue-300' : 'bg-red-900/40 text-red-300';
        }
      }
    }

    // Confidence
    if (a.confidence != null) {
      card.confidence = a.confidence;
    }
  }

  card.outcome = outcome;
  card.totalElapsed = totalMs;

  // Update outcome badge
  const outcomeEl = card.headerEl.querySelector('.stock-outcome');
  if (outcomeEl) {
    outcomeEl.className = `stock-outcome flex items-center gap-1.5 text-xs px-2 py-0.5 rounded ${outcomeBg}`;
    outcomeEl.innerHTML = outcomeText;
  }

  // Update elapsed — when done, stop live timer and show final time
  if (outcome !== 'progress') {
    if (card.liveTimer) {
      clearInterval(card.liveTimer);
      card.liveTimer = null;
    }
    const elapsedEl = card.headerEl.querySelector('.stock-elapsed');
    if (elapsedEl && totalMs > 0) {
      elapsedEl.textContent = `${(totalMs / 1000).toFixed(1)}초`;
    }
  }

  // Update card border color
  card.element.className = `stock-card outcome-${outcome}`;

  // Apply log filter visually
  applyFilterToCard(card);
}

/**
 * 로그 필터 적용 (개별 카드)
 */
function applyFilterToCard(card) {
  if (currentLogFilter === 'ALL') {
    card.element.style.display = '';
  } else if (currentLogFilter === 'SIGNAL') {
    if (['buy', 'sell', 'hold'].includes(card.outcome)) {
      card.element.style.display = '';
    } else {
      card.element.style.display = 'none';
    }
  }
}

/**
 * 로그 필터 변경 (전체 적용)
 */
function setLogFilter(filter) {
  if (currentLogFilter === filter) return;
  currentLogFilter = filter;
  
  // Update buttons
  document.querySelectorAll('.log-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });

  // Apply to all tracked cards
  for (const card of Object.values(getStockCards())) {
    applyFilterToCard(card);
  }
  
  // Also apply to legacy bubbles if needed (though mostly cards)
  document.querySelectorAll('.chat-bubble').forEach(el => {
     if (filter === 'SIGNAL') {
       el.style.display = el.textContent.includes('매수') || el.textContent.includes('매도') || el.textContent.includes('BUY') || el.textContent.includes('SELL') ? '' : 'none';
     } else {
       el.style.display = '';
     }
  });

  // Scroll to bottom after filter change
  if (autoScroll) {
    const container = document.getElementById('chat-container');
    container.scrollTop = container.scrollHeight;
  }
}

/**
 * 카드 바디 토글
 */
function toggleCardBody(card) {
  card.isOpen = !card.isOpen;
  card.bodyEl.classList.toggle('open', card.isOpen);
  const arrow = card.headerEl.querySelector('.stock-expand');
  if (arrow) arrow.style.transform = card.isOpen ? 'rotate(0deg)' : 'rotate(-90deg)';
}

// ══════════════════════════════════════════════════════════
// ── Legacy Bubble (for non-grouped activities) ──
// ══════════════════════════════════════════════════════════

function createBubble(data) {
  const div = document.createElement('div');
  div.className = 'chat-bubble';

  const time = formatTime(data.created_at);
  const typeColor = getTypeColor(data.activity_type);

  let html = `
    <div class="flex items-start gap-2 px-3 py-1.5 rounded-lg hover:bg-dark-700/50 transition group">
      <span class="text-xs text-gray-500 mt-0.5 shrink-0 w-14">${time}</span>
      <div class="flex-1 min-w-0">
        <div class="text-sm whitespace-pre-wrap">${escapeHtml(formatAdminAgentText(data.summary))}</div>`;

  const meta = [];
  if (data.llm_provider) meta.push(`<span class="text-${typeColor}-400">${data.llm_provider}</span>`);
  if (data.execution_time_ms) meta.push(`${(data.execution_time_ms / 1000).toFixed(1)}초`);
  if (data.confidence != null) {
    const pct = Math.round(data.confidence * 100);
    meta.push(`신뢰도 ${pct}%`);
  }
  if (meta.length) {
    html += `<div class="flex items-center gap-3 mt-0.5 text-xs text-gray-500">${meta.join(' | ')}</div>`;
  }

  if (data.detail) {
    const detailId = 'detail-' + (data.id || Math.random().toString(36).substr(2, 6));
    const isLLMCall = data.activity_type === 'LLM_CALL';
    html += `
      <button onclick="toggleDetail('${detailId}')" class="text-xs text-gray-500 hover:text-gray-300 mt-1 flex items-center gap-1">
        ${isLLMCall ? '<i data-lucide="message-square" class="w-3 h-3"></i> LLM 대화 보기' : '<i data-lucide="chevron-down" class="w-3 h-3"></i> 상세 보기'}
      </button>
      <div id="${detailId}" class="detail-content mt-1 text-xs bg-dark-900 rounded p-2 text-gray-400">
        ${isLLMCall ? formatLLMConversation(data.detail) : `<div class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">${formatDetail(data.detail, data.activity_type)}</div>`}
      </div>`;
  }

  if (data.error_message) {
    html += `<div class="text-xs text-red-400 mt-1">${escapeHtml(formatAdminAgentText(data.error_message))}</div>`;
  }

  html += `</div></div>`;
  div.innerHTML = html;
  return div;
}

function toggleDetail(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('open');
  // Update aria-expanded on the trigger button
  const btn = el.previousElementSibling;
  if (btn && btn.tagName === 'BUTTON') {
    btn.setAttribute('aria-expanded', String(el.classList.contains('open')));
  }
}

// ── View Switching ──
function switchView(view) {
  const wasLive = currentView === 'live';
  // Save feed state if leaving live view
  if (wasLive && view !== 'live') {
    saveMarketFeed(currentScope());
  }
  currentView = view;
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.className = 'nav-btn w-full text-left px-3 py-2 rounded-lg text-sm text-gray-400 hover:bg-dark-700';
  });
  const activeBtn = document.getElementById(`nav-${view}`);
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
}

function switchToReport(dateStr) {
  currentView = 'report';
  loadReport(dateStr);
}

// ── Data Loading ──
async function loadTodayActivities() {
  const scope = currentScope();
  const container = document.getElementById('chat-container');
  renderPlaceholder(container, 'loading', '불러오는 중...');
  // Clear card tracking for current market
  cleanupStockCards();

  try {
    const json = await fetchJSON(`${API}/activities?limit=500&market_scope=${encodeURIComponent(scope)}`);
    container.innerHTML = '';
    setActivityCount(0);

    if (json.data && json.data.length) {
      // Pre-process: filter resolved STARTs
      const activities = filterResolvedStarts(json.data);
      activities.forEach(a => appendActivity(a));

      // History load: stop all timers and finalize stuck cards
      const cards = getStockCards();
      for (const card of Object.values(cards)) {
        if (card.liveTimer) {
          clearInterval(card.liveTimer);
          card.liveTimer = null;
        }
        // 히스토리 로드 후 여전히 progress면 → 종료된 분석으로 처리
        if (card.outcome === 'progress') {
          card.outcome = 'hold';
          const outcomeEl = card.headerEl.querySelector('.stock-outcome');
          if (outcomeEl) {
            outcomeEl.className = 'stock-outcome flex items-center gap-1.5 text-xs px-2 py-0.5 rounded bg-gray-700/60 text-gray-400';
            outcomeEl.innerHTML = '<i data-lucide="check-circle" class="w-3 h-3"></i> 완료';
          }
          card.element.className = 'stock-card outcome-hold';
        }
      }

      requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
      });
    } else {
      renderPlaceholder(container, 'empty', '아직 활동 기록이 없습니다');
    }
    marketState[scope].loaded = true;
    refreshIcons();
  } catch (err) {
    renderPlaceholder(container, 'error', `로드 실패: ${err.message}`);
  }
}

function filterResolvedStarts(activities) {
  const resolved = new Set();
  activities.forEach(a => {
    if (a.phase === 'COMPLETE' || a.phase === 'ERROR') {
      resolved.add(getProgressKey(a));
    }
  });
  return activities.filter(a => {
    if (a.phase === 'START' && resolved.has(getProgressKey(a))) return false;
    return true;
  });
}

function getProgressKey(data) {
  const match = (data.summary || '').match(/\[([^\]]+)\]/);
  const symbol = match ? match[1] : '';
  return `${data.activity_type}:${symbol}`;
}

// ── Clear Chat ──
function clearChat() {
  if (!confirm('화면을 비울까요? (DB는 유지됩니다)')) return;
  const scope = currentScope();
  const container = document.getElementById('chat-container');
  container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">화면을 비웠습니다. 새 활동이 들어오면 여기에 표시됩니다.</div>';
  setActivityCount(0);
  document.getElementById('activity-count').textContent = '0건';
  cleanupStockCards();
  // Also clear any saved fragment
  marketState[scope].fragment = null;
}

function cleanupStockCards() {
  const scope = currentScope();
  const cards = marketState[scope].stockCards;
  for (const card of Object.values(cards)) {
    if (card.liveTimer) clearInterval(card.liveTimer);
  }
  marketState[scope].stockCards = {};
  stockCards = marketState[scope].stockCards;
}

// ── Reports ──
async function loadReport(dateStr) {
  const container = document.getElementById('chat-container');
  container.innerHTML = '<div class="text-center text-gray-500 text-sm py-4">리포트 불러오는 중...</div>';
  insertBackToLiveBar(container);
  cleanupStockCards();

  try {
    let url = `${API}/reports/latest`;
    if (dateStr && dateStr !== 'today') url = `${API}/reports/${dateStr}`;
    url += `${url.includes('?') ? '&' : '?'}market_scope=${encodeURIComponent(currentMarket)}`;
    const json = await fetchJSON(url);
    const report = json.data;

    if (!report) {
      container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">해당 날짜의 리포트가 없습니다</div>';
      if (dateStr && dateStr !== 'today') await loadDateActivities(dateStr, container);
      return;
    }
    container.innerHTML = '';
    container.appendChild(createReportCard(report));
    if (report.report_date) await loadDateActivities(report.report_date, container);
    refreshIcons();
  } catch (err) {
    container.innerHTML = `<div class="text-center text-red-400 text-sm py-8">리포트 로드 실패: ${err.message}</div>`;
  }
}

async function loadDateActivities(dateStr, container) {
  try {
    const json = await fetchJSON(`${API}/activities?target_date=${dateStr}&limit=500&market_scope=${encodeURIComponent(currentMarket)}`);
    if (json.data && json.data.length) {
      const section = document.createElement('div');
      section.className = 'mt-4 border-t border-gray-800';
      const toggleBtn = document.createElement('button');
      toggleBtn.className = 'w-full text-center text-gray-500 hover:text-gray-300 text-xs py-3 flex items-center justify-center gap-2 transition';
      toggleBtn.innerHTML = `<span class="activity-toggle-icon"><i data-lucide="chevron-right" class="w-3 h-3 inline-block"></i></span> ${dateStr} 활동 로그 (${json.data.length}건)`;
      const logContainer = document.createElement('div');
      logContainer.className = 'hidden';
      logContainer.style.maxHeight = '600px';
      logContainer.style.overflowY = 'auto';
      json.data.forEach(a => logContainer.appendChild(createBubble(a)));
      toggleBtn.onclick = () => {
        const isHidden = logContainer.classList.contains('hidden');
        logContainer.classList.toggle('hidden');
        toggleBtn.querySelector('.activity-toggle-icon').innerHTML = isHidden ? '<i data-lucide="chevron-down" class="w-3 h-3 inline-block"></i>' : '<i data-lucide="chevron-right" class="w-3 h-3 inline-block"></i>';
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

function createReportCard(report) {
  const div = document.createElement('div');
  div.className = 'bg-dark-700 rounded-xl p-5 border border-gray-600 mx-2 chat-bubble';
  const winRate = (report.win_count + report.loss_count) > 0
    ? ((report.win_count / (report.win_count + report.loss_count)) * 100).toFixed(1)
    : '-';
  const realizedPnlColor = report.total_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const unrealizedPnl = report.unrealized_pnl || 0;
  const unrealizedPnlColor = unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const buyCount = report.buy_count || 0;
  const sellCount = report.sell_count || 0;
  const openCount = report.open_position_count || 0;
  let topPicks = '';
  try {
    const picks = JSON.parse(report.top_picks || '[]');
    topPicks = picks.map(p => typeof p === 'string' ? p : `${p.name || ''}(${p.symbol || ''})`).filter(Boolean).join(', ');
  } catch(e) {}

  div.innerHTML = `
    <div class="flex items-center gap-2 text-lg font-bold text-white mb-4"><i data-lucide="clipboard-list" class="w-5 h-5"></i> ${report.report_date} 일일 리포트 <span class="text-xs text-gray-500">(${report.market_scope || currentMarket})</span></div>
    <div class="grid grid-cols-3 gap-3 mb-3">
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-2xl font-bold text-blue-400">${report.total_cycles}</div>
        <div class="text-xs text-gray-500">사이클</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-2xl font-bold text-purple-400">${report.total_analyses}</div>
        <div class="text-xs text-gray-500">분석</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-2xl font-bold text-yellow-400">${buyCount}<span class="text-xs text-gray-500">매수</span> / ${sellCount}<span class="text-xs text-gray-500">매도</span></div>
        <div class="text-xs text-gray-500">주문 (보유 ${openCount}종목)</div>
      </div>
    </div>
    <div class="grid grid-cols-2 gap-3 mb-4">
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold ${realizedPnlColor}">${formatSignedAmount(report.total_pnl, 'KRW')}</div>
        <div class="text-xs text-gray-500">실현 손익 (승률 ${winRate}%)</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold ${unrealizedPnlColor}">${formatSignedAmount(unrealizedPnl, 'KRW')}</div>
        <div class="text-xs text-gray-500">미실현 손익</div>
      </div>
    </div>
    ${report.market_summary ? `
    <div class="mb-3">
      <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="edit-3" class="w-4 h-4"></i> 오늘 리뷰</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.market_summary)}</div>
    </div>` : ''}
    ${report.performance_review ? `
    <div class="mb-3">
      <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="pie-chart" class="w-4 h-4"></i> 포트폴리오 진단</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.performance_review)}</div>
    </div>` : ''}
    ${report.lessons_learned ? `
    <div class="mb-3">
      <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="compass" class="w-4 h-4"></i> 내일 전망</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.lessons_learned)}</div>
    </div>` : ''}
    ${report.next_day_plan ? `
    <div class="mb-3">
      <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="target" class="w-4 h-4"></i> 액션 플랜</div>
      <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${escapeHtml(report.next_day_plan)}</div>
    </div>` : ''}
    ${topPicks ? `
    <div class="mb-2">
      <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1"><i data-lucide="crosshairs" class="w-4 h-4"></i> 관심 종목</div>
      <div class="text-xs text-gray-400 bg-dark-900 rounded p-2">${escapeHtml(topPicks)}</div>
    </div>` : ''}`;
  return div;
}

// ── Settings ──
async function loadSettings() {
  try {
    const json = await fetchJSON(`${API}/settings`);
    const s = json.data;
    if (!s) return;
    document.getElementById('set-trading').checked = s.TRADING_ENABLED;
    document.getElementById('set-mode').value = s.AUTONOMY_MODE;
    const riskEl = document.getElementById('set-risk-appetite');
    if (riskEl && s.RISK_APPETITE) riskEl.value = s.RISK_APPETITE;
    updateBadge('badge-trading', s.TRADING_ENABLED ? '매매:ON' : '매매:OFF', s.TRADING_ENABLED ? 'green' : 'red');
    updateBadge('badge-mode', s.AUTONOMY_MODE, 'purple');
    // Market tabs setup
    if (s.ENABLED_MARKET_GROUPS && s.ENABLED_MARKET_GROUPS.length) {
      enabledMarkets = s.ENABLED_MARKET_GROUPS;
      if (!enabledMarkets.includes(currentMarket)) currentMarket = enabledMarkets[0];
    }
    // Ensure stockCards alias points to current scope
    stockCards = marketState[currentScope()].stockCards;
    setupMarketTabs();
  } catch (err) {
    console.error('Settings load error:', err);
  }
}

async function updateSetting(key, value) {
  const controls = document.querySelectorAll('#settings-body input, #settings-body select');
  controls.forEach(c => c.disabled = true);
  try {
    await fetchJSON(`${API}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
    showToast('설정 저장됨', 'success');
    await loadSettings();
    loadSystemStatus();
  } catch (err) {
    console.error('Setting update error:', err);
    showToast(`설정 저장 실패: ${err.message}`, 'error');
    await loadSettings(); // UI 롤백
  } finally {
    controls.forEach(c => c.disabled = false);
  }
}

// ── LLM Status ──
async function loadLLMStatus() {
  try {
    const json = await fetchJSON(`${API}/llm/status`);
    const s = json.data;
    if (!s) return;
    const providerId = s.selected_provider || (s.tier1 && s.tier1.provider);
    const providerLabel = s.provider_name || formatProviderLabel(providerId);
    renderLLMAgentSummary(s, providerId, providerLabel);
    renderLLMAgentGuide(s, providerId, providerLabel);
    refreshIcons();
  } catch (err) {
    console.error('LLM status error:', err);
  }
}

// ── LLM Usage ──
async function loadLLMUsage() {
  try {
    const json = await fetchJSON(`${API}/llm/usage`);
    const d = json.data;
    if (!d) {
      document.getElementById('usage-summary').innerHTML = '<div class="text-gray-500 text-xs">데이터 없음</div>';
      return;
    }
    const providerLabel = d.provider_name || formatProviderLabel(d.provider || (d.app_usage && d.app_usage.provider));
    const summary = d.summary || {};
    const totalSessions = summary.total_sessions == null ? '-' : summary.total_sessions.toLocaleString();
    const totalMessages = summary.total_messages == null ? '-' : summary.total_messages.toLocaleString();
    document.getElementById('usage-summary').innerHTML = `
      <div class="flex justify-between">
        <span class="text-gray-400">Provider</span>
        <span class="text-white">${providerLabel}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-gray-400">총 세션</span>
        <span class="text-white">${totalSessions}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-gray-400">총 메시지</span>
        <span class="text-white">${totalMessages}</span>
      </div>`;
    const appEl = document.getElementById('usage-app');
    const app = d.app_usage || {};
    if (app && app.total_calls > 0) {
      const totalInputTokens = app.total_input_tokens ?? app.input_tokens ?? 0;
      const totalOutputTokens = app.total_output_tokens ?? app.output_tokens ?? 0;
      let html = `
        <div class="flex justify-between"><span class="text-gray-400">호출 수</span><span class="text-cyan-400">${app.total_calls}</span></div>
        <div class="flex justify-between"><span class="text-gray-400">입력 토큰</span><span class="text-blue-400">${formatTokens(totalInputTokens)}</span></div>
        <div class="flex justify-between"><span class="text-gray-400">출력 토큰</span><span class="text-green-400">${formatTokens(totalOutputTokens)}</span></div>`;
      if (app.session_id) {
        html += `<div class="flex justify-between"><span class="text-gray-400">세션</span><span class="text-gray-300">${escapeHtml(String(app.session_id).slice(0, 8))}</span></div>`;
      }
      if (app.by_model && Object.keys(app.by_model).length) {
        for (const [model, mu] of Object.entries(app.by_model)) {
          const short = model
            .replace('claude-', '')
            .replace('codex:', '')
            .replace(/-\d{8,}$/, '');
          const cachedTokens = mu.cached_input_tokens ?? mu.cache_read ?? 0;
          html += `<div class="bg-dark-900 rounded p-1.5 mt-1">
            <div class="text-gray-300 text-xs">${short} <span class="text-gray-500">(${mu.calls}회)</span></div>
            <div class="text-gray-500">${formatTokens(mu.input_tokens)} in / ${formatTokens(mu.output_tokens)} out</div>
            ${cachedTokens ? `<div class="text-gray-500">${formatTokens(cachedTokens)} cached</div>` : ''}
          </div>`;
        }
      }
      appEl.innerHTML = html;
    } else {
      appEl.innerHTML = '<div class="text-gray-500">아직 호출 없음</div>';
    }
    const modelsEl = document.getElementById('usage-models');
    const modelData = d.model_usage && Object.keys(d.model_usage).length
      ? d.model_usage
      : (app.by_model && Object.keys(app.by_model).length ? app.by_model : null);
    renderModelCards(modelData, modelsEl);
    const chartEl = document.getElementById('usage-chart');
    const dailyTokens = (d.daily_model_tokens || []).slice(-7);
    if (dailyTokens.length) {
      const totals = dailyTokens.map(day => {
        let sum = 0;
        for (const t of Object.values(day.tokensByModel || {})) sum += t;
        return { date: day.date, tokens: sum };
      });
      const maxTokens = Math.max(...totals.map(t => t.tokens), 1);
      chartEl.innerHTML = totals.map(t => {
        const pct = Math.max((t.tokens / maxTokens) * 100, 2);
        const dateLabel = t.date.slice(5);
        return `<div class="flex items-center gap-2">
          <span class="text-gray-500 w-12 shrink-0">${dateLabel}</span>
          <div class="flex-1 bg-dark-900 rounded-full h-3 overflow-hidden">
            <div class="h-full bg-cyan-500/60 rounded-full" style="width:${pct}%"></div>
          </div>
          <span class="text-gray-400 w-14 text-right shrink-0">${formatTokens(t.tokens)}</span>
        </div>`;
      }).join('');
    } else {
      chartEl.innerHTML = '<div class="text-gray-500">데이터 없음</div>';
    }
  } catch (err) {
    console.error('LLM usage error:', err);
    document.getElementById('usage-summary').innerHTML = '<div class="text-gray-500 text-xs">조회 실패</div>';
  }
}

function formatTokens(n) {
  if (n == null || n === 0) return '0';
  if (n >= 1000000000) return (n / 1000000000).toFixed(1) + 'B';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toLocaleString();
}

function renderModelCards(models, targetEl) {
  if (!models || !Object.keys(models).length) {
    targetEl.innerHTML = '<div class="text-gray-500">데이터 없음</div>';
    return;
  }
  let html = '';
  for (const [model, u] of Object.entries(models)) {
    const short = model.replace('claude-', '').replace('codex:', '').replace(/-\d{8,}$/, '');
    const input = u.inputTokens ?? u.input_tokens ?? 0;
    const output = u.outputTokens ?? u.output_tokens ?? 0;
    const cached = u.cacheReadInputTokens ?? u.cached_input_tokens ?? u.cache_read ?? 0;
    const creation = u.cacheCreationInputTokens ?? 0;
    html += `<div class="bg-dark-900 rounded p-2 mb-1">
      <div class="text-gray-300 font-medium mb-1" title="${model}">${short}</div>
      <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-gray-500">
        <span>입력</span><span class="text-right text-gray-400">${formatTokens(input)}</span>
        <span>출력</span><span class="text-right text-green-400">${formatTokens(output)}</span>
        <span>캐시읽기</span><span class="text-right text-blue-400">${formatTokens(cached)}</span>
        ${creation ? `<span>캐시생성</span><span class="text-right text-purple-400">${formatTokens(creation)}</span>` : ''}
      </div>
    </div>`;
  }
  targetEl.innerHTML = html;
}

function formatProviderLabel(provider) {
  if (!provider) return 'LLM';
  if (provider === 'CLAUDE_CODE') return 'Claude Code';
  if (provider === 'CODEX_CLI') return 'Codex CLI';
  return provider.replaceAll('_', ' ');
}

function getLLMAgentConfig(status, tierKey) {
  return {
    ...LLM_AGENT_DEFAULTS[tierKey],
    ...((status && status[tierKey]) || {}),
  };
}

function formatReasoningEffortText(effort, provider) {
  if (effort) return `추론 ${effort}`;
  if (provider === 'CODEX_CLI') return '추론 global';
  return '';
}

function formatAgentMeta(tierConfig, providerId, providerLabel) {
  const pieces = [providerLabel || formatProviderLabel(providerId), tierConfig.model || '-'];
  const effortText = formatReasoningEffortText(tierConfig.reasoning_effort, providerId);
  if (effortText) pieces.push(effortText);
  return pieces.filter(Boolean).map(item => escapeHtml(item)).join(' · ');
}

function formatTier1ProfileMeta(status, providerId) {
  const profiles = (status && status.tier1_profiles) || {};
  const orderedKeys = ['scan', 'analysis'].filter(key => profiles[key]);
  if (!orderedKeys.length) return '';
  return orderedKeys.map(key => {
    const profile = profiles[key];
    const label = profile.short_label || profile.display_name || key;
    const effort = profile.reasoning_effort || '-';
    return escapeHtml(`${label} ${effort}`);
  }).join(' · ');
}

function renderLLMAgentSummary(status, providerId, providerLabel) {
  const summaryEl = document.getElementById('llm-agent-summary');
  if (!summaryEl) return;

  summaryEl.innerHTML = ['tier1', 'tier2'].map(tierKey => {
    const tierConfig = getLLMAgentConfig(status, tierKey);
    const tier1ProfileMeta = tierKey === 'tier1' ? formatTier1ProfileMeta(status, providerId) : '';
    return `
      <div class="agent-summary-row">
        <div class="text-xs text-gray-200">${escapeHtml(tierConfig.display_name)}</div>
        <div class="text-[11px] text-gray-500 leading-4 mt-1">${escapeHtml(tierConfig.description)}</div>
        <div class="text-[11px] text-gray-500 mt-1 truncate">${formatAgentMeta(tierConfig, providerId, providerLabel)}</div>
        ${tier1ProfileMeta ? `<div class="text-[11px] text-gray-500 mt-1">프로필 · ${tier1ProfileMeta}</div>` : ''}
      </div>
    `;
  }).join('');
}

function renderLLMAgentGuide(status, providerId, providerLabel) {
  const guideEl = document.getElementById('llm-agent-guide');
  if (!guideEl) return;

  guideEl.innerHTML = ['tier1', 'tier2'].map(tierKey => {
    const tierConfig = getLLMAgentConfig(status, tierKey);
    const toneClass = tierKey === 'tier1' ? 'agent-guide-tier1' : 'agent-guide-tier2';
    const tier1ProfileMeta = tierKey === 'tier1' ? formatTier1ProfileMeta(status, providerId) : '';
    return `
      <div class="agent-guide-card ${toneClass}">
        <div class="agent-guide-head">
          <div class="min-w-0">
            <div class="agent-guide-kicker">${escapeHtml(tierConfig.short_label)}</div>
            <div class="agent-guide-title">${escapeHtml(tierConfig.display_name)}</div>
          </div>
          <i data-lucide="${escapeHtml(tierConfig.icon)}" class="w-4 h-4 text-gray-500 shrink-0"></i>
        </div>
        <div class="agent-guide-desc">${escapeHtml(tierConfig.description)}</div>
        <div class="agent-guide-meta">${formatAgentMeta(tierConfig, providerId, providerLabel)}</div>
        ${tier1ProfileMeta ? `<div class="agent-guide-meta">프로필 · ${tier1ProfileMeta}</div>` : ''}
      </div>
    `;
  }).join('');
}

// ── Schedule Timeline ──
async function loadScheduleTimeline() {
  try {
    const json = await fetchJSON(`${API}/schedule/timeline?market=${currentMarket}`);
    const d = json.data;
    if (!d) return;
    renderAdaptivePanel(d.adaptive);
    renderFixedTimeline(d.fixed_jobs);
  } catch (err) {
    console.error('Schedule timeline error:', err);
  }
}

function renderAdaptivePanel(a) {
  const el = document.getElementById('schedule-adaptive');
  if (!el || !a) return;

  const badgeCls = a.enabled ? 'sched-on' : 'sched-off';
  const badgeText = a.enabled ? 'ON' : 'OFF';

  let budgetHtml = '';
  for (let i = 0; i < a.cycles_max; i++) {
    budgetHtml += `<span class="schedule-budget-dot ${i < a.cycles_used ? 'used' : ''}"></span>`;
  }

  let nextHtml = '';
  if (!a.enabled) {
    nextHtml = '<div class="schedule-exhausted">동적 재스캔 비활성</div>';
  } else if (a.cycles_remaining <= 0 && !a.next_run_at) {
    nextHtml = '<div class="schedule-exhausted">예산 소진 — 추가 스캔 없음</div>';
  } else if (a.next_run_at) {
    const t = new Date(a.next_run_at);
    const timeStr = t.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
    const minLabel = a.next_run_in_minutes != null ? `${a.next_run_in_minutes}분 후` : '';
    nextHtml = `<div class="schedule-next-scan">
      <i data-lucide="clock" class="w-3 h-3 text-gray-500"></i>
      <span class="next-label">다음 스캔</span>
      <span class="next-time">${timeStr}</span>
      ${minLabel ? `<span class="next-countdown">(${minLabel})</span>` : ''}
    </div>`;
  } else if (a.last_hint && a.last_hint.action === 'STOP_SESSION') {
    nextHtml = `<div class="schedule-exhausted">세션 종료 — ${a.last_hint.reason || 'AI 판단'}</div>`;
  } else {
    nextHtml = '<div class="schedule-exhausted">대기 중</div>';
  }

  let hintHtml = '';
  if (a.last_hint && a.last_hint.reason) {
    hintHtml = `<div class="schedule-hint-reason">${a.last_hint.reason}</div>`;
  }

  el.innerHTML = `
    <div class="schedule-adaptive-header">
      <span class="schedule-adaptive-title">
        <i data-lucide="bot" class="w-3.5 h-3.5"></i> AI 동적 재스캔
      </span>
      <span class="schedule-adaptive-badge ${badgeCls}">${badgeText}</span>
    </div>
    <div class="schedule-budget">
      ${budgetHtml}
      <span class="schedule-budget-label">${a.cycles_used}/${a.cycles_max} 사용</span>
    </div>
    ${nextHtml}
    ${hintHtml}`;
  refreshIcons();
}

function renderFixedTimeline(jobs) {
  const el = document.getElementById('schedule-fixed');
  if (!el || !jobs || !jobs.length) return;

  const categoryIcons = {
    prep: 'sunrise', scan: 'radar', holdings: 'shield-check',
    liquidation: 'alert-triangle', review: 'clipboard-check',
    sync: 'refresh-cw', data: 'database',
  };

  el.innerHTML = jobs.map(j => {
    const icon = categoryIcons[j.category] || 'circle';
    return `<div class="sched-item sched-${j.status}">
      <span class="sched-dot"></span>
      <span class="sched-time">${j.time}</span>
      <span class="sched-label">${j.name}</span>
    </div>`;
  }).join('');
}

// ── System Status ──
async function loadSystemStatus() {
  try {
    const json = await fetchJSON(`${API}/system/status?market=${currentMarket}`);
    const s = json.data;
    if (!s) return;
    updateBadge('badge-trading', s.trading_enabled ? '매매:ON' : '매매:OFF', s.trading_enabled ? 'green' : 'red');
    updateBadge('badge-mcp', s.mcp_connected ? 'MCP:ON' : 'MCP:OFF', s.mcp_connected ? 'green' : 'red');
    const statusEl = document.getElementById('sys-status');
    const isHoliday = !!s.market_holiday;
    const session = s.market_session || 'CLOSED';
    const sessionLabel = getSessionLabel(session);
    const marketLabel = isHoliday ? `휴장 (${s.market_holiday})` : (session === 'CLOSED' ? '장외' : sessionLabel);
    const marketColor = s.market_open ? 'bg-green-400' : (isHoliday ? 'bg-yellow-400' : 'bg-gray-500');
    const marketExtra = s.market_open ? '' : ` (다음: ${s.next_market_open || ''})`;
    const tzInfo = s.market_tz && s.market_tz !== 'KST' ? ` (${s.market_tz}${s.dst_active ? ' 써머타임' : ''})` : '';
    statusEl.innerHTML = `
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${marketColor}"></span>
        <strong>${marketLabel}</strong>${tzInfo}${marketExtra}
      </div>
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${s.mcp_connected ? 'bg-green-400' : 'bg-red-400'}"></span>
        MCP: ${s.mcp_connected ? '연결' : '끊김'}
      </div>
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${s.scheduler_running ? 'bg-green-400' : 'bg-yellow-400'}"></span>
        스케줄러: ${s.scheduler_running ? '동작' : '중지'}
      </div>
      <div class="flex items-center gap-1.5">
        <span class="status-dot w-1.5 h-1.5 rounded-full ${s.agent_running ? 'bg-green-400' : 'bg-yellow-400'}"></span>
        에이전트: ${s.agent_running ? '동작' : '중지'}
      </div>
      ${s.last_cycle_time ? `<div class="text-gray-500">마지막: ${formatTime(s.last_cycle_time)}</div>` : ''}
      <div class="text-gray-500">SSE: ${s.sse_clients}명</div>`;
    // Update market context bar
    updateMarketContextBar(s);

    const scope = currentScope();
    const scopeLabel = scope === 'KRX' ? 'KRX' : 'US';
    const triggerBtn = document.querySelector('[onclick="triggerCycle()"]');
    if (triggerBtn) {
      const action = s.market_open ? '매매 사이클 실행' : '장마감 리뷰 실행';
      triggerBtn.innerHTML = `<i data-lucide="play" class="w-4 h-4 fill-current"></i> ${enabledMarkets.length > 1 ? scopeLabel + ' ' : ''}${action}`;
      refreshIcons();
    }
    const reportBtn = document.querySelector('[onclick="generateReport()"]');
    if (reportBtn && enabledMarkets.length > 1) {
      reportBtn.innerHTML = `<i data-lucide="file-text" class="w-4 h-4"></i> ${scopeLabel} 리포트 생성`;
      refreshIcons();
    }
  } catch (err) {
    console.error('Status load error:', err);
  }
}

// ── Report List ──
async function loadReportList() {
  try {
    const json = await fetchJSON(`${API}/reports?limit=10&market_scope=${encodeURIComponent(currentMarket)}`);
    const listEl = document.getElementById('report-list');
    listEl.innerHTML = '';
    if (json.data && json.data.length) {
      json.data.forEach(r => {
        const btn = document.createElement('button');
        btn.className = 'w-full text-left px-3 py-1 text-xs text-gray-400 hover:bg-dark-700 rounded';
        btn.textContent = `${r.report_date} (${r.market_scope || currentMarket})`;
        btn.onclick = () => switchToReport(r.report_date);
        listEl.appendChild(btn);
      });
    } else {
      listEl.innerHTML = '<div class="px-3 text-xs text-gray-500">리포트 없음</div>';
    }
  } catch (err) {
    console.error('Report list error:', err);
  }
}

// ── Actions ──
let triggerPending = false;
async function triggerCycle() {
  if (triggerPending) return;
  triggerPending = true;
  const btn = document.querySelector('[onclick="triggerCycle()"]');
  const originalHTML = btn ? btn.innerHTML : '';
  if (btn) { btn.innerHTML = '<span class="progress-spinner"></span> 실행 중...'; btn.disabled = true; }
  try {
    await fetchJSON(`${API}/agent/trigger?market=${currentMarket}`, { method: 'POST' });
    showToast('사이클 실행 요청됨', 'success');
  } catch (err) {
    console.error('Trigger error:', err);
    showToast(`실행 실패: ${err.message}`, 'error');
  } finally {
    setTimeout(() => {
      if (btn) { btn.innerHTML = originalHTML; btn.disabled = false; refreshIcons(); }
      triggerPending = false;
    }, 3000);
  }
}

let reportPending = false;
async function generateReport() {
  if (reportPending) return;
  reportPending = true;
  const btn = document.querySelector('[onclick="generateReport()"]');
  const originalHTML = btn ? btn.innerHTML : '';
  if (btn) { btn.innerHTML = '<span class="progress-spinner"></span> 생성 중...'; btn.disabled = true; }
  try {
    await fetchJSON(`${API}/reports/generate?market_scope=${encodeURIComponent(currentMarket)}`, { method: 'POST' });
    showToast('리포트 생성 요청됨', 'success');
    loadReportList();
  } catch (err) {
    console.error('Report gen error:', err);
    showToast(`리포트 생성 실패: ${err.message}`, 'error');
  } finally {
    setTimeout(() => {
      if (btn) { btn.innerHTML = originalHTML; btn.disabled = false; refreshIcons(); }
      reportPending = false;
    }, 3000);
  }
}

// ── Utilities ──
function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return ts; }
}

function formatDetail(detail, activityType = null) {
  if (!detail) return '';
  try {
    const obj = transformAdminAgentValue(typeof detail === 'string' ? JSON.parse(detail) : detail);
    
    // TIER1_ANALYSIS: Try to extract technical indicators into a mini-table
    if (activityType === 'TIER1_ANALYSIS' && obj.market_context && obj.market_context.indicators) {
      const ind = obj.market_context.indicators;
      const recentPrice = obj.market_context.current_price ? formatAmount(obj.market_context.current_price) : '-';
      return `
        <table class="tech-table mb-2">
          <tr><th colspan="4" class="text-left font-bold text-gray-300 bg-dark-800"><i data-lucide="bar-chart-2" class="w-4 h-4 inline-block mr-1 align-text-bottom"></i>Technical Snapshot</th></tr>
          <tr>
            <td class="label">Price</td><td>${recentPrice}</td>
            <td class="label">RSI(14)</td><td>${ind.rsi ? Number(ind.rsi).toFixed(1) : '-'}</td>
          </tr>
          <tr>
            <td class="label">MACD</td><td>${ind.macd ? Number(ind.macd).toFixed(2) : '-'} / Sig: ${ind.macd_signal ? Number(ind.macd_signal).toFixed(2) : '-'}</td>
            <td class="label">Bollinger</td><td>${ind.bollinger_band_position ? (Number(ind.bollinger_band_position) * 100).toFixed(1) + '%' : '-'}</td>
          </tr>
          <tr>
            <td class="label">Vol Ratio</td><td>${ind.volume_ratio ? Number(ind.volume_ratio).toFixed(1) + 'x' : '-'}</td>
            <td class="label">SMA</td><td>5: ${ind.sma_5 ? Number(ind.sma_5).toFixed(0) : '-'} | 20: ${ind.sma_20 ? Number(ind.sma_20).toFixed(0) : '-'}</td>
          </tr>
        </table>
        <pre class="bg-dark-900/50 p-2 rounded text-gray-500 whitespace-pre-wrap">${escapeHtml(JSON.stringify(obj, null, 2))}</pre>
      `;
    }
    
    return `<pre class="whitespace-pre-wrap">${escapeHtml(JSON.stringify(obj, null, 2))}</pre>`;
  } catch {
    return `<pre class="whitespace-pre-wrap">${escapeHtml(formatAdminAgentText(String(detail)))}</pre>`;
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function getTypeColor(type) {
  const map = {
    CYCLE: 'blue', SCAN: 'cyan', SCREENING: 'purple',
    TIER1_ANALYSIS: 'yellow', TIER2_REVIEW: 'green',
    STRATEGY_EVAL: 'blue', RISK_CHECK: 'yellow',
    RISK_TUNING: 'purple',
    DECISION: 'green', EVENT: 'gray', REPORT: 'purple',
    LLM_CALL: 'cyan', ORDER: 'red', DAILY_PLAN: 'purple',
    TRADE_RESULT: 'green', RISK_GATE: 'red',
  };
  return map[type] || 'gray';
}

function formatLLMConversation(detail) {
  let obj = detail;
  try {
    if (typeof detail === 'string') obj = JSON.parse(detail);
    obj = transformAdminAgentValue(obj);
  } catch { return `<pre class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">${escapeHtml(formatAdminAgentText(String(detail)))}</pre>`; }
  const sys = obj.llm_system_prompt || '';
  const prompt = obj.llm_prompt || '';
  const response = obj.llm_response || '';
  const model = obj.llm_model || '';
  let html = '';
  if (model) html += `<div class="llm-model-tag">${escapeHtml(model)}</div>`;
  if (sys) html += `<div class="llm-msg llm-system"><div class="llm-role">SYSTEM</div><div class="llm-body">${escapeHtml(sys)}</div></div>`;
  if (prompt) html += `<div class="llm-msg llm-user"><div class="llm-role">PROMPT</div><div class="llm-body">${escapeHtml(prompt)}</div></div>`;
  if (response) html += `<div class="llm-msg llm-assistant"><div class="llm-role">RESPONSE</div><div class="llm-body">${escapeHtml(response)}</div></div>`;
  return `<div class="llm-conversation">${html}</div>`;
}

function getPhaseIcon(phase) {
  const icons = {
    START: '<i data-lucide="play" class="w-3 h-3 inline-block"></i>',
    PROGRESS: '<i data-lucide="loader" class="w-3 h-3 inline-block"></i>',
    COMPLETE: '<i data-lucide="check" class="w-3 h-3 inline-block"></i>',
    ERROR: '<i data-lucide="x" class="w-3 h-3 inline-block"></i>',
  };
  return icons[phase] || '<i data-lucide="circle" class="w-3 h-3 inline-block"></i>';
}

function updateBadge(id, text, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  const colors = {
    green: 'bg-green-900/50 text-green-300',
    red: 'bg-red-900/50 text-red-300',
    yellow: 'bg-yellow-900/50 text-yellow-300',
    purple: 'bg-purple-900/50 text-purple-300',
    blue: 'bg-blue-900/50 text-blue-300',
  };
  el.className = `px-2 py-0.5 rounded text-xs font-medium ${colors[color] || colors.blue}`;
}

function setStatus(state, text) {
  const el = document.getElementById('status-text');
  if (el) el.textContent = text;
}

// ── Collapsible Sidebars ──
function toggleLeftSidebar() {
  const sidebar = document.getElementById('left-sidebar');
  if (!sidebar) return;
  sidebar.classList.toggle('collapsed');
  const collapsed = sidebar.classList.contains('collapsed');
  localStorage.setItem('momo-left-sidebar-collapsed', collapsed ? '1' : '');
}

function toggleRightSidebar() {
  const sidebar = document.getElementById('right-sidebar');
  if (!sidebar) return;
  sidebar.classList.toggle('collapsed');
  const collapsed = sidebar.classList.contains('collapsed');
  localStorage.setItem('momo-right-sidebar-collapsed', collapsed ? '1' : '');
}

// Restore sidebar state on load
(function restoreSidebarState() {
  if (localStorage.getItem('momo-left-sidebar-collapsed') === '1') {
    const ls = document.getElementById('left-sidebar');
    if (ls) ls.classList.add('collapsed');
  }
  if (localStorage.getItem('momo-right-sidebar-collapsed') === '1') {
    const rs = document.getElementById('right-sidebar');
    if (rs) rs.classList.add('collapsed');
  }
})();

// ── Collapsible Account Sections (Holdings / Pending) ──
function toggleAccountSection(section) {
  const idMap = { holdings: 'holdings-info', pending: 'pending-orders-info', watchlist: 'watchlist-info' };
  const arrowMap = { holdings: 'holdings-arrow', pending: 'pending-arrow', watchlist: 'watchlist-arrow' };
  const bodyEl = document.getElementById(idMap[section] || `${section}-info`);
  const arrowEl = document.getElementById(arrowMap[section] || `${section}-arrow`);
  const toggleBtn = bodyEl && bodyEl.closest(`#${section}-section`)
    ? bodyEl.closest(`#${section}-section`).querySelector('button')
    : null;
  if (!bodyEl) return;
  const isCollapsed = bodyEl.classList.toggle('collapsed-section');
  if (arrowEl) arrowEl.classList.toggle('collapsed-icon', isCollapsed);
  if (toggleBtn) toggleBtn.setAttribute('aria-expanded', String(!isCollapsed));
}

// ── SSE Disconnect Banner ──
function showSSEDisconnectBanner() {
  if (document.getElementById('sse-disconnect-banner')) return;
  const main = document.querySelector('main');
  if (!main) return;
  const banner = document.createElement('div');
  banner.id = 'sse-disconnect-banner';
  banner.className = 'sse-disconnect-banner shrink-0';
  banner.innerHTML = '<i data-lucide="wifi-off" class="w-4 h-4"></i> 실시간 연결이 끊겼습니다. 재연결 중...';
  main.insertBefore(banner, main.children[1]); // After filter bar
  refreshIcons();
}

function removeSSEDisconnectBanner() {
  const banner = document.getElementById('sse-disconnect-banner');
  if (banner) banner.remove();
}

// ── Back to Live Button ──
function insertBackToLiveBar(container) {
  // Remove existing if any
  const existing = document.getElementById('back-to-live-bar');
  if (existing) existing.remove();
  const bar = document.createElement('div');
  bar.id = 'back-to-live-bar';
  bar.className = 'back-to-live-bar';
  bar.innerHTML = '<button class="back-to-live-btn" onclick="switchView(\'live\')"><i data-lucide="arrow-left" class="w-3 h-3"></i> 실시간으로 돌아가기</button>';
  container.parentNode.insertBefore(bar, container);
  refreshIcons();
}

function removeBackToLiveBar() {
  const bar = document.getElementById('back-to-live-bar');
  if (bar) bar.remove();
}

// ── Common Placeholder Renderer ──
function renderPlaceholder(container, type, message) {
  let html = '';
  if (type === 'loading') {
    html = `<div class="text-center text-gray-500 text-sm py-4 flex items-center justify-center gap-2">
      <span class="progress-spinner"></span> ${escapeHtml(message || '불러오는 중...')}
    </div>`;
  } else if (type === 'empty') {
    html = `<div class="text-center text-gray-500 text-sm py-8">
      <i data-lucide="inbox" class="w-6 h-6 mx-auto mb-2 opacity-50"></i>
      <div>${escapeHtml(message || '데이터가 없습니다')}</div>
    </div>`;
  } else if (type === 'error') {
    html = `<div class="text-center text-red-400 text-sm py-8">
      <i data-lucide="alert-triangle" class="w-6 h-6 mx-auto mb-2 opacity-60"></i>
      <div>${escapeHtml(message || '오류가 발생했습니다')}</div>
    </div>`;
  }
  container.innerHTML = html;
  refreshIcons();
}

// Auto-scroll detection
document.getElementById('chat-container').addEventListener('scroll', function() {
  const el = this;
  autoScroll = (el.scrollHeight - el.scrollTop - el.clientHeight) < 50;
  const fab = document.getElementById('scroll-to-bottom');
  if (fab) fab.classList.toggle('hidden', autoScroll);
  if (autoScroll) {
    missedCount = 0;
    updateScrollBadge();
  }
});
