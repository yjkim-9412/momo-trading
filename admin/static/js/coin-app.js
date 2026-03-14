/**
 * MOMO Trading — Coin Admin Dashboard
 * 코인 전용 관리자 대시보드 (24/7, 독립 구현)
 */

const API = '/api/v1/admin-coin';

// ── State ──
let eventSource = null;
let autoScroll = true;
let missedCount = 0;
let scanCountdownTimer = null;
let lastSystemStatus = null;
let lastAgentState = null;
let manualScanRequestPending = false;
let manualScanQueuedAt = null;
let manualScanNotice = null;
let manualScanSessionObserved = false;
let lastManualCycleActive = false;
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

function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.prepend(toast);

  while (container.children.length > 5) {
    container.lastElementChild?.remove();
  }

  window.setTimeout(() => {
    toast.classList.add('toast-fade-out');
  }, Math.max(0, duration - 300));

  window.setTimeout(() => {
    toast.remove();
  }, duration);
}

function getTypeColor(type) {
  const map = {
    CYCLE: '#60a5fa',
    SCAN: '#22d3ee',
    SCREENING: '#a78bfa',
    TIER1_ANALYSIS: '#fbbf24',
    TIER2_REVIEW: '#34d399',
    DECISION: '#34d399',
    ORDER: '#f87171',
    TRADE_RESULT: '#34d399',
    RISK_GATE: '#f87171',
    EVENT: '#9ca3af',
    REPORT: '#a78bfa',
    LLM_CALL: '#67e8f9',
  };
  return map[type] || '#9ca3af';
}

function getActivityOutcome(data) {
  const summary = String(data.summary || '').toUpperCase();
  if (data.phase === 'ERROR' || data.error_message) return 'error';
  if (summary.includes('BUY') || summary.includes('매수')) return 'buy';
  if (summary.includes('SELL') || summary.includes('매도')) return 'sell';
  if (summary.includes('HOLD') || summary.includes('관망')) return 'hold';
  return 'progress';
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

// ══════════════════════════════════════════════════════════
// ── 1. SSE Connection + Activity Feed ──
// ══════════════════════════════════════════════════════════

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
    updateBadge('badge-sse', 'SSE:ON', 'green');
    setStatusText('실시간 스트림 연결됨', '#34d399');
  };

  es.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'connected') return;

      if (msg.type === 'activity') {
        appendActivity(msg.data || msg);
        // Refresh account on trade events
        if (msg.data && msg.data.phase === 'COMPLETE' &&
            ['DECISION', 'ORDER', 'TRADE_RESULT'].includes(msg.data.activity_type)) {
          setTimeout(loadBalance, 2000);
        }
        // Refresh watchlist on scan/decision events
        if (msg.data && msg.data.phase === 'COMPLETE' &&
            ['SCAN', 'CYCLE', 'DECISION'].includes(msg.data.activity_type)) {
          setTimeout(loadWatchlist, 1500);
        }
        if (msg.data && ['CYCLE', 'SCAN', 'TIER1_ANALYSIS', 'TIER2_REVIEW', 'DECISION'].includes(msg.data.activity_type)) {
          setTimeout(loadAgentState, 700);
        }
        if (msg.data && ['CYCLE', 'DECISION', 'ORDER', 'TRADE_RESULT'].includes(msg.data.activity_type)) {
          setTimeout(loadSystemStatus, 900);
        }
      }

      if (msg.type === 'account_changed') {
        loadBalance();
        loadWatchlist();
      }
    } catch (err) {
      console.error('[coin-sse] parse error', err);
    }
  };

  es.onerror = () => {
    updateBadge('badge-sse', 'SSE:OFF', 'red');
    setStatusText('실시간 스트림 재연결 중', '#f87171');
    setTimeout(() => {
      if (es.readyState === EventSource.CLOSED) connectSSE();
    }, 3000);
  };
}

function appendActivity(data) {
  const feed = document.getElementById('feed-container');
  if (!feed) return;
  const card = createBubble(data);

  // Apply current filter
  if (currentFilter !== 'ALL') {
    const type = data.activity_type || '';
    if (currentFilter === 'SIGNAL' && !['SCAN', 'DECISION', 'TIER1_ANALYSIS', 'TIER2_REVIEW', 'RISK_GATE'].includes(type)) {
      card.style.display = 'none';
    } else if (currentFilter === 'TRADE' && !['ORDER', 'TRADE_RESULT'].includes(type)) {
      card.style.display = 'none';
    }
  }

  feed.appendChild(card);
  refreshIcons();

  // Update activity count
  const total = feed.querySelectorAll('.activity-card').length;
  setTextContent('activity-count', `${total}건`);

  // Auto-scroll logic
  if (autoScroll) {
    feed.scrollTop = feed.scrollHeight;
  } else {
    missedCount++;
    updateScrollFab();
  }
}

