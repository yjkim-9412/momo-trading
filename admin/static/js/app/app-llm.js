// ── app-llm.js — LLM status and usage display (ES module) ──

import { API } from './app-state.js';
import { fetchJSON, escapeHtml, refreshIcons, LLM_AGENT_DEFAULTS } from '../shared/admin-core.js';

// ── LLM Status ──
export async function loadLLMStatus() {
  try {
    var json = await fetchJSON(API + '/llm/status');
    var s = json.data;
    if (!s) return;
    var providerId = s.selected_provider || (s.tier1 && s.tier1.provider);
    var providerLabel = s.provider_name || formatProviderLabel(providerId);
    renderLLMAgentSummary(s, providerId, providerLabel);
    renderLLMAgentGuide(s, providerId, providerLabel);
    refreshIcons();
  } catch (err) {
    console.error('LLM status error:', err);
  }
}

// ── LLM Usage ──
export async function loadLLMUsage() {
  try {
    var json = await fetchJSON(API + '/llm/usage');
    var d = json.data;
    if (!d) {
      document.getElementById('usage-summary').textContent = '';
      var noData = document.createElement('div');
      noData.className = 'text-gray-500 text-xs';
      noData.textContent = '\uB370\uC774\uD130 \uC5C6\uC74C';
      document.getElementById('usage-summary').appendChild(noData);
      return;
    }
    var providerLabel = d.provider_name || formatProviderLabel(d.provider || (d.app_usage && d.app_usage.provider));
    var summary = d.summary || {};
    var totalSessions = summary.total_sessions == null ? '-' : summary.total_sessions.toLocaleString();
    var totalMessages = summary.total_messages == null ? '-' : summary.total_messages.toLocaleString();

    var summaryEl = document.getElementById('usage-summary');
    summaryEl.replaceChildren();
    _appendFlexRow(summaryEl, 'Provider', providerLabel, 'text-gray-400', 'text-white');
    _appendFlexRow(summaryEl, '\uCD1D \uC138\uC158', totalSessions, 'text-gray-400', 'text-white');
    _appendFlexRow(summaryEl, '\uCD1D \uBA54\uC2DC\uC9C0', totalMessages, 'text-gray-400', 'text-white');

    // App usage
    var appEl = document.getElementById('usage-app');
    var app = d.app_usage || {};
    if (app && app.total_calls > 0) {
      var totalInputTokens = app.total_input_tokens != null ? app.total_input_tokens : (app.input_tokens || 0);
      var totalOutputTokens = app.total_output_tokens != null ? app.total_output_tokens : (app.output_tokens || 0);
      appEl.replaceChildren();
      _appendFlexRow(appEl, '\uD638\uCD9C \uC218', String(app.total_calls), 'text-gray-400', 'text-cyan-400');
      _appendFlexRow(appEl, '\uC785\uB825 \uD1A0\uD070', formatTokens(totalInputTokens), 'text-gray-400', 'text-blue-400');
      _appendFlexRow(appEl, '\uCD9C\uB825 \uD1A0\uD070', formatTokens(totalOutputTokens), 'text-gray-400', 'text-green-400');

      if (app.session_id) {
        _appendFlexRow(appEl, '\uC138\uC158', escapeHtml(String(app.session_id).slice(0, 8)), 'text-gray-400', 'text-gray-300');
      }

      if (app.by_model && Object.keys(app.by_model).length) {
        for (var model in app.by_model) {
          var mu = app.by_model[model];
          var shortModel = model
            .replace('claude-', '')
            .replace('codex:', '')
            .replace(/-\d{8,}$/, '');
          var cachedTokens = mu.cached_input_tokens != null ? mu.cached_input_tokens : (mu.cache_read || 0);
          var modelCard = document.createElement('div');
          modelCard.className = 'bg-dark-900 rounded p-1.5 mt-1';
          var modelTitle = document.createElement('div');
          modelTitle.className = 'text-gray-300 text-xs';
          modelTitle.textContent = shortModel + ' (' + mu.calls + '\uD68C)';
          var modelDetail = document.createElement('div');
          modelDetail.className = 'text-gray-500';
          modelDetail.textContent = formatTokens(mu.input_tokens) + ' in / ' + formatTokens(mu.output_tokens) + ' out';
          modelCard.appendChild(modelTitle);
          modelCard.appendChild(modelDetail);
          if (cachedTokens) {
            var cachedDiv = document.createElement('div');
            cachedDiv.className = 'text-gray-500';
            cachedDiv.textContent = formatTokens(cachedTokens) + ' cached';
            modelCard.appendChild(cachedDiv);
          }
          appEl.appendChild(modelCard);
        }
      }
    } else {
      appEl.replaceChildren();
      var noApp = document.createElement('div');
      noApp.className = 'text-gray-500';
      noApp.textContent = '\uC544\uC9C1 \uD638\uCD9C \uC5C6\uC74C';
      appEl.appendChild(noApp);
    }

    // Model usage cards
    var modelsEl = document.getElementById('usage-models');
    var modelData = d.model_usage && Object.keys(d.model_usage).length
      ? d.model_usage
      : (app.by_model && Object.keys(app.by_model).length ? app.by_model : null);
    renderModelCards(modelData, modelsEl);

    // Daily chart (last 7 days)
    var chartEl = document.getElementById('usage-chart');
    var dailyTokens = (d.daily_model_tokens || []).slice(-7);
    if (dailyTokens.length) {
      var totals = dailyTokens.map(function (day) {
        var sum = 0;
        for (var k in (day.tokensByModel || {})) sum += day.tokensByModel[k];
        return { date: day.date, tokens: sum };
      });
      var maxTokens = Math.max.apply(null, totals.map(function (t) { return t.tokens; }).concat([1]));
      chartEl.replaceChildren();
      totals.forEach(function (t) {
        var pctVal = Math.max((t.tokens / maxTokens) * 100, 2);
        var dateLabel = t.date.slice(5);
        var row = document.createElement('div');
        row.className = 'flex items-center gap-2';
        var dateSpan = document.createElement('span');
        dateSpan.className = 'text-gray-500 w-12 shrink-0';
        dateSpan.textContent = dateLabel;
        var barWrap = document.createElement('div');
        barWrap.className = 'flex-1 bg-dark-900 rounded-full h-3 overflow-hidden';
        var barFill = document.createElement('div');
        barFill.className = 'h-full bg-cyan-500/60 rounded-full';
        barFill.style.width = pctVal + '%';
        barWrap.appendChild(barFill);
        var tokSpan = document.createElement('span');
        tokSpan.className = 'text-gray-400 w-14 text-right shrink-0';
        tokSpan.textContent = formatTokens(t.tokens);
        row.appendChild(dateSpan);
        row.appendChild(barWrap);
        row.appendChild(tokSpan);
        chartEl.appendChild(row);
      });
    } else {
      chartEl.replaceChildren();
      var noChart = document.createElement('div');
      noChart.className = 'text-gray-500';
      noChart.textContent = '\uB370\uC774\uD130 \uC5C6\uC74C';
      chartEl.appendChild(noChart);
    }
  } catch (err) {
    console.error('LLM usage error:', err);
    var failEl = document.getElementById('usage-summary');
    if (failEl) {
      failEl.replaceChildren();
      var failDiv = document.createElement('div');
      failDiv.className = 'text-gray-500 text-xs';
      failDiv.textContent = '\uC870\uD68C \uC2E4\uD328';
      failEl.appendChild(failDiv);
    }
  }
}

