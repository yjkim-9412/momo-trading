/**
 * MOMO Trading — Coin Admin Dashboard
 * 코인 전용 관리자 대시보드 (24/7, 독립 구현)
 */

const API = '/api/v1/admin-coin';

// ── State ──
let eventSource = null;
let currentView = 'live';
let autoScroll = true;
let isNearTop = false;
let missedCount = 0;
let scanCountdownTimer = null;
let lastSystemStatus = null;
let lastAgentState = null;
let manualScanRequestPending = false;
let manualScanQueuedAt = null;
let manualScanNotice = null;
let manualScanSessionObserved = false;
let lastManualCycleActive = false;
let currentLogFilter = 'ALL';
let monitorExpanded = true;
let monitorUpdateTimer = null;
let monitorElapsedTimer = null;
let titleFlashInterval = null;
let accountRefreshTimer = null;
const metricDeltaTimers = {};

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

let feedState = createFeedState();

const PIPELINE_STEPS = ['data', 'tier1', 'tier2', 'strategy', 'decision'];
const PIPELINE_LABELS = { data: '조회', tier1: 'Tier1', tier2: 'Tier2', strategy: '전략', decision: '결정' };
let monitorState = {
  cycleActive: false,
  cycleId: null,
  startedAt: null,
  scannedCount: 0,
  analyzedCount: 0,
  slots: {},
  completed: [],
  lastCycleSummary: null,
};
const ORIGINAL_TITLE = document.title || 'MOMO Coin Admin';

const LLM_AGENT_DEFAULTS = {
  tier1: {
    display_name: '후보 분석 에이전트',
    short_label: '후보 분석',
  },
  tier2: {
    display_name: '최종 검토 에이전트',
    short_label: '최종 검토',
  },
};
const ADMIN_AGENT_LABELS = {
  TIER1: LLM_AGENT_DEFAULTS.tier1.display_name,
  TIER2: LLM_AGENT_DEFAULTS.tier2.display_name,
};
const AGENT_PHASE_LABELS = {
  BOOTSTRAP: '준비',
  SCAN: '스캔',
  ANALYSIS: '분석',
  DECISION: '판단',
  COMPLETE: '완료',
};

function getStockCards() {
  return feedState.stockCards;
}

function getActivityCount() {
  return feedState.activityCount;
}

function setActivityCount(value) {
  feedState.activityCount = value;
}

function incActivityCount() {
  feedState.activityCount += 1;
}

function getActivityIdentity(data) {
  return data.id || `${data.created_at || ''}|${data.activity_type || ''}|${data.phase || ''}|${data.symbol || ''}|${data.summary || ''}`;
}

function rememberFeedActivities(activities, { prepend = false } = {}) {
  const accepted = [];
  activities.forEach((activity) => {
    const identity = getActivityIdentity(activity);
    if (feedState.loadedActivityIds.has(identity)) return;
    feedState.loadedActivityIds.add(identity);
    accepted.push(activity);
  });
  if (!accepted.length) return 0;
  feedState.feedItems = prepend
    ? [...accepted, ...feedState.feedItems]
    : [...feedState.feedItems, ...accepted];
  return accepted.length;
}

function resetFeedState({ preserveLoaded = false } = {}) {
  feedState.feedItems = [];
  feedState.feedTradingDate = null;
  feedState.feedHasMore = false;
  feedState.feedCursor = null;
  feedState.feedLoading = false;
  feedState.loadedActivityIds = new Set();
  feedState.activityCount = 0;
  if (!preserveLoaded) feedState.loaded = false;
}

// ── Utility Functions ──

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

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatKRW(n) {
  if (n == null || Number.isNaN(Number(n))) return '-';
  const value = Number(n);
  if (Math.abs(value) >= 100_000_000) return (Math.floor(value / 100_000_000 * 10) / 10).toFixed(1) + '억';
  if (Math.abs(value) >= 10_000) return Math.floor(value / 10_000).toLocaleString() + '만';
  return Math.trunc(value).toLocaleString() + ' KRW';
}