function createBubble(data) {
  const card = document.createElement('div');
  const detailId = `coin-detail-${data.id || Math.random().toString(36).slice(2, 8)}`;
  const hasDetail = Boolean(data.detail || data.error_message);
  const isLLMCall = data.activity_type === 'LLM_CALL';
  const outcome = getActivityOutcome(data);
  const meta = [];
  const symbol = data.symbol ? `<span class="text-gray-200 font-medium">${escapeHtml(data.symbol)}</span>` : '';

  if (data.llm_provider) meta.push(escapeHtml(data.llm_provider));
  if (data.llm_tier) meta.push(escapeHtml(ADMIN_AGENT_LABELS[data.llm_tier] || data.llm_tier));
  if (data.execution_time_ms) meta.push(`${(Number(data.execution_time_ms) / 1000).toFixed(1)}초`);
  if (data.confidence != null && !Number.isNaN(Number(data.confidence))) {
    meta.push(`신뢰도 ${Math.round(Number(data.confidence) * 100)}%`);
  }

  card.className = `activity-card coin-card outcome-${outcome}`;
  card.dataset.activityType = data.activity_type || '';
  card.innerHTML = `
    <div class="coin-card-header" ${hasDetail ? `onclick="toggleDetail('${detailId}')"` : ''}>
      <div class="min-w-0 flex-1">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2 text-[11px]">
              <span style="color:${getTypeColor(data.activity_type)}; font-weight:600;">${escapeHtml(data.activity_type || 'EVENT')}</span>
              ${data.phase ? `<span class="text-gray-500">${escapeHtml(formatPhaseLabel(data.phase))}</span>` : ''}
              ${symbol}
            </div>
            <div class="text-sm text-gray-200 whitespace-pre-wrap mt-1 leading-5">${escapeHtml(formatAdminAgentText(data.summary || ''))}</div>
            ${meta.length ? `<div class="text-[11px] text-gray-500 mt-1">${meta.join(' · ')}</div>` : ''}
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <span class="text-[11px] text-gray-500">${escapeHtml(formatTime(data.created_at) || formatTimeAgo(data.created_at))}</span>
            ${hasDetail ? `
              <div
                data-detail-target="${detailId}"
                class="text-[11px] text-gray-500 hover:text-gray-300 flex items-center gap-1 transition-transform duration-200"
              >
                ${isLLMCall ? 'LLM' : '상세'}
                <i data-lucide="${isLLMCall ? 'message-square' : 'chevron-down'}" data-detail-arrow="${detailId}" class="w-3 h-3 transition-transform duration-200"></i>
              </div>
            ` : ''}
          </div>
        </div>
      </div>
    </div>
    ${hasDetail ? `
      <div id="${detailId}" class="coin-card-body">
        <div class="px-3 pb-3">
          <div class="detail-content open">
            ${data.error_message ? `<div class="mb-3 rounded bg-red-950/30 border border-red-900/50 px-3 py-2 text-red-300 whitespace-pre-wrap">${escapeHtml(formatAdminAgentText(data.error_message))}</div>` : ''}
            ${data.detail ? (
              isLLMCall
                ? formatLLMConversation(data.detail)
                : `<div class="max-h-96 overflow-y-auto">${formatDetail(data.detail, data.activity_type)}</div>`
            ) : ''}
          </div>
        </div>
      </div>
    ` : ''}
  `;
  return card;
}

function toggleDetail(id) {
  const body = document.getElementById(id);
  if (!body) return;
  body.classList.toggle('open');
  const btn = document.querySelector(`[data-detail-target="${id}"]`);
  if (btn) {
    const expanded = body.classList.contains('open');
    const icon = btn.querySelector(`[data-detail-arrow="${id}"]`);
    if (icon) {
      if (icon.dataset.lucide === 'chevron-down') {
        icon.style.transform = expanded ? 'rotate(180deg)' : 'rotate(0deg)';
      }
    }
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
    const id = `llm-${Math.random().toString(36).substr(2, 6)}`;
    const iconClass = defaultOpen ? 'lucide-chevron-up' : 'lucide-chevron-down';
    const bodyClass = defaultOpen ? 'llm-body open' : 'llm-body';
    const typeClass = `type-${role.toLowerCase()}`;
    // Using inline onclick for simplicity similar to existing detail toggle
    return `
      <div class="llm-msg ${typeClass}">
        <div class="llm-role" onclick="document.getElementById('${id}').classList.toggle('open'); this.querySelector('i').classList.toggle('rotate-180')">
          <span>${role}</span>
          <i data-lucide="chevron-down" class="w-3.5 h-3.5 transition-transform duration-200 ${defaultOpen ? 'rotate-180' : ''}"></i>
        </div>
        <div id="${id}" class="${bodyClass}">${escapeHtml(formatAdminAgentText(bodyText))}</div>
      </div>
    `;
  };

  if (model) html += `<div class="llm-model-tag">${escapeHtml(String(model))}</div>`;
  if (systemPrompt) {
    html += makeCollapsible('SYSTEM', systemPrompt, false);
  }
  if (prompt) {
    html += makeCollapsible('PROMPT', prompt, false);
  }
  if (response) {
    html += makeCollapsible('RESPONSE', response, true);
  }

  return `<div class="llm-conversation">${html || '<div class="text-gray-500">표시할 LLM 상세가 없습니다.</div>'}</div>`;
}