// ── Helper: append a flex row ──
function _appendFlexRow(parent, label, value, labelCls, valueCls) {
  var row = document.createElement('div');
  row.className = 'flex justify-between';
  var l = document.createElement('span');
  l.className = labelCls || 'text-gray-400';
  l.textContent = label;
  var v = document.createElement('span');
  v.className = valueCls || 'text-white';
  v.textContent = value;
  row.appendChild(l);
  row.appendChild(v);
  parent.appendChild(row);
}

// ── Token formatting ──
export function formatTokens(n) {
  if (n == null || n === 0) return '0';
  if (n >= 1000000000) return (n / 1000000000).toFixed(1) + 'B';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toLocaleString();
}

// ── Model cards renderer ──
export function renderModelCards(models, targetEl) {
  if (!targetEl) return;
  if (!models || !Object.keys(models).length) {
    targetEl.replaceChildren();
    var noData = document.createElement('div');
    noData.className = 'text-gray-500';
    noData.textContent = '\uB370\uC774\uD130 \uC5C6\uC74C';
    targetEl.appendChild(noData);
    return;
  }
  targetEl.replaceChildren();
  for (var model in models) {
    var u = models[model];
    var short = model.replace('claude-', '').replace('codex:', '').replace(/-\d{8,}$/, '');
    var input = u.inputTokens != null ? u.inputTokens : (u.input_tokens || 0);
    var output = u.outputTokens != null ? u.outputTokens : (u.output_tokens || 0);
    var cached = u.cacheReadInputTokens != null ? u.cacheReadInputTokens : (u.cached_input_tokens != null ? u.cached_input_tokens : (u.cache_read || 0));
    var creation = u.cacheCreationInputTokens || 0;
    var card = document.createElement('div');
    card.className = 'bg-dark-900 rounded p-2 mb-1';
    var title = document.createElement('div');
    title.className = 'text-gray-300 font-medium mb-1';
    title.title = model;
    title.textContent = short;
    card.appendChild(title);
    var grid = document.createElement('div');
    grid.className = 'grid grid-cols-2 gap-x-2 gap-y-1 text-gray-500';
    _addGridPair(grid, '\uC785\uB825', formatTokens(input), 'text-gray-400');
    _addGridPair(grid, '\uCD9C\uB825', formatTokens(output), 'text-green-400');
    _addGridPair(grid, '\uCE90\uC2DC\uC77D\uAE30', formatTokens(cached), 'text-blue-400');
    if (creation) _addGridPair(grid, '\uCE90\uC2DC\uC0DD\uC131', formatTokens(creation), 'text-purple-400');
    card.appendChild(grid);
    targetEl.appendChild(card);
  }
}

