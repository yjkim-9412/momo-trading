/**
 * coin-actions.js -- Manual triggers and action buttons
 *
 * Manual scan trigger, manual report generation, and dynamic
 * button state rendering.
 *
 * Security note: all dynamic content is escaped via Admin.escapeHtml()
 * before DOM insertion. This mirrors the original coin-app.js exactly.
 */
(function () {
  'use strict';

  var Coin = Admin.Coin;

  // -- Trigger manual scan --

  Coin.triggerScan = async function () {
    var btn = document.getElementById('btn-trigger-scan');
    if (!btn) return;
    if (Coin.manualScanRequestPending || (Coin.lastAgentState && Coin.lastAgentState.cycle_active) || (Coin.manualScanQueuedAt && (Date.now() - Coin.manualScanQueuedAt) < 10000)) {
      return;
    }

    Coin.manualScanRequestPending = true;
    Coin.clearManualScanNotice();
    Coin.renderActionButtons();

    try {
      var json = await Admin.fetchJSON(Admin.API + '/agent/trigger', { method: 'POST' });
      var data = json.data;
      var msg = json.message || (data && data.message) || '스캔 트리거 완료';

      if (data && data.skipped) {
        var reason = data.reason || 'skipped';
        Coin.manualScanQueuedAt = null;
        Coin.manualScanSessionObserved = false;
        Coin.setManualScanNotice('스캔 스킵 · ' + reason, 'pause-circle', 'bg-yellow-900/20 text-yellow-300 border-yellow-400/20', 4500);
        Admin.Toast.show(msg, 'info');
      } else {
        Coin.manualScanQueuedAt = Date.now();
        Coin.manualScanSessionObserved = true;
        Coin.lastCycleTime = Date.now();
        Coin.updateScanTimeline();
        Coin.setStatusText('수동 스캔 요청 접수됨', '#fbbf24');
        Admin.Toast.show(msg, 'success');
      }

      setTimeout(Coin.loadAgentState, 300);
      setTimeout(Coin.loadSystemStatus, 600);
    } catch (err) {
      console.error('[coin] scan trigger error:', err);
      Coin.manualScanQueuedAt = null;
      Coin.manualScanSessionObserved = false;
      Coin.setManualScanNotice('요청 실패', 'x', 'bg-red-900/20 text-red-300 border-red-400/20', 5000);
      Coin.setStatusText('수동 스캔 요청 실패 · ' + Coin.truncateText(err.message, 80), '#f87171');
      Admin.Toast.show('수동 스캔 실패: ' + err.message, 'error');
    } finally {
      Coin.manualScanRequestPending = false;
      Coin.renderActionButtons();
    }
  };

  // -- Generate manual report --

  Coin.generateManualReport = async function () {
    var btn = document.getElementById('btn-generate-report');
    if (!btn) return;
    if (Coin.manualReportRequestPending || Coin.manualScanRequestPending || (Coin.lastAgentState && Coin.lastAgentState.cycle_active) || (Coin.manualScanQueuedAt && (Date.now() - Coin.manualScanQueuedAt) < 10000)) {
      return;
    }

    Coin.manualReportRequestPending = true;
    Coin.renderActionButtons();

    try {
      var json = await Admin.fetchJSON(Admin.API + '/reports/generate', { method: 'POST' });
      var msg = json.message || '리포트 생성 완료';
      Coin.setStatusText('수동 리포트 생성 완료', '#93c5fd');
      Admin.Toast.show(msg, 'success');
      Coin.loadLatestReport();
      Coin.loadReportList();
      if (Coin.currentView === 'report') {
        Coin.loadCurrentReportView({ forceLatest: Coin.currentReportSelection.mode === 'latest' });
      }
      setTimeout(Coin.loadSystemStatus, 400);
    } catch (err) {
      console.error('[coin] report generate error:', err);
      Coin.setStatusText('수동 리포트 생성 실패 · ' + Coin.truncateText(err.message, 80), '#f87171');
      Admin.Toast.show('수동 리포트 생성 실패: ' + err.message, 'error');
    } finally {
      Coin.manualReportRequestPending = false;
      Coin.renderActionButtons();
    }
  };

  // -- Render action buttons area --

  Coin.renderActionButtons = function () {
    Coin.renderTriggerButton();
    Coin.renderReportButton();
  };

  // -- Dynamic trigger button --

  Coin.renderTriggerButton = function () {
    var btn = document.getElementById('btn-trigger-scan');
    if (!btn) return;

    if (Coin.manualScanNotice && Coin.manualScanNotice.expiresAt <= Date.now()) {
      Coin.manualScanNotice = null;
    }

    var cycleActive = !!(Coin.lastAgentState && Coin.lastAgentState.cycle_active);
    var cryptoEnabled = !(Coin.lastSystemStatus && Coin.lastSystemStatus.crypto_enabled === false);
    var phase = (Coin.AGENT_PHASE_LABELS[Coin.lastAgentState && Coin.lastAgentState.phase] || (Coin.lastAgentState && Coin.lastAgentState.phase) || '스캔');
    var analyzedCount = Number((Coin.lastAgentState && Coin.lastAgentState.analyzed_count) || 0);
    var scannedCount = Number((Coin.lastAgentState && Coin.lastAgentState.scanned_count) || 0);

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
    } else if (Coin.manualScanRequestPending) {
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
    } else if (Coin.manualScanQueuedAt && (Date.now() - Coin.manualScanQueuedAt) < 10000) {
      label = '사이클 시작 대기...';
      icon = 'clock-3';
      disabled = true;
      title = '요청은 접수되었고 사이클 시작을 기다리는 중입니다';
      baseClasses = 'w-full bg-blue-900/20 text-blue-300 border border-blue-400/20 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
    } else if (Coin.manualScanNotice) {
      label = Coin.manualScanNotice.label;
      icon = Coin.manualScanNotice.icon;
      title = Coin.manualScanNotice.label;
      baseClasses = 'w-full ' + Coin.manualScanNotice.colorClass + ' border text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2';
    }

    btn.className = baseClasses;
    btn.disabled = disabled;
    btn.title = title;
    btn.setAttribute('aria-label', title);
    btn.innerHTML = '<i data-lucide="' + icon + '" class="w-4 h-4 inline-block ' + (icon === 'loader' ? 'animate-spin' : '') + '"></i> ' + Admin.escapeHtml(label);
    Admin.refreshIcons();
  };

  // -- Report generation button --

  Coin.renderReportButton = function () {
    var btn = document.getElementById('btn-generate-report');
    if (!btn) return;

    var cycleActive = !!(Coin.lastAgentState && Coin.lastAgentState.cycle_active);
    var cycleQueued = !!(Coin.manualScanQueuedAt && (Date.now() - Coin.manualScanQueuedAt) < 10000);

    var label = '수동 리포트 생성';
    var icon = 'file-text';
    var disabled = false;
    var title = '현재 시점 기준 코인 회고 리포트 생성';
    var baseClasses = 'w-full bg-blue-900/20 hover:bg-blue-900/30 text-blue-300 border border-blue-400/20 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2';

    if (Coin.manualReportRequestPending) {
      label = '리포트 생성 중...';
      icon = 'loader';
      disabled = true;
      title = '코인 리포트를 생성하고 있습니다';
      baseClasses = 'w-full bg-coin-gold/15 text-coin-gold border border-coin-gold/30 text-sm font-medium rounded-lg py-2 transition flex items-center justify-center gap-2 cursor-wait';
    } else if (Coin.manualScanRequestPending || cycleQueued || cycleActive) {
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
    btn.innerHTML = '<i data-lucide="' + icon + '" class="w-4 h-4 inline-block ' + (icon === 'loader' ? 'animate-spin' : '') + '"></i> ' + Admin.escapeHtml(label);
    Admin.refreshIcons();
  };

  // -- Manual scan notice --

  Coin.setManualScanNotice = function (label, icon, colorClass, durationMs) {
    if (durationMs == null) durationMs = 3500;
    Coin.manualScanNotice = {
      label: label,
      icon: icon,
      colorClass: colorClass,
      expiresAt: Date.now() + durationMs,
    };
  };

  Coin.clearManualScanNotice = function () {
    Coin.manualScanNotice = null;
  };

  // -- Agent snapshot sync (called from Monitor) --

  Coin.syncAgentSnapshot = function (state) {
    Coin.lastAgentState = state || null;
    var cycleActive = !!(state && state.cycle_active);
    var manualCycleActive = cycleActive && Coin.manualScanSessionObserved;

    if (manualCycleActive) {
      Coin.manualScanQueuedAt = null;
      Coin.clearManualScanNotice();
    } else if (Coin.lastManualCycleActive) {
      Coin.setManualScanNotice('최근 스캔 완료', 'check', 'bg-green-900/20 text-green-300 border-green-400/20');
      Coin.manualScanSessionObserved = false;
    } else if (Coin.manualScanQueuedAt && (Date.now() - Coin.manualScanQueuedAt) >= 10000) {
      Coin.manualScanQueuedAt = null;
      Coin.manualScanSessionObserved = false;
      Coin.setManualScanNotice('시작 확인 지연', 'alert-triangle', 'bg-yellow-900/20 text-yellow-300 border-yellow-400/20', 4500);
    }

    Coin.lastManualCycleActive = manualCycleActive;
    Coin.renderActionButtons();
  };
})();