function setupFeedScroll() {
  const feed = document.getElementById('feed-container');
  if (!feed) return;

  feed.addEventListener('scroll', () => {
    const distFromBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
    if (distFromBottom < 40) {
      autoScroll = true;
      missedCount = 0;
      updateScrollFab();
    } else {
      autoScroll = false;
    }
  });
}

function updateScrollFab() {
  const fab = document.getElementById('scroll-to-bottom');
  if (!fab) return;
  if (missedCount > 0) {
    fab.textContent = `${missedCount}건 새 활동`;
    fab.style.display = 'block';
  } else {
    fab.style.display = 'none';
  }
}

function scrollToBottom() {
  const feed = document.getElementById('feed-container');
  if (!feed) return;
  feed.scrollTop = feed.scrollHeight;
  autoScroll = true;
  missedCount = 0;
  updateScrollFab();
}

async function loadActivityFeed() {
  try {
    const json = await fetchJSON(`${API}/activities/feed?limit=100`);
    const items = (json.data && json.data.items) || json.data || [];
    const feed = document.getElementById('feed-container');
    if (!feed) return;
    feed.innerHTML = '';
    const sorted = Array.isArray(items) ? [...items].reverse() : [];
    sorted.forEach(item => appendActivity(item));
    refreshIcons();
  } catch (err) {
    console.error('[coin] activity feed load error:', err);
  }
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
  const totalPnl = Number(b.total_pnl ?? 0);
  const totalPnlRate = Number(b.total_pnl_rate ?? 0);

  setTextContent('total-asset', formatKRW(totalAsset));
  setTextContent('cash-balance', formatKRW(cash));
  setTextContent('coin-eval', formatKRW(coinValue));

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
    const price = formatPrice(r.price);
    const qty = formatCoinQty(r.quantity);
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
          <span>가격 ${price}</span>
          <span>수량 ${qty}</span>
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
    renderAgentState(lastAgentState);
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

async function loadAgentState() {
  try {
    const json = await fetchJSON(`${API}/agent/state`);
    renderAgentState(json.data || {});
  } catch (err) {
    console.error('[coin] agent state error:', err);
    renderAgentState(null);
    renderTriggerButton();
  }
}

function renderAgentState(state) {
  lastAgentState = state;
  const container = document.getElementById('watchlist-agent-state');
  if (!container) return;
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

  if (!state) {
    container.innerHTML = '<div class="text-gray-500">현재 사이클 상태를 가져오지 못했습니다.</div>';
    return;
  }

  const selected = Array.isArray(state.selected_symbols) ? state.selected_symbols : [];
  const watchlistCount = Number(lastSystemStatus?.watchlist_count ?? 0);
  const lastStatus = lastSystemStatus?.last_cycle_status;
  const lastError = lastSystemStatus?.last_cycle_error;

  if (!cycleActive) {
    container.innerHTML = `
      <div class="flex items-center justify-between gap-2">
        <div>
          <div class="text-[11px] text-gray-500">현재 사이클</div>
          <div class="text-sm text-gray-200 font-medium">${lastStatus ? `대기 · 마지막 ${escapeHtml(lastStatus)}` : '대기 중'}</div>
        </div>
        <span class="agent-state-chip">${watchlistCount}개 감시</span>
      </div>
      ${lastSystemStatus?.last_cycle_time ? `<div class="text-[11px] text-gray-500 mt-2">최근 실행 ${escapeHtml(formatDateTime(lastSystemStatus.last_cycle_time))}</div>` : ''}
      ${lastError ? `<div class="mt-2 text-[11px] text-red-300 whitespace-pre-wrap">${escapeHtml(truncateText(lastError, 120))}</div>` : ''}
    `;
    return;
  }

  const selectedHtml = selected.length
    ? selected.map((item) => {
        const symbol = typeof item === 'string' ? item : (item.symbol || '');
        const name = typeof item === 'object' ? (item.name || item.symbol || '') : item;
        const source = typeof item === 'object' ? formatCoinScanSource(item.scan_source) : '';
        return `<span class="agent-state-chip">${escapeHtml(name)}${symbol && symbol !== name ? ` · ${escapeHtml(symbol)}` : ''}${source ? ` · ${escapeHtml(source)}` : ''}</span>`;
      }).join('')
    : '<span class="text-[11px] text-gray-500">선정 종목 집계 중...</span>';

  container.innerHTML = `
    <div class="flex items-center justify-between gap-2">
      <div>
        <div class="text-[11px] text-coin-dim">현재 사이클</div>
        <div class="text-sm text-white font-medium">진행 중 · ${escapeHtml(AGENT_PHASE_LABELS[state.phase] || state.phase || '대기')}</div>
      </div>
      <span class="agent-state-chip">${selected.length || 0}개 선정</span>
    </div>
    <div class="text-[11px] text-gray-500 mt-2">
      시작 ${escapeHtml(formatDateTime(state.started_at))} · 스캔 ${Number(state.scanned_count || 0)} · 분석 ${Number(state.analyzed_count || 0)}
    </div>
    <div class="mt-2 flex flex-wrap">${selectedHtml}</div>
  `;
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
    renderWatchlist(data.symbols || data);
  } catch (err) {
    console.error('[coin] watchlist load error:', err);
  }
}

