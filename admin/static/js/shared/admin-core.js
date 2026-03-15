/**
 * admin-core.js — Shared namespace, constants, and utility functions
 *
 * Loaded first by both stock (admin) and coin (admin-coin) dashboards.
 * Creates the `window.Admin` namespace that all other shared modules attach to.
 *
 * Each app sets:
 *   Admin.API            — base API path
 *   Admin.notificationTagPrefix — 'momo' | 'momo-coin'
 *   Admin.CardTheme      — card color palette overrides
 */
(function () {
  'use strict';

  // ── Namespace ──
  var Admin = {};

  // ── Config (set by each app) ──
  Admin.API = '';
  Admin.notificationTagPrefix = 'momo';
  Admin.CardTheme = {};

  // ── Constants ──
  Admin.PIPELINE_STEPS = ['data', 'tier1', 'tier2', 'strategy', 'decision'];
  Admin.PIPELINE_LABELS = { data: '\uC870\uD68C', tier1: 'Tier1', tier2: 'Tier2', strategy: '\uC804\uB7B5', decision: '\uACB0\uC815' };

  Admin.LLM_AGENT_DEFAULTS = {
    tier1: {
      display_name: '\uD6C4\uBCF4 \uBD84\uC11D \uC5D0\uC774\uC804\uD2B8',
      short_label: '\uD6C4\uBCF4 \uBD84\uC11D',
      description: '\uCC28\uD2B8\u00B7\uC2DC\uC7A5 \uCEE8\uD14D\uC2A4\uD2B8\uB97C \uBC14\uD0D5\uC73C\uB85C \uB9E4\uC218 \uD6C4\uBCF4\uC640 \uBAA9\uD45C/\uC190\uC808\uC744 1\uCC28 \uD310\uB2E8',
      icon: 'search',
    },
    tier2: {
      display_name: '\uCD5C\uC885 \uAC80\uD1A0 \uC5D0\uC774\uC804\uD2B8',
      short_label: '\uCD5C\uC885 \uAC80\uD1A0',
      description: '1\uCC28 \uBD84\uC11D \uACB0\uACFC\uB97C \uB9AC\uC2A4\uD06C\u00B7\uD3EC\uD2B8\uD3F4\uB9AC\uC624 \uAD00\uC810\uC5D0\uC11C \uC7AC\uAC80\uC99D\uD574 \uC8FC\uBB38 \uC2B9\uC778 \uC5EC\uBD80\uB97C \uACB0\uC815',
      icon: 'shield-check',
    },
  };

  Admin.ADMIN_AGENT_LABELS = {
    TIER1: Admin.LLM_AGENT_DEFAULTS.tier1.display_name,
    TIER2: Admin.LLM_AGENT_DEFAULTS.tier2.display_name,
  };

  Admin.ORIGINAL_TITLE = document.title || 'MOMO Trading Admin';

  // ── Pure Utility Functions ──

  Admin.escapeHtml = function (str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  };

  Admin.formatKRW = function (n) {
    if (n == null || Number.isNaN(Number(n))) return '-';
    var value = Number(n);
    if (Math.abs(value) >= 100000000) return (Math.floor(value / 100000000 * 10) / 10).toFixed(1) + '\uC5B5';
    if (Math.abs(value) >= 10000) return Math.floor(value / 10000).toLocaleString() + '\uB9CC';
    return Math.trunc(value).toLocaleString() + '\uC6D0';
  };

  Admin.formatTime = function (ts) {
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
  };

  Admin.formatAdminAgentText = function (text) {
    if (text == null) return '';
    return String(text)
      .replace(/\[TIER1\]/g, '[' + Admin.ADMIN_AGENT_LABELS.TIER1 + ']')
      .replace(/\[TIER2\]/g, '[' + Admin.ADMIN_AGENT_LABELS.TIER2 + ']')
      .replace(/\bTIER1\b/g, Admin.ADMIN_AGENT_LABELS.TIER1)
      .replace(/\bTIER2\b/g, Admin.ADMIN_AGENT_LABELS.TIER2)
      .replace(/\bTier 1\b/g, Admin.ADMIN_AGENT_LABELS.TIER1)
      .replace(/\bTier1\b/g, Admin.ADMIN_AGENT_LABELS.TIER1)
      .replace(/\bTier 2\b/g, Admin.ADMIN_AGENT_LABELS.TIER2)
      .replace(/\bTier2\b/g, Admin.ADMIN_AGENT_LABELS.TIER2);
  };

  Admin.transformAdminAgentValue = function (value) {
    if (typeof value === 'string') return Admin.formatAdminAgentText(value);
    if (Array.isArray(value)) return value.map(Admin.transformAdminAgentValue);
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value).map(function (entry) {
          return [entry[0], Admin.transformAdminAgentValue(entry[1])];
        })
      );
    }
    return value;
  };

  Admin.refreshIcons = function () {
    if (typeof lucide !== 'undefined') {
      requestAnimationFrame(function () { lucide.createIcons(); });
    }
  };

  Admin.getTypeColor = function (type) {
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
  };

  Admin.fetchJSON = async function (url, options) {
    var resp = await fetch(url, options || {});
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.json();
  };

  Admin.updateBadge = function (id, text, color) {
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
  };

  Admin.getProgressKey = function (data) {
    var match = (data.summary || '').match(/\[([^\]]+)\]/);
    var symbol = match ? match[1] : '';
    return data.activity_type + ':' + symbol;
  };

  Admin.filterResolvedStarts = function (activities) {
    var resolved = new Set();
    activities.forEach(function (a) {
      if (a.phase === 'COMPLETE' || a.phase === 'ERROR') {
        resolved.add(Admin.getProgressKey(a));
      }
    });
    return activities.filter(function (a) {
      return !(a.phase === 'START' && resolved.has(Admin.getProgressKey(a)));
    });
  };

  /**
   * Render a loading / error / empty placeholder into a container element.
   * NOTE: This sets container.innerHTML with pre-escaped content only.
   * All dynamic text is passed through Admin.escapeHtml before insertion.
   */
  Admin.renderPlaceholder = function (container, type, message) {
    if (!container) return;
    if (type === 'loading') {
      container.textContent = '';
      var wrapper = document.createElement('div');
      wrapper.className = 'text-center text-gray-500 text-sm py-4 flex items-center justify-center gap-2';
      var spinner = document.createElement('span');
      spinner.className = 'progress-spinner';
      wrapper.appendChild(spinner);
      wrapper.appendChild(document.createTextNode(' ' + (message || '\uBD88\uB7EC\uC624\uB294 \uC911...')));
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
      msgDiv.textContent = message || '\uC624\uB958\uAC00 \uBC1C\uC0DD\uD588\uC2B5\uB2C8\uB2E4';
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
      msgDiv2.textContent = message || '\uB370\uC774\uD130\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4';
      emptyDiv.appendChild(msgDiv2);
      container.appendChild(emptyDiv);
    }
    Admin.refreshIcons();
  };

  Admin.getPhaseIcon = function (phase) {
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
  };

  Admin.formatPhaseLabel = function (phase) {
    var map = {
      START: '\uC2DC\uC791',
      PROGRESS: '\uC9C4\uD589',
      COMPLETE: '\uC644\uB8CC',
      ERROR: '\uC624\uB958',
      SKIP: '\uC2A4\uD0B5',
    };
    return map[phase] || phase || '';
  };

  Admin.getActivityIdentity = function (data) {
    return data.id || (data.created_at || '') + '|' + (data.activity_type || '') + '|' + (data.phase || '') + '|' + (data.symbol || '') + '|' + (data.summary || '');
  };

  // ── Expose ──
  window.Admin = Admin;
})();
