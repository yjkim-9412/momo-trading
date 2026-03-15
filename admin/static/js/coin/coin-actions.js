// ── coin-actions.js — Manual triggers and action buttons ──
//
// Security note: all dynamic content is escaped via escapeHtml()
// before DOM insertion. This mirrors the original coin-app.js exactly.

import { fetchJSON } from '../shared/admin-core.js';
import * as Toast from '../shared/admin-toast.js';
import { state, API, registerSyncAgentSnapshot } from './coin-state.js';
import { setStatusText, truncateText } from './coin-utils.js';

// Late-bound references
let _loadAgentState = null;
let _loadSystemStatus = null;
let _updateScanTimeline = null;
let _loadLatestReport = null;
let _loadReportList = null;
let _loadCurrentReportView = null;

export function registerActionDeps(deps) {
  _loadAgentState = deps.loadAgentState;
  _loadSystemStatus = deps.loadSystemStatus;
  _updateScanTimeline = deps.updateScanTimeline;
  _loadLatestReport = deps.loadLatestReport;
  _loadReportList = deps.loadReportList;
  _loadCurrentReportView = deps.loadCurrentReportView;
}

// -- Trigger manual scan --

export async function triggerScan() {
  var btn = document.getElementById('btn-trigger-scan');
  if (!btn) return;
  if (state.manualScanRequestPending || (state.lastAgentState && state.lastAgentState.cycle_active) || (state.manualScanQueuedAt && (Date.now() - state.manualScanQueuedAt) < 10000)) {
    return;
  }

  state.manualScanRequestPending = true;
  clearManualScanNotice();
  renderActionButtons();

  try {
    var json = await fetchJSON(API + '/agent/trigger', { method: 'POST' });
    var data = json.data;
    var msg = json.message || (data && data.message) || '스캔 트리거 완료';

    if (data && data.skipped) {
      var reason = data.reason || 'skipped';
      state.manualScanQueuedAt = null;
      state.manualScanSessionObserved = false;
      setManualScanNotice('스캔 스킵 · ' + reason, 'pause-circle', 'bg-yellow-900/20 text-yellow-300 border-yellow-400/20', 4500);
      Toast.show(msg, 'info');
    } else {
      state.manualScanQueuedAt = Date.now();
      state.manualScanSessionObserved = true;
      state.lastCycleTime = Date.now();
      if (_updateScanTimeline) _updateScanTimeline();
      setStatusText('수동 스캔 요청 접수됨', '#fbbf24');
      Toast.show(msg, 'success');
    }

    if (_loadAgentState) setTimeout(_loadAgentState, 300);
    if (_loadSystemStatus) setTimeout(_loadSystemStatus, 600);
  } catch (err) {
    console.error('[coin] scan trigger error:', err);
    state.manualScanQueuedAt = null;
    state.manualScanSessionObserved = false;
    setManualScanNotice('요청 실패', 'x', 'bg-red-900/20 text-red-300 border-red-400/20', 5000);
    setStatusText('수동 스캔 요청 실패 · ' + truncateText(err.message, 80), '#f87171');
    Toast.show('수동 스캔 실패: ' + err.message, 'error');
  } finally {
    state.manualScanRequestPending = false;
    renderActionButtons();
  }
}

// -- Generate manual report --

export async function generateManualReport() {
  var btn = document.getElementById('btn-generate-report');
  if (!btn) return;
  if (state.manualReportRequestPending || state.manualScanRequestPending || (state.lastAgentState && state.lastAgentState.cycle_active) || (state.manualScanQueuedAt && (Date.now() - state.manualScanQueuedAt) < 10000)) {
    return;
  }

  state.manualReportRequestPending = true;
  renderActionButtons();

  try {
    var json = await fetchJSON(API + '/reports/generate', { method: 'POST' });
    var msg = json.message || '리포트 생성 완료';
    setStatusText('수동 리포트 생성 완료', '#93c5fd');
    Toast.show(msg, 'success');
    if (_loadLatestReport) _loadLatestReport();
    if (_loadReportList) _loadReportList();
    if (state.currentView === 'report' && _loadCurrentReportView) {
      _loadCurrentReportView({ forceLatest: state.currentReportSelection.mode === 'latest' });
    }
    if (_loadSystemStatus) setTimeout(_loadSystemStatus, 400);
  } catch (err) {
    console.error('[coin] report generate error:', err);
    setStatusText('수동 리포트 생성 실패 · ' + truncateText(err.message, 80), '#f87171');
    Toast.show('수동 리포트 생성 실패: ' + err.message, 'error');
  } finally {
    state.manualReportRequestPending = false;
    renderActionButtons();
  }
}

// -- Render action buttons area --

export function renderActionButtons() {
  renderTriggerButton();
  renderReportButton();
}

// -- Dynamic trigger button --

