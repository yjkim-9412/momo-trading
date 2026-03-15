// ── admin-core.js — Shared constants & pure utilities ──

export const ORIGINAL_TITLE = document.title || 'MOMO Trading Admin';

export const PIPELINE_STEPS = ['data', 'tier1', 'tier2', 'strategy', 'decision'];
export const PIPELINE_LABELS = { data: '조회', tier1: 'Tier1', tier2: 'Tier2', strategy: '전략', decision: '결정' };

export const LLM_AGENT_DEFAULTS = {
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

export const ADMIN_AGENT_LABELS = {
  TIER1: LLM_AGENT_DEFAULTS.tier1.display_name,
  TIER2: LLM_AGENT_DEFAULTS.tier2.display_name,
};

// ── Pure Utility Functions ──

export function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function formatKRW(n) {
  if (n == null || Number.isNaN(Number(n))) return '-';
  var value = Number(n);
  if (Math.abs(value) >= 100000000) return (Math.floor(value / 100000000 * 10) / 10).toFixed(1) + '억';
  if (Math.abs(value) >= 10000) return Math.floor(value / 10000).toLocaleString() + '만';
  return Math.trunc(value).toLocaleString() + '원';
}

export function formatTime(ts) {
  if (!ts) return '';
  try {
    var d = new Date(ts);
    return d.toLocaleTimeString('ko-KR', {
      timeZone: 'Asia/Seoul',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch (e) { return String(ts); }
}

export function formatAdminAgentText(text) {
  if (text == null) return '';
  return String(text)
    .replace(/\[TIER1\]/g, '[' + ADMIN_AGENT_LABELS.TIER1 + ']')
    .replace(/\[TIER2\]/g, '[' + ADMIN_AGENT_LABELS.TIER2 + ']')
    .replace(/\bTIER1\b/g, ADMIN_AGENT_LABELS.TIER1)
    .replace(/\bTIER2\b/g, ADMIN_AGENT_LABELS.TIER2)
    .replace(/\bTier 1\b/g, ADMIN_AGENT_LABELS.TIER1)
    .replace(/\bTier1\b/g, ADMIN_AGENT_LABELS.TIER1)
    .replace(/\bTier 2\b/g, ADMIN_AGENT_LABELS.TIER2)
    .replace(/\bTier2\b/g, ADMIN_AGENT_LABELS.TIER2);
}

export function transformAdminAgentValue(value) {
  if (typeof value === 'string') return formatAdminAgentText(value);
  if (Array.isArray(value)) return value.map(transformAdminAgentValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(function (entry) {
        return [entry[0], transformAdminAgentValue(entry[1])];
      })
    );
  }
  return value;
}

export function refreshIcons() {
  if (typeof lucide !== 'undefined') {
    requestAnimationFrame(function () { lucide.createIcons(); });
  }
}

export function getTypeColor(type) {
  var map = {
    CYCLE: 'blue',
    SCAN: 'cyan',
    SCREENING: 'purple',
    TIER1_ANALYSIS: 'yellow',
    TIER2_REVIEW: 'green',
    STRATEGY_EVAL: 'blue',
    RISK_CHECK: 'yellow',
    RISK_TUNING: 'purple',
    DECISION: 'green',
    ORDER: 'red',
    TRADE_RESULT: 'green',
    RISK_GATE: 'red',
    EVENT: 'gray',
    REPORT: 'purple',
    LLM_CALL: 'cyan',
    DAILY_PLAN: 'purple',
  };
  return map[type] || 'gray';
}

export async function fetchJSON(url, options) {
  var resp = await fetch(url, options || {});
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp.json();
}

export function updateBadge(id, text, color) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  var colors = {
    green: 'bg-green-900/50 text-green-300',
    red: 'bg-red-900/50 text-red-300',
    yellow: 'bg-yellow-900/50 text-yellow-300',
    gray: 'bg-gray-800 text-gray-400',
    blue: 'bg-blue-900/50 text-blue-300',
    purple: 'bg-purple-900/50 text-purple-300',
  };
  el.className = 'px-2 py-0.5 rounded text-xs font-medium ' + (colors[color] || colors.blue);
}

export function getProgressKey(data) {
  var match = (data.summary || '').match(/\[([^\]]+)\]/);
  var symbol = match ? match[1] : '';
  return data.activity_type + ':' + symbol;
}

export function filterResolvedStarts(activities) {
  var resolved = new Set();
  activities.forEach(function (a) {
    if (a.phase === 'COMPLETE' || a.phase === 'ERROR') {
      resolved.add(getProgressKey(a));
    }
  });
  return activities.filter(function (a) {
    return !(a.phase === 'START' && resolved.has(getProgressKey(a)));
  });
}

/**
 * Render a loading / error / empty placeholder into a container element.
 * All dynamic text is passed through escapeHtml before insertion.
 */
export function renderPlaceholder(container, type, message) {
  if (!container) return;
  if (type === 'loading') {
    container.textContent = '';
    var wrapper = document.createElement('div');
    wrapper.className = 'text-center text-gray-500 text-sm py-4 flex items-center justify-center gap-2';
    var spinner = document.createElement('span');
    spinner.className = 'progress-spinner';
    wrapper.appendChild(spinner);
    wrapper.appendChild(document.createTextNode(' ' + (message || '불러오는 중...')));
    container.appendChild(wrapper);
  } else if (type === 'error') {
    container.textContent = '';
    var errDiv = document.createElement('div');
    errDiv.className = 'text-center text-red-400 text-sm py-8';
    var iconHolder = document.createElement('div');
    iconHolder.setAttribute('data-lucide', 'alert-triangle');
    iconHolder.className = 'w-6 h-6 mx-auto mb-2 opacity-60';
    errDiv.appendChild(iconHolder);
    var msgDiv = document.createElement('div');
    msgDiv.textContent = message || '오류가 발생했습니다';
    errDiv.appendChild(msgDiv);
    container.appendChild(errDiv);
  } else {
    container.textContent = '';
    var emptyDiv = document.createElement('div');
    emptyDiv.className = 'text-center text-gray-500 text-sm py-8';
    var iconHolder2 = document.createElement('div');
    iconHolder2.setAttribute('data-lucide', 'inbox');
    iconHolder2.className = 'w-6 h-6 mx-auto mb-2 opacity-50';
    emptyDiv.appendChild(iconHolder2);
    var msgDiv2 = document.createElement('div');
    msgDiv2.textContent = message || '데이터가 없습니다';
    emptyDiv.appendChild(msgDiv2);
    container.appendChild(emptyDiv);
  }
  refreshIcons();
}

export function getPhaseIcon(phase) {
  var icons = {
    START: 'play',
    PROGRESS: 'loader',
    COMPLETE: 'check',
    ERROR: 'x',
  };
  var name = icons[phase] || 'circle';
  var i = document.createElement('i');
  i.setAttribute('data-lucide', name);
  i.className = 'w-3 h-3 inline-block';
  return i.outerHTML;
}

export function formatPhaseLabel(phase) {
  var map = {
    START: '시작',
    PROGRESS: '진행',
    COMPLETE: '완료',
    ERROR: '오류',
    SKIP: '스킵',
  };
  return map[phase] || phase || '';
}

export function getActivityIdentity(data) {
  return data.id || (data.created_at || '') + '|' + (data.activity_type || '') + '|' + (data.phase || '') + '|' + (data.symbol || '') + '|' + (data.summary || '');
}
