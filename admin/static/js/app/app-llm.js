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