function _addGridPair(grid, label, value, valueClass) {
  var l = document.createElement('span');
  l.textContent = label;
  var v = document.createElement('span');
  v.className = 'text-right ' + (valueClass || '');
  v.textContent = value;
  grid.appendChild(l);
  grid.appendChild(v);
}

// ── Provider label formatting ──
export function formatProviderLabel(provider) {
  if (!provider) return 'LLM';
  if (provider === 'CLAUDE_CODE') return 'Claude Code';
  if (provider === 'CODEX_CLI') return 'Codex CLI';
  return provider.replaceAll('_', ' ');
}

// ── Agent config helper ──
export function getLLMAgentConfig(status, tierKey) {
  return Object.assign({}, LLM_AGENT_DEFAULTS[tierKey], (status && status[tierKey]) || {});
}

export function formatReasoningEffortText(effort, provider) {
  if (effort) return '\uCD94\uB860 ' + effort;
  if (provider === 'CODEX_CLI') return '\uCD94\uB860 global';
  return '';
}

function formatTier1ProfileMeta(status) {
  var profiles = (status && status.tier1_profiles) || {};
  var orderedKeys = ['scan', 'analysis'].filter(function (key) { return profiles[key]; });
  if (!orderedKeys.length) return '';
  return orderedKeys.map(function (key) {
    var profile = profiles[key];
    var label = profile.short_label || profile.display_name || key;
    var effort = profile.reasoning_effort || '-';
    return escapeHtml(label + ' ' + effort);
  }).join(' \u00b7 ');
}

