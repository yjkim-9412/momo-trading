// ── admin-monitor.js — Agent pipeline monitor ──
import { PIPELINE_STEPS, PIPELINE_LABELS, escapeHtml, refreshIcons } from './admin-core.js';

let _config = {};
let _renderTimer = null;
let _elapsedTimer = null;
let _expanded = true;

export function init(config) {
  _config = config || {};
}

// ── Public API ──

export function scheduleRender() {
  if (_renderTimer) return;
  _renderTimer = setTimeout(function () {
    _renderTimer = null;
    render();
  }, 80);
}

export function updateFromActivity(data) {
  var scope = _config.resolveScope ? _config.resolveScope(data) : null;
  var ms = scope ? _config.getScopeState(scope) : _config.getState();
  if (!ms) return;

  var activityType = data.activity_type;
  var phase = data.phase;
  var symbol = data.symbol;
  var summary = data.summary;

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
          var detail = typeof data.detail === 'string' ? JSON.parse(data.detail) : data.detail;
          ms.scannedCount = (detail.selected || []).length || ms.scannedCount;
        } catch (e) { /* ignore malformed payload */ }
      }
      break;

    case 'TIER1_ANALYSIS':
      if (phase === 'START' && symbol) {
        ms.slots[symbol] = { symbol: symbol, name: _extractName(summary, symbol), step: 'tier1', startedAt: Date.now() };
      } else if (phase === 'COMPLETE' && symbol) {
        if (ms.slots[symbol]) ms.slots[symbol].step = 'tier1_done';
        _tryResolveSlot(ms, symbol, data);
      } else if (phase === 'ERROR' && symbol) {
        _finishSlot(ms, symbol, 'error');
      } else if (phase === 'SKIP' && symbol) {
        _finishSlot(ms, symbol, 'skip');
      }
      break;

    case 'TIER2_REVIEW':
      if (symbol && ms.slots[symbol]) {
        ms.slots[symbol].step = phase === 'COMPLETE' ? 'tier2_done' : 'tier2';
        if ((summary || '').includes('\uBBF8\uC2B9\uC778')) _finishSlot(ms, symbol, 'hold');
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

    case 'TRADE_RESULT':
      if (symbol) {
        var existing = ms.completed.find(function (c) { return c.symbol === symbol; });
        if (existing) {
          var outcome = _detectOutcome(summary, data);
          if (outcome !== 'hold') existing.outcome = outcome;
        }
      }
      break;
  }

  // Persist state back if using scope-based setter
  if (scope && _config.setScopeState) _config.setScopeState(scope, ms);
  else if (_config.setState) _config.setState(ms);

  scheduleRender();
}

export function handleAgentStateEvent(data) {
  var scope = _config.resolveScope ? _config.resolveScope(data) : null;
  var ms = scope ? _config.getScopeState(scope) : _config.getState();
  if (!ms) return;

  if (data && data.cycle_active) {
    ms.cycleActive = true;
    ms.cycleId = data.cycle_id;
    ms.startedAt = data.started_at ? new Date(data.started_at).getTime() : Date.now();
    ms.scannedCount = data.scanned_count || 0;
    ms.analyzedCount = data.analyzed_count || 0;
    ms.slots = ms.slots || {};
  } else {
    if (ms.cycleActive) {
      ms.lastCycleSummary = {
        analyzedCount: (data && data.analyzed_count) || ms.analyzedCount,
        scannedCount: (data && data.scanned_count) || ms.scannedCount,
        elapsed: ms.startedAt ? Math.round((Date.now() - ms.startedAt) / 1000) : 0,
        time: new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      };
    }
    ms.cycleActive = false;
    ms.slots = {};
  }

  if (scope && _config.setScopeState) _config.setScopeState(scope, ms);
  else if (_config.setState) _config.setState(ms);

  scheduleRender();
}

/**
 * Optional: coin-app calls this to sync the agent snapshot for UI buttons.
 * Returns the same data object for chaining.
 */
export function syncAgentSnapshot(state) {
  // Apps can override this via _config.onSyncAgentSnapshot
  if (_config.onSyncAgentSnapshot) _config.onSyncAgentSnapshot(state);
  handleAgentStateEvent(state);
  return state;
}