function formatCoinQty(n) {
  if (n == null || Number.isNaN(Number(n))) return '-';
  const value = Number(n);
  if (value === 0) return '0';
  // Show up to 8 decimal places, trim trailing zeros
  if (Math.abs(value) < 0.0001) return value.toExponential(4);
  if (Math.abs(value) < 1) return value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
  if (Math.abs(value) < 100) return value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  if (Math.abs(value) < 10000) return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatPnl(pnl, rate) {
  const pnlVal = Number(pnl ?? 0);
  const rateVal = Number(rate ?? 0);
  const sign = pnlVal >= 0 ? '+' : '';
  const color = pnlVal > 0 ? 'text-green-400' : pnlVal < 0 ? 'text-red-400' : 'text-gray-400';
  const pnlText = `${sign}${formatKRW(pnlVal)}`;
  const rateText = `${sign}${rateVal.toFixed(2)}%`;
  return `<span class="${color}">${pnlText} / ${rateText}</span>`;
}

function formatTimeAgo(dateStr) {
  if (!dateStr) return '';
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  if (Number.isNaN(then)) return '';
  const diff = Math.floor((now - then) / 1000);
  if (diff < 60) return `${diff}초 전`;
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}

function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('ko-KR', {
      timeZone: 'Asia/Seoul',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch { return String(ts); }
}

function formatDateTime(ts) {
  if (!ts) return '--';
  try {
    const d = new Date(ts);
    return d.toLocaleString('ko-KR', {
      timeZone: 'Asia/Seoul',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return String(ts);
  }
}

function formatPrice(n) {
  if (n == null || Number.isNaN(Number(n))) return '-';
  const value = Number(n);
  if (value >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (value >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (value >= 0.01) return value.toFixed(4);
  return value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds)) || seconds < 0) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}시간 ${m}분`;
  if (m > 0) return `${m}분`;
  return `${s}초`;
}

function truncateText(text, limit = 72) {
  if (!text) return '';
  const normalized = String(text).replace(/\s+/g, ' ').trim();
  return normalized.length > limit ? `${normalized.slice(0, limit)}...` : normalized;
}

function formatCoinScanSource(scanSource) {
  const source = String(scanSource || '').toUpperCase();
  if (source === 'DISCOVERY') return '발견';
  if (source === 'WATCHLIST') return '고정';
  if (source === 'WATCHLIST_FALLBACK') return '고정 폴백';
  if (source === 'HOLDING_FALLBACK') return '보유 폴백';
  return '';
}

function setInlineStatus(id, text, color = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = color || '';
}

function setStatusText(text, color = '') {
  const el = document.getElementById('status-text');
  if (!el) return;
  el.textContent = text;
  el.style.color = color || '';
}

function setManualScanNotice(label, icon, colorClass, durationMs = 3500) {
  manualScanNotice = {
    label,
    icon,
    colorClass,
    expiresAt: Date.now() + durationMs,
  };
}

function clearManualScanNotice() {
  manualScanNotice = null;
}

function renderTriggerButton() {
  const btn = document.getElementById('btn-trigger-scan');
  if (!btn) return;

  if (manualScanNotice && manualScanNotice.expiresAt <= Date.now()) {
    manualScanNotice = null;
  }

  const cycleActive = !!lastAgentState?.cycle_active;
  const cryptoEnabled = lastSystemStatus?.crypto_enabled !== false;
  const phase = AGENT_PHASE_LABELS[lastAgentState?.phase] || lastAgentState?.phase || '스캔';
  const analyzedCount = Number(lastAgentState?.analyzed_count || 0);
  const scannedCount = Number(lastAgentState?.scanned_count || 0);

  let label = '수동 스캔 실행';
  let icon = 'scan';
  let disabled = false;
  let baseClasses = 'w-full bg-coin-purple/20 hover:bg-coin-purple/30 text-coin-purple border border-coin-purple/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2';
  let title = '코인 수동 스캔 실행';

  if (!cryptoEnabled) {
    label = '코인 비활성';
    icon = 'ban';
    disabled = true;
    title = 'CRYPTO_ENABLED=false 상태입니다';
    baseClasses = 'w-full bg-gray-800 text-gray-500 border border-gray-700 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-not-allowed';
  } else if (manualScanRequestPending) {
    label = '요청 전송 중...';
    icon = 'loader';
    disabled = true;
    title = '수동 스캔 요청을 전송하고 있습니다';
    baseClasses = 'w-full bg-coin-gold/15 text-coin-gold border border-coin-gold/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
  } else if (cycleActive) {
    label = `${phase} 진행 중${scannedCount ? ` · ${analyzedCount}/${scannedCount}` : ''}`;
    icon = 'loader';
    disabled = true;
    title = '현재 코인 사이클이 실행 중입니다';
    baseClasses = 'w-full bg-coin-gold/15 text-coin-gold border border-coin-gold/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
  } else if (manualScanQueuedAt && (Date.now() - manualScanQueuedAt) < 10000) {
    label = '사이클 시작 대기...';
    icon = 'clock-3';
    disabled = true;
    title = '요청은 접수되었고 사이클 시작을 기다리는 중입니다';
    baseClasses = 'w-full bg-blue-900/20 text-blue-300 border border-blue-400/20 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
  } else if (manualScanNotice) {
    label = manualScanNotice.label;
    icon = manualScanNotice.icon;
    title = manualScanNotice.label;
    baseClasses = `w-full ${manualScanNotice.colorClass} border text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2`;
  }

  btn.className = baseClasses;
  btn.disabled = disabled;
  btn.title = title;
  btn.setAttribute('aria-label', title);
  btn.innerHTML = `<i data-lucide="${icon}" class="w-4 h-4 inline-block ${icon === 'loader' ? 'animate-spin' : ''}"></i> ${escapeHtml(label)}`;
  refreshIcons();
}

function showToast(message, optsOrType = 'info', legacyDuration) {
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
    toast.innerHTML = `<div class="flex items-center justify-between gap-2"><span class="flex-1">${escapeHtml(message)}</span>${persistent ? '<button class="toast-dismiss text-white/60 hover:text-white ml-2">x</button>' : ''}</div>`;
  } else {
    toast.textContent = message;
  }

  if (onClick) {
    toast.style.cursor = 'pointer';
    toast.addEventListener('click', (e) => {
      if (!e.target.closest('.toast-dismiss')) onClick();
    });
  }
  if (persistent) {
    const dismiss = toast.querySelector('.toast-dismiss');
    if (dismiss) {
      dismiss.addEventListener('click', (e) => {
        e.stopPropagation();
        _removeToast(toast);
      });
    }
  }

  container.prepend(toast);
  while (container.children.length > 5) _removeToast(container.lastChild);

  if (dur > 0) {
    window.setTimeout(() => toast.classList.add('toast-fade-out'), Math.max(0, dur - 300));
    window.setTimeout(() => _removeToast(toast), dur);
  }
}

function _removeToast(el) {
  if (el && el.parentNode) el.remove();
}

function getTypeColor(type) {
  const map = {
    CYCLE: 'blue',
    SCAN: 'cyan',
    SCREENING: 'purple',
    TIER1_ANALYSIS: 'yellow',
    TIER2_REVIEW: 'green',
    STRATEGY_EVAL: 'blue',
    DECISION: 'green',
    ORDER: 'red',
    TRADE_RESULT: 'green',
    RISK_GATE: 'red',
    EVENT: 'gray',
    REPORT: 'purple',
    LLM_CALL: 'cyan',
  };
  return map[type] || 'gray';
}

function formatPhaseLabel(phase) {
  const map = {
    START: '시작',
    PROGRESS: '진행',
    COMPLETE: '완료',
    ERROR: '오류',
    SKIP: '스킵',
  };
  return map[phase] || phase || '';
}

// ── HTTP Helper ──

async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// ── Badge / Status ──

function updateBadge(id, text, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  const colors = {
    green: 'bg-green-900/50 text-green-300',
    red: 'bg-red-900/50 text-red-300',
    yellow: 'bg-yellow-900/50 text-yellow-300',
    gray: 'bg-gray-800 text-gray-400',
    blue: 'bg-blue-900/50 text-blue-300',
    purple: 'bg-purple-900/50 text-purple-300',
  };
  el.className = `px-2 py-0.5 rounded text-xs font-medium ${colors[color] || colors.blue}`;
}

// ── Importance / Notification ──

function classifyImportance(data) {
  const t = data.activity_type;
  const p = data.phase;
  const s = (data.summary || '').toUpperCase();

  if ((t === 'ORDER' || t === 'TRADE_RESULT') && p === 'COMPLETE') {
    return {
      level: 'CRITICAL',
      title: (s.includes('매도') || s.includes('SELL')) ? '매도 체결' : '매수 체결',
      body: `${data.symbol || ''} — ${data.summary || ''}`,
    };
  }

  if (t === 'TIER1_ANALYSIS' && p === 'COMPLETE' && (s.includes('BUY') || s.includes('SELL'))) {
    return {
      level: 'HIGH',
      title: s.includes('BUY') ? '매수 신호' : '매도 신호',
      body: `${data.symbol || ''} — ${data.summary || ''}`,
    };
  }

  if (t === 'TIER2_REVIEW' && p === 'COMPLETE' && s.includes('미승인')) {
    return {
      level: 'HIGH',
      title: 'TIER2 미승인',
      body: `${data.symbol || ''} — ${data.summary || ''}`,
    };
  }

  if (t === 'DECISION' && p === 'COMPLETE') {
    return {
      level: 'HIGH',
      title: '주문 실행',
      body: `${data.symbol || ''} — ${data.summary || ''}`,
    };
  }

  return { level: 'NONE' };
}

function sendBrowserNotification(info) {
  if (!document.hidden) return;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  try {
    const notification = new Notification(info.title, {
      body: info.body,
      tag: `momo-coin-${info.level}-${Date.now()}`,
      requireInteraction: info.level === 'CRITICAL',
    });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Browser notification unavailable.
  }
}

function requestNotificationPermission() {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function flashTitle(alertText) {
  if (titleFlashInterval) return;
  let show = true;
  titleFlashInterval = setInterval(() => {
    document.title = show ? alertText : ORIGINAL_TITLE;
    show = !show;
  }, 1000);
}

function stopTitleFlash() {
  if (!titleFlashInterval) return;
  clearInterval(titleFlashInterval);
  titleFlashInterval = null;
  document.title = ORIGINAL_TITLE;
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) stopTitleFlash();
});

// ── Agent Monitor ──

function scheduleMonitorRender() {
  if (monitorUpdateTimer) return;
  monitorUpdateTimer = setTimeout(() => {
    monitorUpdateTimer = null;
    renderAgentMonitor();
  }, 80);
}

function _extractName(summary, symbol) {
  const match = summary && summary.match(/\[([^\]]+)\]/);
  return match ? match[1] : symbol;
}

function _detectOutcome(summary, data) {
  if (data?.phase === 'ERROR' || data?.error_message) return 'error';
  if (!summary) return 'hold';
  const normalized = summary.toLowerCase();
  if (normalized.includes('매수') || normalized.includes('buy')) return 'buy';
  if (normalized.includes('매도') || normalized.includes('sell')) return 'sell';
  if (normalized.includes('오류') || normalized.includes('error') || normalized.includes('실패')) return 'error';
  if (normalized.includes('스킵') || normalized.includes('skip') || normalized.includes('차단')) return 'skip';
  return 'hold';
}

function _finishSlot(ms, symbol, outcome) {
  const slot = ms.slots[symbol];
  const elapsed = slot ? Math.round((Date.now() - slot.startedAt) / 1000) : 0;
  ms.completed.push({ symbol, name: slot ? slot.name : symbol, outcome, elapsed });
  delete ms.slots[symbol];
  ms.analyzedCount = ms.completed.length;
}

function syncAgentSnapshot(state) {
  lastAgentState = state || null;
  const cycleActive = !!state?.cycle_active;
  const manualCycleActive = cycleActive && manualScanSessionObserved;

  if (manualCycleActive) {
    manualScanQueuedAt = null;
    clearManualScanNotice();
  } else if (lastManualCycleActive) {
    setManualScanNotice('최근 스캔 완료', 'check', 'bg-green-900/20 text-green-300 border-green-400/20');
    manualScanSessionObserved = false;
  } else if (manualScanQueuedAt && (Date.now() - manualScanQueuedAt) >= 10000) {
    manualScanQueuedAt = null;
    manualScanSessionObserved = false;
    setManualScanNotice('시작 확인 지연', 'alert-triangle', 'bg-yellow-900/20 text-yellow-300 border-yellow-400/20', 4500);
  }

  lastManualCycleActive = manualCycleActive;
  renderTriggerButton();
}

function updateMonitorFromActivity(data) {
  const ms = monitorState;
  const { activity_type: activityType, phase, symbol, summary } = data;

  switch (activityType) {
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
          const detail = typeof data.detail === 'string' ? JSON.parse(data.detail) : data.detail;
          ms.scannedCount = (detail.selected || []).length || ms.scannedCount;
        } catch {
          // Ignore malformed detail payload.
        }
      }
      break;
    case 'TIER1_ANALYSIS':
      if (phase === 'START' && symbol) {
        ms.slots[symbol] = { symbol, name: _extractName(summary, symbol), step: 'tier1', startedAt: Date.now() };
      } else if (phase === 'COMPLETE' && symbol) {
        if (ms.slots[symbol]) ms.slots[symbol].step = 'tier1_done';
        const normalized = (summary || '').toUpperCase();
        if (normalized.includes('HOLD') || normalized.includes('관망') || normalized.includes('보류')) {
          _finishSlot(ms, symbol, 'hold');
        }
      } else if (phase === 'ERROR' && symbol) {
        _finishSlot(ms, symbol, 'error');
      } else if (phase === 'SKIP' && symbol) {
        _finishSlot(ms, symbol, 'skip');
      }
      break;
    case 'TIER2_REVIEW':
      if (symbol && ms.slots[symbol]) {
        ms.slots[symbol].step = phase === 'COMPLETE' ? 'tier2_done' : 'tier2';
        if ((summary || '').includes('미승인')) _finishSlot(ms, symbol, 'hold');
      }
      break;
    case 'STRATEGY_EVAL':
      if (symbol && ms.slots[symbol]) ms.slots[symbol].step = 'strategy';
      break;
    case 'RISK_GATE':
      if (phase === 'SKIP' && symbol) _finishSlot(ms, symbol, 'skip');
      break;
    case 'DECISION':
      if ((phase === 'COMPLETE' || phase === 'ERROR') && symbol) {
        _finishSlot(ms, symbol, _detectOutcome(summary, data));
      } else if (symbol && ms.slots[symbol]) {
        ms.slots[symbol].step = 'decision';
      }
      break;
    case 'TRADE_RESULT': {
      if (!symbol) break;
      const existing = ms.completed.find((entry) => entry.symbol === symbol);
      if (existing) {
        const outcome = _detectOutcome(summary, data);
        if (outcome !== 'hold') existing.outcome = outcome;
      }
      break;
    }
  }

  scheduleMonitorRender();
}

function handleAgentStateEvent(data) {
  syncAgentSnapshot(data);
  const ms = monitorState;

  if (data?.cycle_active) {
    ms.cycleActive = true;
    ms.cycleId = data.cycle_id;
    ms.startedAt = data.started_at ? new Date(data.started_at).getTime() : Date.now();
    ms.scannedCount = data.scanned_count || 0;
    ms.analyzedCount = data.analyzed_count || 0;
    ms.slots = ms.slots || {};
  } else {
    if (ms.cycleActive) {
      ms.lastCycleSummary = {
        analyzedCount: data?.analyzed_count || ms.analyzedCount,
        scannedCount: data?.scanned_count || ms.scannedCount,
        elapsed: ms.startedAt ? Math.round((Date.now() - ms.startedAt) / 1000) : 0,
        time: new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      };
    }
    ms.cycleActive = false;
    ms.slots = {};
  }

  scheduleMonitorRender();
}

function renderAgentMonitor() {
  const ms = monitorState;
  const iconEl = document.getElementById('monitor-status-icon');
  const statusEl = document.getElementById('monitor-status');
  const elapsedEl = document.getElementById('monitor-elapsed');
  const progressWrap = document.getElementById('monitor-progress-wrap');
  const progressBar = document.getElementById('monitor-progress-bar');
  const body = document.getElementById('monitor-body');
  const slotsEl = document.getElementById('monitor-slots');
  const completedWrap = document.getElementById('monitor-completed');
  const completedList = document.getElementById('monitor-completed-list');

  if (!iconEl || !statusEl || !elapsedEl || !progressWrap || !progressBar || !body || !slotsEl || !completedWrap || !completedList) {
    return;
  }

  if (ms.cycleActive) {
    iconEl.classList.add('active');
    statusEl.textContent = '코인 사이클 진행 중';
    statusEl.style.color = '#c4b5fd';

    progressWrap.style.display = '';
    const total = ms.scannedCount || 1;
    const done = ms.analyzedCount;
    progressBar.style.width = `${Math.min(100, Math.round((done / total) * 100))}%`;

    if (ms.startedAt) {
      const sec = Math.round((Date.now() - ms.startedAt) / 1000);
      elapsedEl.textContent = `${sec}s`;
      _startElapsedTimer();
    }

    if (monitorExpanded) {
      body.style.display = '';
      const slotSymbols = Object.keys(ms.slots);
      let slotsHTML = '';
      slotSymbols.forEach((sym) => {
        const slot = ms.slots[sym];
        const stepIdx = PIPELINE_STEPS.indexOf(slot.step.replace('_done', ''));
        const isDone = slot.step.endsWith('_done');
        const slotSec = Math.round((Date.now() - slot.startedAt) / 1000);
        slotsHTML += `<div class="monitor-slot slot-active">
          <div class="monitor-slot-symbol">${escapeHtml(slot.symbol)}</div>
          <div class="monitor-slot-name">${escapeHtml(slot.name)}</div>
          <div class="pipeline-steps">${_renderPipeline(stepIdx, isDone)}</div>
          <div class="monitor-slot-timer">${slotSec}s · ${PIPELINE_LABELS[slot.step.replace('_done', '')] || slot.step}</div>
        </div>`;
      });
      if (!slotSymbols.length && !ms.completed.length) {
        slotsHTML += `<div class="monitor-slot" style="opacity:0.3; grid-column: 1/-1; text-align:center;">
          <div class="text-xs text-gray-600">스캔 완료 · 분석 대기 중...</div>
        </div>`;
      }
      slotsEl.innerHTML = slotsHTML;

      if (ms.completed.length > 0) {
        completedWrap.style.display = '';
        completedList.innerHTML = ms.completed.map((item) => {
          const cls = `badge-${item.outcome}`;
          const outcomeLabel = { buy: '매수', sell: '매도', hold: '관망', error: '오류', skip: '스킵' }[item.outcome] || item.outcome;
          return `<span class="monitor-completed-badge ${cls}" title="${escapeHtml(item.name)} (${item.elapsed}s)">${escapeHtml(item.symbol)} <span style="opacity:0.7">${outcomeLabel}</span></span>`;
        }).join('');
      } else {
        completedWrap.style.display = 'none';
      }
    } else {
      body.style.display = 'none';
    }
  } else {
    iconEl.classList.remove('active');
    statusEl.style.color = '#6b7280';
    progressWrap.style.display = 'none';
    body.style.display = 'none';
    _stopElapsedTimer();

    if (ms.lastCycleSummary) {
      const summary = ms.lastCycleSummary;
      statusEl.textContent = '대기 중';
      elapsedEl.textContent = `마지막: ${summary.time} (${summary.scannedCount}종목, ${summary.elapsed}s)`;
    } else {
      statusEl.textContent = '대기 중';
      elapsedEl.textContent = '';
    }
  }

  refreshIcons();
}

function _renderPipeline(activeIdx, isDone) {
  let html = '';
  for (let i = 0; i < PIPELINE_STEPS.length; i += 1) {
    if (i > 0) {
      const connectorClass = i <= activeIdx ? 'conn-done' : '';
      html += `<div class="pipeline-connector ${connectorClass}"></div>`;
    }
    let cls = 'pipeline-step';
    if (i < activeIdx || (i === activeIdx && isDone)) cls += ' step-done';
    else if (i === activeIdx) cls += ' step-active';
    html += `<div class="${cls}" title="${PIPELINE_LABELS[PIPELINE_STEPS[i]]}"></div>`;
  }
  return html;
}

function toggleMonitorExpand() {
  monitorExpanded = !monitorExpanded;
  renderAgentMonitor();
}

function _startElapsedTimer() {
  if (monitorElapsedTimer) return;
  monitorElapsedTimer = setInterval(() => {
    if (!monitorState.cycleActive || !monitorState.startedAt) {
      _stopElapsedTimer();
      return;
    }
    const sec = Math.round((Date.now() - monitorState.startedAt) / 1000);
    const elapsedEl = document.getElementById('monitor-elapsed');
    if (elapsedEl) elapsedEl.textContent = `${sec}s`;
  }, 1000);
}

function _stopElapsedTimer() {
  if (!monitorElapsedTimer) return;
  clearInterval(monitorElapsedTimer);
  monitorElapsedTimer = null;
}

async function initAgentMonitor() {
  await loadAgentState({ quiet: true });
  renderAgentMonitor();
}

// ── SSE Connection / Feed ──

function showSSEDisconnectBanner() {
  if (document.getElementById('sse-disconnect-banner')) return;
  const main = document.querySelector('main');
  if (!main) return;
  const banner = document.createElement('div');
  banner.id = 'sse-disconnect-banner';
  banner.className = 'sse-disconnect-banner shrink-0';
  banner.innerHTML = '<i data-lucide="wifi-off" class="w-4 h-4"></i> 실시간 연결이 끊겼습니다. 재연결 중...';
  main.insertBefore(banner, main.children[1]);
  refreshIcons();
}

function removeSSEDisconnectBanner() {
  document.getElementById('sse-disconnect-banner')?.remove();
}

function highlightCard(data) {
  if (!data?.symbol) return;
  const cards = Object.values(getStockCards());
  const card = cards.find((item) => item.symbol === data.symbol);
  if (!card?.element) return;
  card.element.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.element.classList.add('highlighted');
  setTimeout(() => card.element.classList.remove('highlighted'), 3000);
}

function navigateToCard(data) {
  if (!data) return;
  highlightCard(data);
}

window.addEventListener('beforeunload', () => {
  if (eventSource) eventSource.close();
});

function connectSSE() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }

  const es = new EventSource(`${API}/stream`);
  eventSource = es;

  es.onopen = () => {
    updateBadge('badge-sse', '연결', 'green');
    setStatusText('SSE 연결됨', '#34d399');
    removeSSEDisconnectBanner();
    requestNotificationPermission();
    setTimeout(initAgentMonitor, 100);
  };

  es.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'connected') return;

      if (msg.type === 'agent_state') {
        handleAgentStateEvent(msg.data);
        return;
      }

      if (msg.type === 'activity') {
        const data = msg.data || msg;
        updateMonitorFromActivity(data);

        const importance = classifyImportance(data);
        if (importance.level !== 'NONE') {
          showToast(`[CRYPTO] ${importance.title}: ${data.symbol || ''} ${data.summary || ''}`, {
            level: importance.level,
            persistent: importance.level === 'CRITICAL',
            onClick: () => navigateToCard(data),
          });
          if (document.hidden) {
            sendBrowserNotification({ ...importance, title: `[CRYPTO] ${importance.title}` });
            flashTitle(`${importance.level === 'CRITICAL' ? '[긴급]' : '[알림]'} [CRYPTO] ${importance.title}`);
          }
        }

        if (currentView === 'live') {
          appendActivity(data);
          if (importance.level === 'CRITICAL' || importance.level === 'HIGH') {
            setTimeout(() => highlightCard(data), 100);
          }
        }

        if (data.phase === 'COMPLETE' && ['DECISION', 'ORDER', 'TRADE_RESULT'].includes(data.activity_type)) {
          scheduleAccountOverviewRefresh(2000);
        }
        if (data.phase === 'COMPLETE' && ['SCAN', 'CYCLE', 'DECISION'].includes(data.activity_type)) {
          setTimeout(loadWatchlist, 1500);
        }
        if (['CYCLE', 'DECISION', 'ORDER', 'TRADE_RESULT'].includes(data.activity_type)) {
          setTimeout(loadSystemStatus, 900);
        }
      }

      if (msg.type === 'account_changed') {
        scheduleAccountOverviewRefresh();
      }
    } catch (err) {
      console.error('[coin-sse] parse error', err);
    }
  };

  es.onerror = () => {
    updateBadge('badge-sse', '끊김', 'red');
    setStatusText('SSE 재연결 중...', '#f87171');
    showSSEDisconnectBanner();
    setTimeout(() => {
      if (es.readyState === EventSource.CLOSED) connectSSE();
    }, 3000);
    setTimeout(initAgentMonitor, 4000);
  };
}

function appendActivity(data, options = {}) {
  const { skipStore = false } = options;
  if (!skipStore && rememberFeedActivities([data]) === 0) return;

  const container = document.getElementById('chat-container');
  if (!container) return;

  if (container.children.length === 1 && container.children[0].classList.contains('text-center')) {
    container.innerHTML = '';
  }

  const symbol = data.symbol;
  const isCycleActivity = data.activity_type === 'CYCLE';
  const isLLMCall = data.activity_type === 'LLM_CALL';

  if (!symbol || isCycleActivity) {
    if (isCycleActivity && data.phase === 'START') {
      container.appendChild(createCycleDivider(data, true));
    } else if (isCycleActivity && (data.phase === 'COMPLETE' || data.phase === 'ERROR')) {
      const startKey = `cycle-start-${data.cycle_id}`;
      const existing = container.querySelector(`[data-cycle-start="${startKey}"]`);
      if (existing) {
        existing.querySelector('.progress-spinner')?.remove();
        const text = existing.querySelector('.cycle-text');
        if (text) text.textContent += ' → 완료';
      }
      container.appendChild(createCycleDivider(data, false));
    } else if (isLLMCall && !symbol) {
      container.appendChild(createBubble(data));
    } else {
      container.appendChild(createBubble(data));
    }
  } else {
    const cards = getStockCards();
    const cardKey = `${data.cycle_id || 'ev'}:${symbol}`;
    let card = cards[cardKey];

    if (!card) {
      Object.entries(cards).forEach(([key, existing]) => {
        if (!card && key.endsWith(`:${symbol}`) && (!existing.outcome || existing.outcome === 'progress')) {
          card = existing;
          cards[cardKey] = card;
        }
      });
    }

    if (!card) {
      if (isLLMCall) return;
      card = createStockCard(symbol, data);
      cards[cardKey] = card;
      container.appendChild(card.element);
    }

    addStepToCard(card, data);
    updateCardHeader(card);
  }

  incActivityCount();
  const countEl = document.getElementById('activity-count');
  if (countEl) countEl.textContent = `${getActivityCount()}건`;

  if (!autoScroll) {
    missedCount += 1;
    updateScrollFab();
  }

  if (autoScroll) container.scrollTop = container.scrollHeight;
  refreshIcons();
}

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

function createStockCard(symbol, firstActivity) {
  const el = document.createElement('div');
  el.className = 'stock-card outcome-progress';

  const nameMatch = (firstActivity.summary || '').match(/\[([^\]]+)\]/);
  let coinName = nameMatch ? nameMatch[1] : symbol;
  if (/^TIER\d/i.test(coinName)) coinName = symbol;

  const header = document.createElement('div');
  header.className = 'stock-card-header';

  const badgeClass = 'bg-violet-900/40 text-violet-300';
  header.innerHTML = `
    <i data-lucide="activity" class="w-4 h-4 text-gray-400 shrink-0"></i>
    <span class="text-sm font-medium text-white flex-1 truncate">
      ${escapeHtml(coinName)} <span class="text-gray-500 text-xs">${escapeHtml(symbol)}</span>
    </span>
    <span class="stock-outcome flex items-center gap-1 text-xs px-2 py-0.5 rounded ${badgeClass}">
      <span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:2px"></span>분석 중
    </span>
    <span class="stock-elapsed text-xs text-gray-400"></span>
    <span class="stock-expand text-gray-500 text-xs transition-transform" style="transform:rotate(-90deg)"><i data-lucide="chevron-down" class="w-4 h-4"></i></span>
  `;

  const body = document.createElement('div');
  body.className = 'stock-card-body';

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
    symbol,
    stockName: coinName,
    outcome: null,
    confidence: null,
    totalElapsed: 0,
    isOpen: false,
    startTime: Date.now(),
    liveTimer: null,
  };

  header.onclick = () => toggleCardBody(card);
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

function addStepToCard(card, data) {
  card.activities.push(data);
  const progressKey = getProgressKey(data);

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

  if (data.phase === 'COMPLETE' || data.phase === 'ERROR') {
    card.stepsEl.querySelector(`[data-progress-key="${progressKey}"]`)?.remove();
  }

  const step = document.createElement('div');
  step.className = 'stock-step';
  const time = formatTime(data.created_at);
  const typeColor = getTypeColor(data.activity_type);
  const elapsed = data.execution_time_ms ? `${(data.execution_time_ms / 1000).toFixed(1)}초` : '';

  let html = `
    <span class="text-xs text-gray-500 shrink-0 w-14">${time}</span>
    <div class="flex-1 min-w-0">
      <div class="text-xs">${escapeHtml(formatAdminAgentText(data.summary))}</div>`;

  const meta = [];
  if (data.llm_provider) meta.push(`<span class="text-${typeColor}-400">${escapeHtml(data.llm_provider)}</span>`);
  if (elapsed) meta.push(elapsed);
  if (data.confidence != null) {
    const pct = Math.round(data.confidence * 100);
    const confColor = pct >= 70 ? 'text-green-400' : pct >= 40 ? 'text-yellow-400' : 'text-red-400';
    meta.push(`<span class="${confColor} font-medium">신뢰도 ${pct}%</span>`);
  }
  if (meta.length) {
    html += `<div class="text-xs text-gray-400 mt-0.5">${meta.join(' · ')}</div>`;
  }

  if (data.detail) {
    const detailId = `sd-${Math.random().toString(36).slice(2, 8)}`;
    const isLLMCall = data.activity_type === 'LLM_CALL';
    html += `
      <button onclick="event.stopPropagation(); toggleDetail('${detailId}')" class="text-xs text-gray-500 hover:text-gray-300 mt-0.5 flex items-center gap-1">
        ${isLLMCall ? '<i data-lucide="message-square" class="w-3 h-3"></i> LLM 대화' : '<i data-lucide="chevron-down" class="w-3 h-3"></i> 상세'}
      </button>
      <div id="${detailId}" class="detail-content mt-1 text-xs bg-dark-900/50 rounded p-2 text-gray-400">
        ${isLLMCall ? formatLLMConversation(data.detail) : `<div class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">${formatDetail(data.detail, data.activity_type)}</div>`}
      </div>`;
  }

  if (data.error_message) {
    html += `<div class="text-xs text-red-400 mt-0.5">${escapeHtml(formatAdminAgentText(data.error_message))}</div>`;
  }

  html += '</div>';
  step.innerHTML = html;
  card.stepsEl.appendChild(step);
}

function updateCardHeader(card) {
  let outcome = 'progress';
  let outcomeText = '<span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:2px"></span>분석 중';
  let outcomeBg = 'bg-violet-900/40 text-violet-300';
  let totalMs = 0;

  card.activities.forEach((activity) => {
    if (activity.execution_time_ms) totalMs += activity.execution_time_ms;

    if (activity.phase === 'ERROR' || activity.error_message) {
      outcome = 'error';
      outcomeText = '<i data-lucide="x-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 오류';
      outcomeBg = 'bg-red-900/40 text-red-300';
    }

    if (activity.phase === 'SKIP' && outcome !== 'error') {
      outcome = 'hold';
      outcomeText = '<i data-lucide="skip-forward" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 스킵';
      outcomeBg = 'bg-gray-700/60 text-gray-300';
    }

    if (activity.activity_type === 'TIER1_ANALYSIS' && activity.phase === 'COMPLETE') {
      const summary = activity.summary || '';
      if (summary.includes('HOLD') || summary.includes('관망') || summary.includes('실패')) {
        outcome = 'hold';
        outcomeText = summary.includes('실패')
          ? '<i data-lucide="alert-triangle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 분석 실패'
          : '<i data-lucide="pause-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> HOLD';
        outcomeBg = 'bg-gray-700/60 text-gray-300';
      } else if (summary.includes('BUY') || summary.includes('매수')) {
        outcome = 'buy';
        outcomeText = '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매수';
        outcomeBg = 'bg-green-900/40 text-green-300';
      } else if (summary.includes('SELL') || summary.includes('매도')) {
        outcome = 'sell';
        outcomeText = '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매도';
        outcomeBg = 'bg-amber-900/40 text-amber-200';
      }
    }

    if (activity.activity_type === 'TIER2_REVIEW' && activity.phase === 'COMPLETE') {
      const summary = activity.summary || '';
      if (summary.includes('미승인')) {
        outcome = outcome !== 'error' ? 'hold' : outcome;
        outcomeText = '<i data-lucide="minus-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 미승인';
        outcomeBg = 'bg-gray-700/60 text-gray-300';
      }
    }

    if (activity.activity_type === 'STRATEGY_EVAL' && activity.phase === 'COMPLETE') {
      const summary = activity.summary || '';
      if ((summary.includes('HOLD') || summary.includes('스킵')) && outcome !== 'error') {
        outcome = 'hold';
        outcomeText = '<i data-lucide="pause-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> HOLD';
        outcomeBg = 'bg-gray-700/60 text-gray-300';
      }
    }

    if (activity.activity_type === 'DECISION' || activity.activity_type === 'ORDER' || activity.activity_type === 'TRADE_RESULT') {
      const summary = activity.summary || '';
      const isSell = outcome === 'sell' || summary.includes('SELL') || summary.includes('매도');
      if (activity.phase === 'COMPLETE' && (summary.includes('주문 접수') || summary.includes('체결'))) {
        outcome = isSell ? 'sell' : 'buy';
        outcomeText = isSell
          ? '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매도 완료'
          : '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매수 완료';
        outcomeBg = isSell ? 'bg-amber-900/40 text-amber-200' : 'bg-green-900/40 text-green-300';
      } else if (summary.includes('주문 실행')) {
        if (outcome !== 'buy' && outcome !== 'sell') {
          outcome = isSell ? 'sell' : 'buy';
          outcomeText = isSell
            ? '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매도'
            : '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> 매수';
          outcomeBg = isSell ? 'bg-amber-900/40 text-amber-200' : 'bg-green-900/40 text-green-300';
        }
      }
    }

    if (activity.confidence != null) {
      card.confidence = activity.confidence;
    }
  });

  card.outcome = outcome;
  card.totalElapsed = totalMs;

  const outcomeEl = card.headerEl.querySelector('.stock-outcome');
  if (outcomeEl) {
    outcomeEl.className = `stock-outcome flex items-center gap-1.5 text-xs px-2 py-0.5 rounded ${outcomeBg}`;
    outcomeEl.innerHTML = outcomeText;
  }

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

  card.element.className = `stock-card outcome-${outcome}`;
  applyFilterToCard(card);
}

function applyFilterToCard(card) {
  if (currentLogFilter === 'ALL') {
    card.element.style.display = '';
    return;
  }
  card.element.style.display = ['buy', 'sell', 'hold'].includes(card.outcome) ? '' : 'none';
}

function toggleCardBody(card) {
  card.isOpen = !card.isOpen;
  card.bodyEl.classList.toggle('open', card.isOpen);
  const arrow = card.headerEl.querySelector('.stock-expand');
  if (arrow) arrow.style.transform = card.isOpen ? 'rotate(0deg)' : 'rotate(-90deg)';
}

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
  if (data.llm_provider) meta.push(`<span class="text-${typeColor}-400">${escapeHtml(data.llm_provider)}</span>`);
  if (data.execution_time_ms) meta.push(`${(data.execution_time_ms / 1000).toFixed(1)}초`);
  if (data.confidence != null) meta.push(`신뢰도 ${Math.round(data.confidence * 100)}%`);
  if (meta.length) {
    html += `<div class="flex items-center gap-3 mt-0.5 text-xs text-gray-500">${meta.join(' | ')}</div>`;
  }

  if (data.detail) {
    const detailId = `detail-${data.id || Math.random().toString(36).slice(2, 8)}`;
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

  html += '</div></div>';
  div.innerHTML = html;
  return div;
}

function toggleDetail(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('open');
  const btn = el.previousElementSibling;
  if (btn && btn.tagName === 'BUTTON') {
    btn.setAttribute('aria-expanded', String(el.classList.contains('open')));
  }
}

function formatDetail(detail, activityType = null) {
  if (!detail) return '';
  try {
    const obj = transformAdminAgentValue(typeof detail === 'string' ? JSON.parse(detail) : detail);
    const indicators = obj?.market_context?.indicators;
    if (activityType === 'TIER1_ANALYSIS' && indicators) {
      const recentPrice = obj.market_context?.current_price != null
        ? formatPrice(obj.market_context.current_price)
        : '-';
      return `
        <table class="tech-table mb-2">
          <tr><th colspan="4" class="text-left">Technical Snapshot</th></tr>
          <tr>
            <td class="label">Price</td><td>${recentPrice}</td>
            <td class="label">RSI(14)</td><td>${indicators.rsi != null ? Number(indicators.rsi).toFixed(1) : '-'}</td>
          </tr>
          <tr>
            <td class="label">MACD</td><td>${indicators.macd != null ? Number(indicators.macd).toFixed(2) : '-'} / Sig ${indicators.macd_signal != null ? Number(indicators.macd_signal).toFixed(2) : '-'}</td>
            <td class="label">BB 위치</td><td>${indicators.bollinger_band_position != null ? `${(Number(indicators.bollinger_band_position) * 100).toFixed(1)}%` : '-'}</td>
          </tr>
          <tr>
            <td class="label">Vol Ratio</td><td>${indicators.volume_ratio != null ? `${Number(indicators.volume_ratio).toFixed(1)}x` : '-'}</td>
            <td class="label">SMA</td><td>5 ${indicators.sma_5 != null ? Number(indicators.sma_5).toFixed(0) : '-'} / 20 ${indicators.sma_20 != null ? Number(indicators.sma_20).toFixed(0) : '-'}</td>
          </tr>
        </table>
        <pre class="whitespace-pre-wrap text-gray-400">${escapeHtml(JSON.stringify(obj, null, 2))}</pre>
      `;
    }
    return `<pre class="whitespace-pre-wrap text-gray-400">${escapeHtml(JSON.stringify(obj, null, 2))}</pre>`;
  } catch {
    return `<pre class="whitespace-pre-wrap text-gray-400">${escapeHtml(formatAdminAgentText(String(detail)))}</pre>`;
  }
}

function formatLLMConversation(detail) {
  let obj = detail;
  try {
    if (typeof detail === 'string') obj = JSON.parse(detail);
    obj = transformAdminAgentValue(obj);
  } catch {
    return `<pre class="whitespace-pre-wrap text-gray-400">${escapeHtml(formatAdminAgentText(String(detail)))}</pre>`;
  }

  const systemPrompt = obj.llm_system_prompt || obj.system_prompt || obj.system || '';
  const prompt = obj.llm_prompt || obj.prompt || '';
  const response = obj.llm_response || obj.response || obj.raw_response || '';
  const model = obj.llm_model || obj.model || '';
  let html = '';

  const makeCollapsible = (role, bodyText, defaultOpen = false) => {
    const id = `llm-${Math.random().toString(36).slice(2, 8)}`;
    const bodyClass = defaultOpen ? 'llm-body open' : 'llm-body';
    const typeClass = `type-${role.toLowerCase()}`;
    return `
      <div class="llm-msg ${typeClass}">
        <div class="llm-role" onclick="document.getElementById('${id}').classList.toggle('open'); this.querySelector('i').classList.toggle('rotate-180')">
          <span>${role}</span>
          <i data-lucide="chevron-down" class="w-3.5 h-3.5 transition-transform duration-200 ${defaultOpen ? 'rotate-180' : ''}"></i>
        </div>
        <div id="${id}" class="${bodyClass}">${escapeHtml(formatAdminAgentText(bodyText))}</div>
      </div>`;
  };

  if (model) html += `<div class="llm-model-tag">${escapeHtml(String(model))}</div>`;
  if (systemPrompt) html += makeCollapsible('SYSTEM', systemPrompt, false);
  if (prompt) html += makeCollapsible('PROMPT', prompt, false);
  if (response) html += makeCollapsible('RESPONSE', response, true);

  return `<div class="llm-conversation">${html || '<div class="text-gray-500">표시할 LLM 상세가 없습니다.</div>'}</div>`;
}

function renderPlaceholder(container, type, message) {
  if (!container) return;
  let html = '';
  if (type === 'loading') {
    html = `<div class="text-center text-gray-500 text-sm py-4 flex items-center justify-center gap-2"><span class="progress-spinner"></span> ${escapeHtml(message || '불러오는 중...')}</div>`;
  } else if (type === 'error') {
    html = `<div class="text-center text-red-400 text-sm py-8">${escapeHtml(message || '로드 실패')}</div>`;
  } else {
    html = `<div class="text-center text-gray-500 text-sm py-8"><i data-lucide="inbox" class="w-6 h-6 mx-auto mb-2 opacity-50"></i><div>${escapeHtml(message || '데이터가 없습니다')}</div></div>`;
  }
  container.innerHTML = html;
  refreshIcons();
}

function setupFeedScroll() {
  const container = document.getElementById('chat-container');
  if (!container) return;

  container.addEventListener('scroll', () => {
    feedState.scrollPos = container.scrollTop;
    isNearTop = container.scrollTop < 64;
    const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distFromBottom < 40) {
      autoScroll = true;
      missedCount = 0;
      updateScrollFab();
    } else {
      autoScroll = false;
    }
    updateFeedLoadMore();
  });
}

function updateScrollFab() {
  const fab = document.getElementById('scroll-to-bottom');
  const badge = document.getElementById('scroll-fab-badge');
  if (!fab || !badge) return;
  if (missedCount > 0) {
    badge.textContent = missedCount > 99 ? '99+' : String(missedCount);
    badge.classList.add('visible');
    fab.classList.remove('hidden');
  } else {
    badge.classList.remove('visible');
    fab.classList.add('hidden');
  }
}

function scrollToBottom() {
  const container = document.getElementById('chat-container');
  if (!container) return;
  container.scrollTop = container.scrollHeight;
  autoScroll = true;
  missedCount = 0;
  updateScrollFab();
}

function buildActivityFeedUrl(options = {}) {
  const params = new URLSearchParams();
  params.set('limit', String(options.limit || 100));
  if (options.targetDate) params.set('target_date', options.targetDate);
  if (options.beforeCreatedAt) params.set('before_created_at', options.beforeCreatedAt);
  if (options.beforeId) params.set('before_id', options.beforeId);
  return `${API}/activities/feed?${params.toString()}`;
}

function updateFeedLoadMore() {
  const bar = document.getElementById('feed-load-more-bar');
  const btn = document.getElementById('feed-load-more-btn');
  const meta = document.getElementById('feed-load-more-meta');
  if (!bar || !btn || !meta) return;

  const label = btn.querySelector('span');
  if (currentView !== 'live') {
    bar.classList.add('hidden');
    btn.disabled = false;
    if (label) label.textContent = '이전 내역 더보기';
    meta.textContent = '';
    return;
  }

  const canShow = feedState.loaded && (feedState.feedHasMore || feedState.feedLoading) && isNearTop;
  if (!canShow) {
    bar.classList.add('hidden');
    btn.disabled = false;
    if (label) label.textContent = '이전 내역 더보기';
  } else {
    bar.classList.remove('hidden');
    btn.disabled = feedState.feedLoading;
    if (label) label.textContent = feedState.feedLoading ? '불러오는 중...' : '이전 내역 더보기';
  }

  meta.textContent = feedState.feedTradingDate ? `${feedState.feedTradingDate} 거래일` : '';
}

function renderLiveFeedFromState(options = {}) {
  const {
    restoreScroll = false,
    preserveViewport = false,
    previousScrollHeight = 0,
    previousScrollTop = 0,
  } = options;

  if (currentView !== 'live') return;
  const container = document.getElementById('chat-container');
  if (!container) return;

  cleanupStockCards();
  container.innerHTML = '';
  setActivityCount(0);

  const visibleActivities = filterResolvedStarts(feedState.feedItems);
  if (!visibleActivities.length) {
    renderPlaceholder(container, 'empty', '현재 거래일 활동이 없습니다');
  } else {
    visibleActivities.forEach((activity) => appendActivity(activity, { skipStore: true }));
  }

  feedState.loaded = true;
  feedState.activityCount = visibleActivities.length;
  const countEl = document.getElementById('activity-count');
  if (countEl) countEl.textContent = `${getActivityCount()}건`;
  updateFeedLoadMore();
  refreshIcons();

  requestAnimationFrame(() => {
    if (preserveViewport) {
      const nextHeight = container.scrollHeight;
      container.scrollTop = previousScrollTop + (nextHeight - previousScrollHeight);
      return;
    }
    if (restoreScroll) {
      container.scrollTop = feedState.scrollPos || 0;
      return;
    }
    container.scrollTop = container.scrollHeight;
  });
}

async function loadMoreActivities() {
  if (feedState.feedLoading || !feedState.feedHasMore || !feedState.feedCursor) return;

  const container = document.getElementById('chat-container');
  if (!container) return;

  const previousScrollHeight = container.scrollHeight;
  const previousScrollTop = container.scrollTop;
  feedState.feedLoading = true;
  updateFeedLoadMore();

  try {
    const json = await fetchJSON(buildActivityFeedUrl({
      limit: 100,
      targetDate: feedState.feedTradingDate,
      beforeCreatedAt: feedState.feedCursor.before_created_at,
      beforeId: feedState.feedCursor.before_id,
    }));
    const feed = json.data || {};
    const page = Array.isArray(feed.items) ? [...feed.items].reverse() : [];
    rememberFeedActivities(page, { prepend: true });
    feedState.feedTradingDate = feed.resolved_trading_date || feedState.feedTradingDate;
    feedState.feedHasMore = !!feed.has_more;
    feedState.feedCursor = feed.next_cursor || null;
    renderLiveFeedFromState({
      preserveViewport: true,
      previousScrollHeight,
      previousScrollTop,
    });
  } catch (err) {
    showToast(`이전 내역 로드 실패: ${err.message}`, 'error');
  } finally {
    feedState.feedLoading = false;
    updateFeedLoadMore();
  }
}

async function loadTodayActivities() {
  const container = document.getElementById('chat-container');
  if (!container) return;

  autoScroll = true;
  missedCount = 0;
  renderPlaceholder(container, 'loading', '불러오는 중...');
  cleanupStockCards();
  resetFeedState();
  updateFeedLoadMore();
  updateScrollFab();

  try {
    const json = await fetchJSON(buildActivityFeedUrl({ limit: 100 }));
    const feed = json.data || {};
    const page = Array.isArray(feed.items) ? [...feed.items].reverse() : [];
    rememberFeedActivities(page);
    feedState.feedTradingDate = feed.resolved_trading_date || null;
    feedState.feedHasMore = !!feed.has_more;
    feedState.feedCursor = feed.next_cursor || null;
    feedState.loaded = true;
    renderLiveFeedFromState();
    setStatusText('현재 거래일 활동을 불러왔습니다', '#9ca3af');
  } catch (err) {
    renderPlaceholder(container, 'error', `로드 실패: ${err.message}`);
    updateFeedLoadMore();
    setStatusText(`활동 피드 로드 실패 · ${truncateText(err.message, 48)}`, '#f87171');
  }
}

function filterResolvedStarts(activities) {
  const resolved = new Set();
  activities.forEach((activity) => {
    if (activity.phase === 'COMPLETE' || activity.phase === 'ERROR') {
      resolved.add(getProgressKey(activity));
    }
  });
  return activities.filter((activity) => !(activity.phase === 'START' && resolved.has(getProgressKey(activity))));
}

function getProgressKey(data) {
  const match = (data.summary || '').match(/\[([^\]]+)\]/);
  const symbol = match ? match[1] : '';
  return `${data.activity_type}:${symbol}`;
}

function cleanupStockCards() {
  Object.values(feedState.stockCards).forEach((card) => {
    if (card.liveTimer) clearInterval(card.liveTimer);
  });
  feedState.stockCards = {};
}

async function loadActivityFeed() {
  await loadTodayActivities();
}

// ══════════════════════════════════════════════════════════
// ── 2. Account Balance + Holdings ──
// ══════════════════════════════════════════════════════════

async function loadBalance() {
  try {
    const json = await fetchJSON(`${API}/account/overview`);
    const data = json.data;
    if (!data) return;
    renderBalance(data.balance || data);
    renderHoldings(data.holdings || []);
    renderPendingOrders(data.pending_orders || []);
  } catch (err) {
    console.error('[coin] balance load error:', err);
    setInlineStatus('sys-exchange', '오류', '#f87171');
  }
}

function renderBalance(b) {
  if (!b) {
    setInlineStatus('sys-exchange', '데이터 없음', '#9ca3af');
    return;
  }
  const totalAsset = Number(b.total_asset ?? 0);
  const cash = Number(b.cash ?? 0);
  const coinValue = Number(b.coin_value ?? b.stock_value ?? 0);
  const lockedKrw = Number(b.locked_krw ?? 0);
  const totalPnl = Number(b.total_pnl ?? 0);
  const totalPnlRate = Number(b.total_pnl_rate ?? 0);

  updateMetricValue('total-asset', 'total-asset-delta', totalAsset, formatKRW(totalAsset));
  updateMetricValue('cash-balance', 'cash-balance-delta', cash, formatKRW(cash));
  updateMetricValue('coin-eval', 'coin-eval-delta', coinValue, formatKRW(coinValue));
  updateMetricValue('locked-krw', 'locked-krw-delta', lockedKrw, formatKRW(lockedKrw));

  const pnlEl = document.getElementById('total-pnl');
  if (pnlEl) {
    pnlEl.innerHTML = formatPnl(totalPnl, totalPnlRate);
  }

  const exchangeMessage = b.is_valid === false
    ? `오류${b.status_message ? ` · ${truncateText(b.status_message, 48)}` : ''}`
    : `정상${b.status_message ? ` · ${truncateText(b.status_message, 48)}` : ''}`;
  setInlineStatus('sys-exchange', exchangeMessage, b.is_valid === false ? '#f87171' : '#34d399');
}

function renderHoldings(holdings) {
  const container = document.getElementById('holdings-list');
  if (!container) return;

  setTextContent('holdings-count', `${(holdings && holdings.length) || 0}종목`);

  if (!holdings || !holdings.length) {
    container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">보유 코인 없음</div>';
    return;
  }

  container.innerHTML = holdings.map(h => {
    const pnlVal = Number(h.pnl ?? 0);
    const pnlRate = Number(h.pnl_rate ?? 0);
    const pnlColor = pnlVal > 0 ? '#34d399' : pnlVal < 0 ? '#f87171' : '#9ca3af';
    const pnlSign = pnlVal >= 0 ? '+' : '';
    const qty = formatCoinQty(h.quantity);
    const avgPrice = formatPrice(h.avg_buy_price);
    const curPrice = formatPrice(h.current_price);
    const evalAmt = formatKRW(h.current_price * h.quantity);
    const name = h.name || h.symbol || '';
    const symbol = h.symbol || '';

    return `
      <div class="holding-card" style="background:rgba(30,30,46,0.6); border-radius:8px; padding:10px 12px; margin-bottom:6px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div style="min-width:0;">
            <div style="color:#e2e8f0; font-weight:500; font-size:13px;">${escapeHtml(name)}</div>
            <div style="color:#6b7280; font-size:11px;">${escapeHtml(symbol)} · ${qty}</div>
          </div>
          <span style="color:${pnlColor}; font-weight:500; font-size:13px;">${pnlSign}${pnlRate.toFixed(2)}%</span>
        </div>
        <div style="display:flex; justify-content:space-between; color:#9ca3af; font-size:11px; margin-top:4px;">
          <span>평단 ${avgPrice}</span>
          <span>현재 ${curPrice}</span>
        </div>
        <div style="display:flex; justify-content:space-between; color:#6b7280; font-size:11px; margin-top:2px;">
          <span>평가 ${evalAmt}</span>
          <span style="color:${pnlColor};">${pnlSign}${formatKRW(pnlVal)}</span>
        </div>
      </div>
    `;
  }).join('');
}

function formatPendingOrderTime(order) {
  if (order?.updated_at) return formatTime(order.updated_at);
  if (order?.submitted_at) return formatTime(order.submitted_at);
  const orderTime = String(order?.order_time || '');
  if (orderTime.length === 6) {
    return `${orderTime.slice(0, 2)}:${orderTime.slice(2, 4)}:${orderTime.slice(4, 6)}`;
  }
  return '';
}

function getPendingOrderStatusMeta(status) {
  const normalized = String(status || '').toUpperCase();
  switch (normalized) {
    case 'PARTIAL':
      return { label: '부분체결', className: 'status-partial' };
    case 'FILLED':
      return { label: '체결완료', className: 'status-filled' };
    case 'CANCELED':
      return { label: '취소', className: 'status-canceled' };
    case 'OPEN':
      return { label: '대기', className: '' };
    case 'SUBMITTED':
      return { label: '접수', className: '' };
    default:
      return { label: normalized || '대기', className: '' };
  }
}

function renderPendingOrders(orders) {
  const container = document.getElementById('pending-list');
  const countEl = document.getElementById('pending-count');
  if (!container || !countEl) return;

  if (!orders || !orders.length) {
    countEl.textContent = '0건';
    container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">미체결 주문 없음</div>';
    return;
  }

  const totalAmount = orders.reduce((sum, order) => (
    sum + (Number(order.order_price || 0) * Number(order.remaining_qty || 0))
  ), 0);
  countEl.textContent = `${orders.length}건 (${formatKRW(totalAmount)})`;

  container.innerHTML = orders.map((order) => {
    const isBuy = order.side === '매수';
    const sideClass = isBuy ? 'buy' : 'sell';
    const statusMeta = getPendingOrderStatusMeta(order.status);
    const updatedAt = formatPendingOrderTime(order);
    const remainingQty = formatCoinQty(order.remaining_qty);
    const orderQty = formatCoinQty(order.order_qty);
    const filledQty = formatCoinQty(order.filled_qty);
    const statusDetail = order.status_detail
      ? `<div class="text-[10px] text-gray-500 truncate" title="${escapeHtml(order.status_detail)}">${escapeHtml(order.status_detail)}</div>`
      : '';
    return `
      <div class="coin-pending-card">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="text-gray-100 font-medium truncate" title="${escapeHtml(order.symbol)}">${escapeHtml(order.name || order.symbol)}</div>
            <div class="text-[11px] text-gray-500">${escapeHtml(order.symbol || '')}</div>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <span class="coin-side-chip ${sideClass}">${escapeHtml(order.side || '')}</span>
            <span class="coin-status-chip ${statusMeta.className}">${escapeHtml(statusMeta.label)}</span>
          </div>
        </div>
        <div class="flex justify-between items-center gap-2 mt-1.5 text-[11px] text-gray-300">
          <span>주문가</span>
          <span>${formatPrice(order.order_price)} KRW</span>
        </div>
        <div class="flex justify-between items-center gap-2 text-[11px] text-gray-400 mt-1">
          <span>미체결 ${remainingQty} / 주문 ${orderQty}</span>
          <span>${formatKRW(Number(order.order_price || 0) * Number(order.remaining_qty || 0))}</span>
        </div>
        <div class="flex justify-between items-center gap-2 text-[11px] text-gray-500 mt-1">
          <span>체결 ${filledQty}</span>
          <span>${escapeHtml(updatedAt || '')}</span>
        </div>
        ${statusDetail}
      </div>
    `;
  }).join('');
}

// ══════════════════════════════════════════════════════════
// ── 3. Recommendations (SEMI_AUTO) ──
// ══════════════════════════════════════════════════════════

async function loadRecommendations() {
  try {
    const json = await fetchJSON(`${API}/recommendations?status=PENDING`);
    const data = json.data || [];
    renderRecommendations(data);
  } catch (err) {
    console.error('[coin] recommendations load error:', err);
  }
}

function renderRecommendations(recs) {
  const container = document.getElementById('rec-queue');
  const countEl = document.getElementById('rec-count');
  if (!container) return;
  if (countEl) countEl.textContent = String((recs && recs.length) || 0);

  if (!recs || !recs.length) {
    container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">대기 중인 추천 없음</div>';
    return;
  }

  container.innerHTML = recs.map(r => {
    const isBuy = (r.side || r.action || '').toUpperCase().includes('BUY');
    const borderColor = isBuy ? '#fbbf24' : '#a78bfa';
    const sideLabel = isBuy ? 'BUY' : 'SELL';
    const sideColor = isBuy ? '#fbbf24' : '#a78bfa';
    const coin = r.coin || r.symbol || '';
    const priceValue = r.suggested_price ?? r.price;
    const qtyValue = r.suggested_quantity ?? r.quantity;
    const amountValue = r.suggested_amount_krw ?? r.amount_krw ?? (
      Number(priceValue) > 0 && Number(qtyValue) > 0
        ? Number(priceValue) * Number(qtyValue)
        : null
    );
    const price = formatPrice(priceValue);
    const qty = formatCoinQty(qtyValue);
    const amount = formatKRW(amountValue);
    const confidence = r.confidence != null ? `${(Number(r.confidence) * 100).toFixed(0)}%` : '-';
    const expiresAt = r.expires_at ? new Date(r.expires_at) : null;
    const recId = r.id || r.recommendation_id || '';

    return `
      <div class="rec-card" style="border-left:3px solid ${borderColor}; background:rgba(30,30,46,0.6); border-radius:8px; padding:10px 12px; margin-bottom:8px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <div>
            <span style="color:#e2e8f0; font-weight:600; font-size:14px;">${escapeHtml(coin)}</span>
            <span style="color:${sideColor}; font-weight:500; font-size:12px; margin-left:6px; padding:1px 6px; border-radius:4px; background:${isBuy ? 'rgba(251,191,36,0.15)' : 'rgba(167,139,250,0.15)'};">${sideLabel}</span>
          </div>
          <span style="color:#9ca3af; font-size:11px;">신뢰도 ${confidence}</span>
        </div>
        <div style="display:flex; justify-content:space-between; color:#9ca3af; font-size:12px; margin-bottom:6px;">
          <span>금액 ${amount}</span>
          <span>예상수량 ${qty}</span>
        </div>
        <div style="display:flex; justify-content:space-between; color:#6b7280; font-size:11px; margin-bottom:6px;">
          <span>가격 ${price}</span>
          <span>${isBuy ? '금액 기준 BUY' : '수량 기준 SELL'}</span>
        </div>
        ${expiresAt ? `<div style="color:#6b7280; font-size:11px; margin-bottom:6px;" data-expires="${expiresAt.toISOString()}" class="rec-countdown">만료: 계산 중...</div>` : ''}
        <div style="display:flex; gap:8px;">
          <button onclick="approveRecommendation('${recId}')"
            style="flex:1; padding:5px 0; border-radius:6px; border:none; cursor:pointer; font-size:12px; font-weight:500; background:rgba(52,211,153,0.15); color:#34d399;"
            onmouseover="this.style.background='rgba(52,211,153,0.3)'"
            onmouseout="this.style.background='rgba(52,211,153,0.15)'">
            <i data-lucide="check" class="w-3.5 h-3.5 inline-block"></i> 승인
          </button>
          <button onclick="rejectRecommendation('${recId}')"
            style="flex:1; padding:5px 0; border-radius:6px; border:none; cursor:pointer; font-size:12px; font-weight:500; background:rgba(248,113,113,0.15); color:#f87171;"
            onmouseover="this.style.background='rgba(248,113,113,0.3)'"
            onmouseout="this.style.background='rgba(248,113,113,0.15)'">
            <i data-lucide="x" class="w-3.5 h-3.5 inline-block"></i> 거절
          </button>
        </div>
      </div>
    `;
  }).join('');

  refreshIcons();
  updateRecCountdowns();
}

async function approveRecommendation(id) {
  try {
    await fetchJSON(`${API}/recommendations/${id}/approve`, { method: 'POST' });
    loadRecommendations();
  } catch (err) {
    console.error('[coin] approve error:', err);
  }
}

async function rejectRecommendation(id) {
  try {
    await fetchJSON(`${API}/recommendations/${id}/reject`, { method: 'POST' });
    loadRecommendations();
  } catch (err) {
    console.error('[coin] reject error:', err);
  }
}

function updateRecCountdowns() {
  const els = document.querySelectorAll('.rec-countdown');
  els.forEach(el => {
    const expires = el.dataset.expires;
    if (!expires) return;
    const remaining = Math.max(0, Math.floor((new Date(expires).getTime() - Date.now()) / 1000));
    if (remaining <= 0) {
      el.textContent = '만료됨';
      el.style.color = '#f87171';
    } else {
      el.textContent = `만료: ${formatDuration(remaining)} 후`;
    }
  });
}

// ══════════════════════════════════════════════════════════
// ── 4. System Status ──
// ══════════════════════════════════════════════════════════

let lastCycleTime = null;
let scanIntervalHours = null;

async function loadSystemStatus() {
  try {
    const json = await fetchJSON(`${API}/system/status`);
    const s = json.data;
    if (!s) return;
    lastSystemStatus = s;
    const tradingEnabled = s.trading_enabled ?? s.crypto_trading_enabled;
    const autonomyMode = s.autonomy_mode ?? s.crypto_autonomy_mode ?? 'SEMI_AUTO';
    const sseClients = Number(s.sse_clients ?? s.coin_sse_clients ?? 0);
    const todayTrades = Number(s.today_filled_order_count ?? 0);
    const uptime = formatDuration(Number(s.uptime_seconds ?? 0));

    // Header badges
    updateBadge('badge-crypto',
      s.crypto_enabled ? 'CRYPTO:ON' : 'CRYPTO:OFF',
      s.crypto_enabled ? 'green' : 'red');
    updateBadge('badge-trading',
      tradingEnabled ? '매매:ON' : '매매:OFF',
      tradingEnabled ? 'green' : 'red');

    const modeLabels = { AUTONOMOUS: '자동', FULL_AUTO: '자동', SEMI_AUTO: '반자동', MANUAL: '수동' };
    const modeColors = { AUTONOMOUS: 'green', FULL_AUTO: 'green', SEMI_AUTO: 'yellow', MANUAL: 'gray' };
    const mode = autonomyMode;
    updateBadge('badge-mode',
      modeLabels[mode] || mode,
      modeColors[mode] || 'gray');
    const recSection = document.getElementById('rec-queue-section');
    if (recSection) recSection.classList.toggle('hidden', mode !== 'SEMI_AUTO');

    // Track scan timeline data
    if (s.last_cycle_time) lastCycleTime = new Date(s.last_cycle_time).getTime();
    if (s.scan_interval_hours) scanIntervalHours = Number(s.scan_interval_hours);
    if (s.scan_interval_hours) {
      setTextContent('scan-cycle-label', `${Number(s.scan_interval_hours)}h 주기`);
    }

    setInlineStatus(
      'sys-scan-engine',
      s.scheduler_running ? `동작 · ${Number(s.scan_interval_hours || 0)}h 주기` : '중지',
      s.scheduler_running ? '#34d399' : '#fbbf24'
    );
    setInlineStatus(
      'sys-agent',
      s.last_cycle_status
        ? `${s.agent_running ? '준비됨' : '중지'} · 마지막 ${s.last_cycle_status}`
        : (s.agent_running ? '준비됨' : '중지'),
      s.last_cycle_error ? '#f87171' : (s.agent_running ? '#34d399' : '#fbbf24')
    );
    setInlineStatus('sys-sse', `${sseClients}명 구독`, sseClients > 0 ? '#34d399' : '#9ca3af');
    const realtime = s.realtime || {};
    const desiredCount = Number(realtime.desired_count || 0);
    const activeCount = Number(realtime.subscription_count || realtime.active_count || 0);
    const realtimeLabel = realtime.connected
      ? `연결 · ${activeCount}/${desiredCount || activeCount}`
      : `${desiredCount ? `${desiredCount}종목 대기` : '감시 대기'}`;
    setInlineStatus(
      'sys-realtime',
      realtimeLabel,
      realtime.connected ? '#34d399' : (realtime.last_connect_error ? '#f87171' : '#9ca3af')
    );
    const privateSync = s.private_sync || {};
    const privateLabel = privateSync.connected
      ? `연결${privateSync.last_order_message_at ? ` · 주문 ${formatTimeAgo(privateSync.last_order_message_at)}` : ''}${privateSync.last_asset_message_at ? ` · 자산 ${formatTimeAgo(privateSync.last_asset_message_at)}` : ''}`
      : (privateSync.configured === false ? '미설정' : (privateSync.last_error ? `오류 · ${truncateText(privateSync.last_error, 24)}` : '대기'));
    setInlineStatus(
      'sys-private-sync',
      privateLabel,
      privateSync.connected ? '#34d399' : (privateSync.last_error ? '#f87171' : '#9ca3af')
    );
    setInlineStatus('sys-uptime', uptime, '#d1d5db');
    setInlineStatus('sys-trades-today', `${todayTrades}건`, todayTrades > 0 ? '#fbbf24' : '#9ca3af');
    if (typeof s.watchlist_count === 'number') {
      setTextContent('watchlist-count', `${s.watchlist_count}종목`);
    }

    const sessionLabel = getSessionLabel(s.market_session);
    if (s.last_cycle_error) {
      setStatusText(`마지막 사이클 오류 · ${truncateText(s.last_cycle_error, 88)}`, '#f87171');
    } else {
      const cycleState = s.last_cycle_status ? ` · 마지막 ${s.last_cycle_status}` : '';
      setStatusText(`${sessionLabel} · 스캔 ${s.scheduler_running ? '동작' : '중지'}${cycleState}`, '#9ca3af');
    }

    updateScanTimeline();
    renderScanScheduleSummary(s);
    renderTriggerButton();
  } catch (err) {
    console.error('[coin] system status error:', err);
  }
}

function getSessionLabel(session) {
  const labels = {
    CRYPTO_ACTIVE: '24/7 ACTIVE',
    CLOSED: '대기',
  };
  return labels[session] || session || '대기';
}

async function loadAgentState(options = {}) {
  const { quiet = false } = options;
  try {
    const json = await fetchJSON(`${API}/agent/state`);
    const state = json.data || {};
    handleAgentStateEvent(state);
  } catch (err) {
    if (!quiet) console.error('[coin] agent state error:', err);
    renderTriggerButton();
  }
}

function renderScanScheduleSummary(status) {
  const container = document.getElementById('scan-schedule');
  if (!container || !status) return;

  const nextScanTs = lastCycleTime && scanIntervalHours
    ? new Date(lastCycleTime + scanIntervalHours * 3600 * 1000).toISOString()
    : null;
  const sessionLabel = getSessionLabel(status.market_session);

  container.innerHTML = `
    <div class="text-xs text-gray-300 space-y-2">
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">세션</span>
        <span class="text-gray-300">${escapeHtml(sessionLabel)}</span>
      </div>
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">주기</span>
        <span class="text-gray-300">${escapeHtml(scanIntervalHours ? `${scanIntervalHours}시간 고정` : '--')}</span>
      </div>
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">최근 상태</span>
        <span class="text-gray-300">${escapeHtml(status.last_cycle_status || '대기')}</span>
      </div>
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">다음 예정</span>
        <span class="text-gray-300">${escapeHtml(nextScanTs ? formatDateTime(nextScanTs) : '--')}</span>
      </div>
    </div>
  `;
}

// ══════════════════════════════════════════════════════════
// ── 5. AI Watchlist ──
// ══════════════════════════════════════════════════════════

async function loadWatchlist() {
  try {
    const json = await fetchJSON(`${API}/watchlist`);
    const data = json.data || {};
    renderWatchlist(data);
  } catch (err) {
    console.error('[coin] watchlist load error:', err);
  }
}

function renderWatchlist(data) {
  const container = document.getElementById('watchlist-list');
  const dot = document.getElementById('watchlist-stream-dot');
  if (!container) return;

  const payload = Array.isArray(data) ? { symbols: data } : (data || {});
  const items = Array.isArray(payload.symbols) ? payload.symbols : [];
  const stream = payload.stream_status || null;
  setTextContent('watchlist-count', `${items.length}종목`);
  if (dot) {
    const dotClass = stream
      ? (stream.connected ? 'bg-green-400' : (items.length ? 'bg-red-400' : 'bg-gray-600'))
      : (items.length ? 'bg-green-400' : 'bg-gray-600');
    dot.className = `w-1.5 h-1.5 rounded-full ${dotClass}`;
  }

  const streamSummary = stream ? (() => {
    const subscriptionLimit = Number(stream.subscription_limit || 0);
    const subscriptionCount = Number(stream.subscription_count || 0);
    const pct = subscriptionLimit > 0
      ? Math.max(0, Math.min(100, Math.round((subscriptionCount / subscriptionLimit) * 100)))
      : 0;
    const statusColor = stream.connected ? '#34d399' : (stream.last_connect_error ? '#f87171' : '#9ca3af');
    const stateText = stream.connected ? '연결됨' : (stream.last_connect_error ? '연결 오류' : '대기');
    const lastSeen = stream.last_message_at ? formatTimeAgo(stream.last_message_at) : '';
    return `
      <div class="coin-watchlist-stream">
        <div class="coin-watchlist-gauge">
          <span style="min-width:18px; color:${statusColor};">WS</span>
          <span class="coin-watchlist-gauge-track"><span class="coin-watchlist-gauge-fill" style="width:${pct}%"></span></span>
          <span>${subscriptionCount}/${subscriptionLimit || 0}</span>
        </div>
        <div class="flex items-center justify-between mt-2 text-[10px] text-gray-500">
          <span style="color:${statusColor};">${escapeHtml(stateText)}</span>
          <span>${escapeHtml(lastSeen ? `${lastSeen} 수신` : '수신 이력 없음')}</span>
        </div>
      </div>
    `;
  })() : '';

  if (!items.length) {
    container.innerHTML = `${streamSummary}<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">감시 코인 없음</div>`;
    return;
  }

  container.innerHTML = streamSummary + items.map(s => {
    const sym = typeof s === 'string' ? s : (s.symbol || '');
    const name = typeof s === 'string' ? s : (s.name || s.symbol || '');
    const reason = typeof s === 'object' && s.reason ? s.reason : '';
    const price = typeof s === 'object' && s.price != null ? formatPrice(s.price) : '';
    const changeRate = typeof s === 'object' && s.change_rate != null ? Number(s.change_rate) : null;
    const changeColor = changeRate == null ? '#6b7280' : (changeRate >= 0 ? '#34d399' : '#f87171');
    const scanSource = typeof s === 'object' ? formatCoinScanSource(s.scan_source) : '';
    const isHolding = typeof s === 'object' && !!s.is_holding;
    const isSubscribed = typeof s === 'object' && !!s.is_subscribed;
    const statusLabel = isHolding ? '보유' : (isSubscribed ? '감시' : '대기');
    const statusClass = isHolding ? 'holding' : (isSubscribed ? 'watching' : 'pending');
    const thresholds = typeof s === 'object' ? (s.thresholds || null) : null;
    const thresholdItems = [];
    if (thresholds) {
      if (thresholds.surge_pct != null) thresholdItems.push(`<span class="coin-watchlist-threshold">급등 +${Number(thresholds.surge_pct).toFixed(1)}%</span>`);
      if (thresholds.drop_pct != null) thresholdItems.push(`<span class="coin-watchlist-threshold">급락 ${Number(thresholds.drop_pct).toFixed(1)}%</span>`);
      if (thresholds.volume_spike_ratio) thresholdItems.push(`<span class="coin-watchlist-threshold">거래량 x${Number(thresholds.volume_spike_ratio).toFixed(1)}</span>`);
      if (Number(thresholds.stop_loss || 0) > 0) thresholdItems.push(`<span class="coin-watchlist-threshold">SL ${escapeHtml(formatPrice(thresholds.stop_loss))}</span>`);
      if (Number(thresholds.take_profit || 0) > 0) thresholdItems.push(`<span class="coin-watchlist-threshold">TP ${escapeHtml(formatPrice(thresholds.take_profit))}</span>`);
      if (Number(thresholds.trailing_stop_pct || 0) > 0) thresholdItems.push(`<span class="coin-watchlist-threshold">Trail ${Number(thresholds.trailing_stop_pct).toFixed(1)}%</span>`);
    }

    return `
      <div class="coin-watchlist-card">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 min-w-0">
              <span style="color:#e2e8f0; font-weight:600;">${escapeHtml(sym)}</span>
              ${name && sym !== name ? `<span style="color:#9ca3af; font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(name)}</span>` : ''}
              ${scanSource ? `<span style="color:#a78bfa; font-size:10px;">${escapeHtml(scanSource)}</span>` : ''}
            </div>
            ${reason ? `<div style="color:#6b7280; font-size:11px; margin-top:3px;">${escapeHtml(reason)}</div>` : ''}
            ${price ? `<div style="color:#9ca3af; font-size:11px; margin-top:3px;">현재가 ${escapeHtml(price)}</div>` : ''}
            ${thresholdItems.length ? `<div class="coin-watchlist-thresholds">${thresholdItems.join('')}</div>` : ''}
          </div>
          <div class="text-right shrink-0">
            <span class="coin-watchlist-chip ${statusClass}">${statusLabel}</span>
            ${changeRate != null ? `<div style="color:${changeColor}; font-size:11px; margin-top:6px;">${changeRate >= 0 ? '+' : ''}${changeRate.toFixed(2)}%</div>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// ══════════════════════════════════════════════════════════
// ── 6. Settings ──
// ══════════════════════════════════════════════════════════

async function loadLatestReport() {
  try {
    const json = await fetchJSON(`${API}/reports/latest`);
    renderLatestReport(json.data || null);
  } catch (err) {
    console.error('[coin] latest report error:', err);
    renderLatestReport(null);
  }
}

function renderLatestReport(report) {
  const container = document.getElementById('latest-report');
  if (!container) return;
  if (!report) {
    container.innerHTML = '<div class="text-gray-500 text-xs py-1">최근 리포트 없음</div>';
    return;
  }

  const pnl = Number(report.total_pnl ?? 0);
  const pnlColor = pnl >= 0 ? '#34d399' : '#f87171';
  const summary = truncateText(report.market_summary || report.performance_review || report.lessons_learned || '요약 없음', 120);

  container.innerHTML = `
    <div class="space-y-2">
      <div class="flex items-center justify-between gap-2">
        <span class="text-gray-300">${escapeHtml(report.report_date || '--')}</span>
        <span style="color:${pnlColor};">${escapeHtml(formatKRW(pnl))}</span>
      </div>
      <div class="text-[11px] text-gray-500 whitespace-pre-wrap">${escapeHtml(summary)}</div>
    </div>
  `;
}

async function loadSettings() {
  try {
    const json = await fetchJSON(`${API}/settings`);
    const data = json.data;
    if (!data) return;
    renderSettings(data);
  } catch (err) {
    console.error('[coin] settings load error:', err);
  }
}

function renderSettings(settings) {
  const el = (id) => document.getElementById(id);
  if (settings.CRYPTO_TRADING_ENABLED != null && el('set-trading')) el('set-trading').checked = !!settings.CRYPTO_TRADING_ENABLED;
  if (settings.CRYPTO_ENABLED != null && el('set-auto-scan')) el('set-auto-scan').checked = !!settings.CRYPTO_ENABLED;
  if (settings.CRYPTO_AUTONOMY_MODE && el('set-mode')) el('set-mode').value = settings.CRYPTO_AUTONOMY_MODE;
  if (settings.CRYPTO_SCAN_INTERVAL_HOURS != null && el('set-risk')) el('set-risk').value = String(settings.CRYPTO_SCAN_INTERVAL_HOURS);
}

async function updateSetting(key, value) {
  try {
    await fetchJSON(`${API}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
    // Reload to reflect changes
    loadSettings();
    loadSystemStatus();
  } catch (err) {
    console.error('[coin] setting update error:', err);
  }
}

// ══════════════════════════════════════════════════════════
// ── 7. Manual Scan Trigger ──
// ══════════════════════════════════════════════════════════

async function triggerScan() {
  const btn = document.getElementById('btn-trigger-scan');
  if (!btn) return;
  if (manualScanRequestPending || lastAgentState?.cycle_active || (manualScanQueuedAt && (Date.now() - manualScanQueuedAt) < 10000)) {
    return;
  }

  manualScanRequestPending = true;
  clearManualScanNotice();
  renderTriggerButton();

  try {
    const json = await fetchJSON(`${API}/agent/trigger`, { method: 'POST' });
    const data = json.data;
    const msg = json.message || (data && data.message) || '스캔 트리거 완료';

    if (data?.skipped) {
      const reason = data.reason || 'skipped';
      manualScanQueuedAt = null;
      manualScanSessionObserved = false;
      setManualScanNotice(`스캔 스킵 · ${reason}`, 'pause-circle', 'bg-yellow-900/20 text-yellow-300 border-yellow-400/20', 4500);
      showToast(msg, 'info');
    } else {
      manualScanQueuedAt = Date.now();
      manualScanSessionObserved = true;
      lastCycleTime = Date.now();
      updateScanTimeline();
      setStatusText('수동 스캔 요청 접수됨', '#fbbf24');
      showToast(msg, 'success');
    }

    setTimeout(loadAgentState, 300);
    setTimeout(loadSystemStatus, 600);
  } catch (err) {
    console.error('[coin] scan trigger error:', err);
    manualScanQueuedAt = null;
    manualScanSessionObserved = false;
    setManualScanNotice('요청 실패', 'x', 'bg-red-900/20 text-red-300 border-red-400/20', 5000);
    setStatusText(`수동 스캔 요청 실패 · ${truncateText(err.message, 80)}`, '#f87171');
    showToast(`수동 스캔 실패: ${err.message}`, 'error');
  } finally {
    manualScanRequestPending = false;
    renderTriggerButton();
  }
}

// ══════════════════════════════════════════════════════════
// ── 8. Scan Timeline ──
// ══════════════════════════════════════════════════════════

function updateScanTimeline() {
  const progressBar = document.getElementById('scan-progress-fill');
  const countdown = document.getElementById('scan-countdown');
  const lastTimeEl = document.getElementById('scan-last-time');
  const nextTimeEl = document.getElementById('scan-next-time');
  if (!progressBar && !countdown) return;

  if (!lastCycleTime || !scanIntervalHours) {
    if (countdown) countdown.textContent = '스캔 정보 없음';
    if (progressBar) progressBar.style.width = '0%';
    if (lastTimeEl) lastTimeEl.textContent = '마지막: --';
    if (nextTimeEl) nextTimeEl.textContent = '다음: --';
    return;
  }

  const intervalMs = scanIntervalHours * 3600 * 1000;
  const nextScanTime = lastCycleTime + intervalMs;
  const now = Date.now();
  const elapsed = now - lastCycleTime;
  const remaining = Math.max(0, nextScanTime - now);
  const progress = Math.min(100, (elapsed / intervalMs) * 100);

  if (progressBar) {
    progressBar.style.width = `${progress}%`;
    // Color: green -> yellow -> red as it approaches
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
      countdown.textContent = `다음 스캔 ${formatDuration(remaining / 1000)} 후`;
      countdown.style.color = '#d1d5db';
    }
  }

  if (lastTimeEl) lastTimeEl.textContent = `마지막: ${formatDateTime(lastCycleTime)}`;
  if (nextTimeEl) nextTimeEl.textContent = `다음: ${formatDateTime(nextScanTime)}`;
}

function startScanCountdown() {
  if (scanCountdownTimer) clearInterval(scanCountdownTimer);
  scanCountdownTimer = setInterval(() => {
    updateScanTimeline();
    updateRecCountdowns();
  }, 1000);
}

// ══════════════════════════════════════════════════════════
// ── Helper ──
// ══════════════════════════════════════════════════════════

function setTextContent(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function scheduleAccountOverviewRefresh(delay = 250) {
  if (accountRefreshTimer) clearTimeout(accountRefreshTimer);
  accountRefreshTimer = setTimeout(() => {
    accountRefreshTimer = null;
    loadBalance();
  }, delay);
}

function flashMetricDelta(deltaId, deltaValue) {
  const el = document.getElementById(deltaId);
  if (!el) return;

  const absDelta = Math.abs(Number(deltaValue || 0));
  if (absDelta < 1) {
    el.textContent = '';
    el.className = 'metric-delta';
    return;
  }

  const isPositive = deltaValue > 0;
  el.textContent = `${isPositive ? '+' : '-'}${formatKRW(absDelta)}`;
  el.className = `metric-delta show ${isPositive ? 'positive' : 'negative'}`;

  if (metricDeltaTimers[deltaId]) clearTimeout(metricDeltaTimers[deltaId]);
  metricDeltaTimers[deltaId] = setTimeout(() => {
    el.className = 'metric-delta';
    el.textContent = '';
    delete metricDeltaTimers[deltaId];
  }, 3000);
}

function updateMetricValue(id, deltaId, numericValue, formattedValue, threshold = 1) {
  const el = document.getElementById(id);
  if (!el) return;

  const prevValue = Number(el.dataset.value);
  el.textContent = formattedValue;
  el.dataset.value = String(numericValue);
  if (!Number.isNaN(prevValue) && Math.abs(numericValue - prevValue) >= threshold) {
    flashMetricDelta(deltaId, numericValue - prevValue);
  }
}

// ── Sidebar Toggle ──

function toggleLeftSidebar() {
  const sb = document.getElementById('left-sidebar');
  if (sb) sb.classList.toggle('collapsed');
}

function toggleRightSidebar() {
  const sb = document.getElementById('right-sidebar');
  if (sb) sb.classList.toggle('collapsed');
}

// ── Section Toggle ──

function toggleSection(name) {
  const arrow = document.getElementById(`${name}-arrow`);
  const body = document.getElementById(`${name}-list`);
  if (!body) return;
  body.classList.toggle('collapsed-section');
  if (arrow) arrow.classList.toggle('collapsed-icon');
}

function toggleSettings() {
  const body = document.getElementById('settings-body');
  const arrow = document.getElementById('settings-arrow');
  if (!body) return;
  const hidden = body.classList.toggle('hidden');
  if (arrow) arrow.style.transform = hidden ? 'rotate(-90deg)' : 'rotate(0deg)';
}

// ── Feed Filter / Actions ──

function setLogFilter(filter) {
  if (currentLogFilter === filter) return;
  currentLogFilter = filter;
  document.querySelectorAll('.log-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });
  Object.values(getStockCards()).forEach((card) => {
    applyFilterToCard(card);
  });
  document.querySelectorAll('.chat-bubble').forEach((el) => {
    if (filter === 'SIGNAL') {
      el.style.display = el.textContent.includes('매수') || el.textContent.includes('매도') || el.textContent.includes('BUY') || el.textContent.includes('SELL')
        ? ''
        : 'none';
    } else {
      el.style.display = '';
    }
  });
  if (autoScroll) {
    const container = document.getElementById('chat-container');
    if (container) container.scrollTop = container.scrollHeight;
  }
}

function clearChat() {
  if (!confirm('화면을 비울까요? (DB는 유지됩니다)')) return;
  const container = document.getElementById('chat-container');
  if (!container) return;
  container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">화면을 비웠습니다. 새 활동이 들어오면 여기에 표시됩니다.</div>';
  resetFeedState({ preserveLoaded: true });
  cleanupStockCards();
  autoScroll = true;
  missedCount = 0;
  feedState.loaded = true;
  setActivityCount(0);
  setTextContent('activity-count', '0건');
  updateFeedLoadMore();
  updateScrollFab();
  setStatusText('활동 피드를 비웠습니다', '#9ca3af');
}

function clearFeed() {
  clearChat();
}

function refreshFeed() {
  loadTodayActivities();
}

// ══════════════════════════════════════════════════════════
// ── Init ──
// ══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  // Setup
  setupFeedScroll();
  renderTriggerButton();
  const fab = document.getElementById('scroll-to-bottom');
  if (fab) fab.addEventListener('click', scrollToBottom);
  const scanBtn = document.getElementById('btn-trigger-scan');
  if (scanBtn) scanBtn.addEventListener('click', triggerScan);

  // SSE
  connectSSE();

  // Initial loads
  loadSystemStatus();
  initAgentMonitor();
  loadBalance();
  loadRecommendations();
  loadWatchlist();
  loadSettings();
  loadTodayActivities();
  loadLatestReport();

  // Polling intervals
  setInterval(loadBalance, 30000);          // 30s
  setInterval(loadRecommendations, 10000);  // 10s
  setInterval(loadSystemStatus, 60000);     // 60s
  setInterval(loadAgentState, 15000);       // 15s
  setInterval(loadWatchlist, 120000);       // 2min
  setInterval(loadLatestReport, 300000);    // 5min

  // Countdown ticker (scan timeline + recommendation expiry)
  startScanCountdown();
});