// ── LLM Agent Summary (left sidebar settings) ──
export function renderLLMAgentSummary(status, providerId, providerLabel) {
  var summaryEl = document.getElementById('llm-agent-summary');
  if (!summaryEl) return;

  summaryEl.replaceChildren();
  ['tier1', 'tier2'].forEach(function (tierKey) {
    var tierConfig = getLLMAgentConfig(status, tierKey);
    var tier1ProfileMeta = tierKey === 'tier1' ? formatTier1ProfileMeta(status) : '';
    var row = document.createElement('div');
    row.className = 'agent-summary-row';
    var nameDiv = document.createElement('div');
    nameDiv.className = 'text-xs text-gray-200';
    nameDiv.textContent = tierConfig.display_name;
    var descDiv = document.createElement('div');
    descDiv.className = 'text-[11px] text-gray-500 leading-4 mt-1';
    descDiv.textContent = tierConfig.description;
    var metaDiv = document.createElement('div');
    metaDiv.className = 'text-[11px] text-gray-500 mt-1 truncate';
    metaDiv.textContent = [providerLabel || formatProviderLabel(providerId), tierConfig.model || '-',
      formatReasoningEffortText(tierConfig.reasoning_effort, providerId)].filter(Boolean).join(' \u00b7 ');
    row.appendChild(nameDiv);
    row.appendChild(descDiv);
    row.appendChild(metaDiv);
    if (tier1ProfileMeta) {
      var profileDiv = document.createElement('div');
      profileDiv.className = 'text-[11px] text-gray-500 mt-1';
      profileDiv.textContent = '\uD504\uB85C\uD544 \u00b7 ' + tier1ProfileMeta.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
      row.appendChild(profileDiv);
    }
    summaryEl.appendChild(row);
  });
}

// ── LLM Agent Guide (right panel) ──
export function renderLLMAgentGuide(status, providerId, providerLabel) {
  var guideEl = document.getElementById('llm-agent-guide');
  if (!guideEl) return;

  guideEl.replaceChildren();
  ['tier1', 'tier2'].forEach(function (tierKey) {
    var tierConfig = getLLMAgentConfig(status, tierKey);
    var toneClass = tierKey === 'tier1' ? 'agent-guide-tier1' : 'agent-guide-tier2';
    var tier1ProfileMeta = tierKey === 'tier1' ? formatTier1ProfileMeta(status) : '';

    var card = document.createElement('div');
    card.className = 'agent-guide-card ' + toneClass;

    // Head
    var head = document.createElement('div');
    head.className = 'agent-guide-head';
    var headLeft = document.createElement('div');
    headLeft.className = 'min-w-0';
    var kicker = document.createElement('div');
    kicker.className = 'agent-guide-kicker';
    kicker.textContent = tierConfig.short_label;
    var title = document.createElement('div');
    title.className = 'agent-guide-title';
    title.textContent = tierConfig.display_name;
    headLeft.appendChild(kicker);
    headLeft.appendChild(title);
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', tierConfig.icon);
    icon.className = 'w-4 h-4 text-gray-500 shrink-0';
    head.appendChild(headLeft);
    head.appendChild(icon);
    card.appendChild(head);

    // Description
    var desc = document.createElement('div');
    desc.className = 'agent-guide-desc';
    desc.textContent = tierConfig.description;
    card.appendChild(desc);

    // Meta
    var meta = document.createElement('div');
    meta.className = 'agent-guide-meta';
    meta.textContent = [providerLabel || formatProviderLabel(providerId), tierConfig.model || '-',
      formatReasoningEffortText(tierConfig.reasoning_effort, providerId)].filter(Boolean).join(' \u00b7 ');
    card.appendChild(meta);

    if (tier1ProfileMeta) {
      var profileMeta = document.createElement('div');
      profileMeta.className = 'agent-guide-meta';
      profileMeta.textContent = '\uD504\uB85C\uD544 \u00b7 ' + tier1ProfileMeta.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
      card.appendChild(profileMeta);
    }

    guideEl.appendChild(card);
  });
}