export function render() {
  var ms = _config.getState();
  var iconEl = document.getElementById('monitor-status-icon');
  var statusEl = document.getElementById('monitor-status');
  var elapsedEl = document.getElementById('monitor-elapsed');
  var progressWrap = document.getElementById('monitor-progress-wrap');
  var progressBar = document.getElementById('monitor-progress-bar');
  var body = document.getElementById('monitor-body');
  var slotsEl = document.getElementById('monitor-slots');
  var completedWrap = document.getElementById('monitor-completed');
  var completedList = document.getElementById('monitor-completed-list');

  if (!iconEl || !statusEl || !elapsedEl || !progressWrap || !progressBar || !body || !slotsEl || !completedWrap || !completedList) return;

  if (ms.cycleActive) {
    iconEl.classList.add('active');
    var label = _config.scopeLabel ? _config.scopeLabel() : '';
    var total = ms.scannedCount || 1;
    var done = ms.analyzedCount;
    statusEl.textContent = label + ' 사이클 진행 중';
    statusEl.style.color = _config.activeColor || '#34d399';

    progressWrap.style.display = '';
    var pct = Math.min(100, Math.round((done / total) * 100));
    progressBar.style.width = pct + '%';

    if (ms.startedAt) {
      var sec = Math.round((Date.now() - ms.startedAt) / 1000);
      elapsedEl.textContent = sec + 's';
      _startElapsedTimer();
    }

    if (_expanded) {
      body.style.display = '';
      var slotSymbols = Object.keys(ms.slots);
      var slotsHTML = '';
      slotSymbols.forEach(function (sym) {
        var slot = ms.slots[sym];
        var stepIdx = PIPELINE_STEPS.indexOf(slot.step.replace('_done', ''));
        var isDone = slot.step.endsWith('_done');
        var slotSec = Math.round((Date.now() - slot.startedAt) / 1000);
        slotsHTML += '<div class="monitor-slot slot-active">'
          + '<div class="monitor-slot-symbol">' + escapeHtml(slot.symbol) + '</div>'
          + '<div class="monitor-slot-name">' + escapeHtml(slot.name) + '</div>'
          + '<div class="pipeline-steps">' + _renderPipeline(stepIdx, isDone) + '</div>'
          + '<div class="monitor-slot-timer">' + slotSec + 's \u00B7 ' + (PIPELINE_LABELS[slot.step.replace('_done', '')] || slot.step) + '</div>'
          + '</div>';
      });
      if (!slotSymbols.length && !ms.completed.length) {
        slotsHTML += '<div class="monitor-slot" style="opacity:0.3; grid-column: 1/-1; text-align:center;">'
          + '<div class="text-xs text-gray-600">스캔 완료 \u00B7 분석 대기 중...</div>'
          + '</div>';
      }
      slotsEl.textContent = '';
      slotsEl.insertAdjacentHTML('beforeend', slotsHTML);

      if (ms.completed.length > 0) {
        completedWrap.style.display = '';
        var cHTML = '';
        ms.completed.forEach(function (c) {
          var cls = 'badge-' + c.outcome;
          var outcomeLabel = { buy: '매수', sell: '매도', hold: '관망', error: '오류', skip: '스킵' }[c.outcome] || c.outcome;
          cHTML += '<span class="monitor-completed-badge ' + cls + '" title="' + escapeHtml(c.name) + ' (' + c.elapsed + 's)">'
            + escapeHtml(c.symbol) + ' <span style="opacity:0.7">' + outcomeLabel + '</span>'
            + '</span>';
        });
        completedList.textContent = '';
        completedList.insertAdjacentHTML('beforeend', cHTML);
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
      var s = ms.lastCycleSummary;
      statusEl.textContent = '대기 중';
      elapsedEl.textContent = '마지막: ' + s.time + ' (' + s.scannedCount + '종목, ' + s.elapsed + 's)';
    } else {
      statusEl.textContent = '대기 중';
      elapsedEl.textContent = '';
    }
  }

  refreshIcons();
}

function _renderPipeline(activeIdx, isDone) {
  var html = '';
  for (var i = 0; i < PIPELINE_STEPS.length; i++) {
    if (i > 0) {
      var connClass = i <= activeIdx ? 'conn-done' : '';
      html += '<div class="pipeline-connector ' + connClass + '"></div>';
    }
    var cls = 'pipeline-step';
    if (i < activeIdx || (i === activeIdx && isDone)) cls += ' step-done';
    else if (i === activeIdx) cls += ' step-active';
    html += '<div class="' + cls + '" title="' + PIPELINE_LABELS[PIPELINE_STEPS[i]] + '"></div>';
  }
  return html;
}

export function toggleExpand() {
  _expanded = !_expanded;
  render();
}

function _startElapsedTimer() {
  if (_elapsedTimer) return;
  _elapsedTimer = setInterval(function () {
    var ms = _config.getState();
    if (!ms.cycleActive || !ms.startedAt) { _stopElapsedTimer(); return; }
    var sec = Math.round((Date.now() - ms.startedAt) / 1000);
    var el = document.getElementById('monitor-elapsed');
    if (el) el.textContent = sec + 's';
  }, 1000);
}

function _stopElapsedTimer() {
  if (!_elapsedTimer) return;
  clearInterval(_elapsedTimer);
  _elapsedTimer = null;
}

function _extractName(summary, symbol) {
  var m = summary && summary.match(/\[([^\]]+)\]/);
  return m ? m[1] : symbol;
}

/**
 * Detect outcome from summary text and activity data.
 */
function _detectOutcome(summary, data) {
  if (data && (data.phase === 'ERROR' || data.error_message)) return 'error';
  if (!summary) return 'hold';
  var s = summary.toLowerCase();
  if (s.includes('\uB9E4\uC218') || s.includes('buy')) return 'buy';
  if (s.includes('\uB9E4\uB3C4') || s.includes('sell')) return 'sell';
  if (s.includes('\uC624\uB958') || s.includes('error') || s.includes('\uC2E4\uD328')) return 'error';
  if (s.includes('\uC2A4\uD0B5') || s.includes('skip') || s.includes('\uCC28\uB2E8')) return 'skip';
  return 'hold';
}

function _finishSlot(ms, symbol, outcome) {
  var slot = ms.slots[symbol];
  var elapsed = slot ? Math.round((Date.now() - slot.startedAt) / 1000) : 0;
  ms.completed.push({ symbol: symbol, name: slot ? slot.name : symbol, outcome: outcome, elapsed: elapsed });
  delete ms.slots[symbol];
  ms.analyzedCount = ms.completed.length;
  if (_config.onSlotFinished) _config.onSlotFinished(symbol, outcome, elapsed);
}

function _tryResolveSlot(ms, symbol, data) {
  var summary = (data && data.summary) || '';
  var normalized = summary.toUpperCase();
  if (normalized.includes('HOLD') || normalized.includes('\uAD00\uB9DD') || normalized.includes('\uBCF4\uB958')) {
    _finishSlot(ms, symbol, 'hold');
  }
}