export function renderTriggerButton() {
  var btn = document.getElementById('btn-trigger-scan');
  if (!btn) return;

  if (state.manualScanNotice && state.manualScanNotice.expiresAt <= Date.now()) {
    state.manualScanNotice = null;
  }

  var cycleActive = !!(state.lastAgentState && state.lastAgentState.cycle_active);
  var cryptoEnabled = !(state.lastSystemStatus && state.lastSystemStatus.crypto_enabled === false);
  var phase = (state.AGENT_PHASE_LABELS[state.lastAgentState && state.lastAgentState.phase] || (state.lastAgentState && state.lastAgentState.phase) || '스캔');
  var analyzedCount = Number((state.lastAgentState && state.lastAgentState.analyzed_count) || 0);
  var scannedCount = Number((state.lastAgentState && state.lastAgentState.scanned_count) || 0);

  var label = '수동 스캔 실행';
  var icon = 'scan';
  var disabled = false;
  var baseClasses = 'w-full bg-coin-purple/20 hover:bg-coin-purple/30 text-coin-purple border border-coin-purple/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2';
  var title = '코인 수동 스캔 실행';

  if (!cryptoEnabled) {
    label = '코인 비활성';
    icon = 'ban';
    disabled = true;
    title = 'CRYPTO_ENABLED=false 상태입니다';
    baseClasses = 'w-full bg-gray-800 text-gray-500 border border-gray-700 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-not-allowed';
  } else if (state.manualScanRequestPending) {
    label = '요청 전송 중...';
    icon = 'loader';
    disabled = true;
    title = '수동 스캔 요청을 전송하고 있습니다';
    baseClasses = 'w-full bg-coin-gold/15 text-coin-gold border border-coin-gold/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
  } else if (cycleActive) {
    label = phase + ' 진행 중' + (scannedCount ? ' · ' + analyzedCount + '/' + scannedCount : '');
    icon = 'loader';
    disabled = true;
    title = '현재 코인 사이클이 실행 중입니다';
    baseClasses = 'w-full bg-coin-gold/15 text-coin-gold border border-coin-gold/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
  } else if (state.manualScanQueuedAt && (Date.now() - state.manualScanQueuedAt) < 10000) {
    label = '사이클 시작 대기...';
    icon = 'clock-3';
    disabled = true;
    title = '요청은 접수되었고 사이클 시작을 기다리는 중입니다';
    baseClasses = 'w-full bg-blue-900/20 text-blue-300 border border-blue-400/20 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
  } else if (state.manualScanNotice) {
    label = state.manualScanNotice.label;
    icon = state.manualScanNotice.icon;
    title = state.manualScanNotice.label;
    baseClasses = 'w-full ' + state.manualScanNotice.colorClass + ' border text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2';
  }

  btn.className = baseClasses;
  btn.disabled = disabled;
  btn.title = title;
  btn.setAttribute('aria-label', title);

  // Build button content using DOM methods
  btn.textContent = '';
  var iconEl = document.createElement('i');
  iconEl.setAttribute('data-lucide', icon);
  iconEl.className = 'w-4 h-4 inline-block' + (icon === 'loader' ? ' animate-spin' : '');
  btn.appendChild(iconEl);
  btn.appendChild(document.createTextNode(' ' + label));
  refreshIcons();
}

// -- Report generation button --

export function renderReportButton() {
  var btn = document.getElementById('btn-generate-report');
  if (!btn) return;

  var cycleActive = !!(state.lastAgentState && state.lastAgentState.cycle_active);
  var cycleQueued = !!(state.manualScanQueuedAt && (Date.now() - state.manualScanQueuedAt) < 10000);

  var label = '수동 리포트 생성';
  var icon = 'file-text';
  var disabled = false;
  var title = '현재 시점 기준 코인 회고 리포트 생성';
  var baseClasses = 'w-full bg-blue-900/20 hover:bg-blue-900/30 text-blue-300 border border-blue-400/20 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2';

  if (state.manualReportRequestPending) {
    label = '리포트 생성 중...';
    icon = 'loader';
    disabled = true;
    title = '코인 리포트를 생성하고 있습니다';
    baseClasses = 'w-full bg-coin-gold/15 text-coin-gold border border-coin-gold/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
  } else if (state.manualScanRequestPending || cycleQueued || cycleActive) {
    label = cycleActive ? '사이클 진행 중' : '사이클 시작 대기...';
    icon = 'loader';
    disabled = true;
    title = '코인 사이클과 동시에 리포트를 생성할 수 없습니다';
    baseClasses = 'w-full bg-gray-800 text-gray-500 border border-gray-700 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-not-allowed';
  }

  btn.className = baseClasses;
  btn.disabled = disabled;
  btn.title = title;
  btn.setAttribute('aria-label', title);

  // Build button content using DOM methods
  btn.textContent = '';
  var iconEl = document.createElement('i');
  iconEl.setAttribute('data-lucide', icon);
  iconEl.className = 'w-4 h-4 inline-block' + (icon === 'loader' ? ' animate-spin' : '');
  btn.appendChild(iconEl);
  btn.appendChild(document.createTextNode(' ' + label));
  refreshIcons();
}

// -- Manual scan notice --

export function setManualScanNotice(label, icon, colorClass, durationMs) {
  if (durationMs == null) durationMs = 3500;
  state.manualScanNotice = {
    label: label,
    icon: icon,
    colorClass: colorClass,
    expiresAt: Date.now() + durationMs,
  };
}

export function clearManualScanNotice() {
  state.manualScanNotice = null;
}

// -- Agent snapshot sync (called from Monitor) --

export function syncAgentSnapshot(agentState) {
  state.lastAgentState = agentState || null;
  var cycleActive = !!(agentState && agentState.cycle_active);
  var manualCycleActive = cycleActive && state.manualScanSessionObserved;

  if (manualCycleActive) {
    state.manualScanQueuedAt = null;
    clearManualScanNotice();
  } else if (state.lastManualCycleActive) {
    setManualScanNotice('최근 스캔 완료', 'check', 'bg-green-900/20 text-green-300 border-green-400/20');
    state.manualScanSessionObserved = false;
  } else if (state.manualScanQueuedAt && (Date.now() - state.manualScanQueuedAt) >= 10000) {
    state.manualScanQueuedAt = null;
    state.manualScanSessionObserved = false;
    setManualScanNotice('시작 확인 지연', 'alert-triangle', 'bg-yellow-900/20 text-yellow-300 border-yellow-400/20', 4500);
  }

  state.lastManualCycleActive = manualCycleActive;
  renderActionButtons();
}

// Register with coin-state so Monitor can call syncAgentSnapshot
registerSyncAgentSnapshot(syncAgentSnapshot);