function renderWatchlist(symbols) {
  const container = document.getElementById('watchlist-list');
  const dot = document.getElementById('watchlist-stream-dot');
  if (!container) return;

  const items = Array.isArray(symbols) ? symbols : [];
  setTextContent('watchlist-count', `${items.length}종목`);
  if (dot) {
    dot.className = `w-1.5 h-1.5 rounded-full ${items.length ? 'bg-green-400' : 'bg-gray-600'}`;
  }

  if (!items.length) {
    container.innerHTML = '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">감시 코인 없음</div>';
    return;
  }

  container.innerHTML = items.map(s => {
    const sym = typeof s === 'string' ? s : (s.symbol || '');
    const name = typeof s === 'string' ? s : (s.name || s.symbol || '');
    const reason = typeof s === 'object' && s.reason ? s.reason : '';
    const score = typeof s === 'object' && s.score != null ? `${(Number(s.score) * 100).toFixed(0)}점` : '';
    const price = typeof s === 'object' && s.price != null ? formatPrice(s.price) : '';
    const changeRate = typeof s === 'object' && s.change_rate != null ? Number(s.change_rate) : null;
    const changeColor = changeRate == null ? '#6b7280' : (changeRate >= 0 ? '#34d399' : '#f87171');
    const scanSource = typeof s === 'object' ? formatCoinScanSource(s.scan_source) : '';

    return `
      <div style="display:flex; align-items:center; justify-content:space-between; padding:6px 10px; border-radius:6px; background:rgba(30,30,46,0.4); margin-bottom:4px; font-size:12px;">
        <div style="min-width:0;">
          <span style="color:#e2e8f0; font-weight:500;">${escapeHtml(name)}</span>
          ${sym !== name ? `<span style="color:#6b7280; margin-left:4px;">${escapeHtml(sym)}</span>` : ''}
          ${scanSource ? `<span style="color:#a78bfa; margin-left:6px; font-size:11px;">${escapeHtml(scanSource)}</span>` : ''}
          ${reason ? `<div style="color:#6b7280; font-size:11px; margin-top:1px;">${escapeHtml(reason)}</div>` : ''}
          ${price ? `<div style="color:#6b7280; font-size:11px; margin-top:1px;">현재가 ${escapeHtml(price)}</div>` : ''}
        </div>
        <div style="text-align:right;">
          ${score ? `<div style="color:#a78bfa; font-size:11px;">${score}</div>` : ''}
          ${changeRate != null ? `<div style="color:${changeColor}; font-size:11px; margin-top:2px;">${changeRate >= 0 ? '+' : ''}${changeRate.toFixed(2)}%</div>` : ''}
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

let currentFilter = 'ALL';

function setLogFilter(filter) {
  currentFilter = filter;
  document.querySelectorAll('.log-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });
  const feed = document.getElementById('feed-container');
  if (!feed) return;
  feed.querySelectorAll('.activity-card').forEach(card => {
    if (filter === 'ALL') { card.style.display = ''; return; }
    const type = card.dataset.activityType || '';
    if (filter === 'SIGNAL') {
      card.style.display = ['SCAN', 'DECISION', 'TIER1_ANALYSIS', 'TIER2_REVIEW', 'RISK_GATE'].includes(type) ? '' : 'none';
    } else if (filter === 'TRADE') {
      card.style.display = ['ORDER', 'TRADE_RESULT'].includes(type) ? '' : 'none';
    }
  });
}

function clearFeed() {
  const feed = document.getElementById('feed-container');
  if (feed) feed.innerHTML = '';
  setTextContent('activity-count', '0건');
}

function refreshFeed() {
  loadActivityFeed();
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
  loadAgentState();
  loadBalance();
  loadRecommendations();
  loadWatchlist();
  loadSettings();
  loadActivityFeed();
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
