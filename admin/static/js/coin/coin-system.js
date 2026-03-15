/**
 * coin-system.js -- System status, agent state, and watchlist
 *
 * Loads system status (including bithumb_connectivity), agent state,
 * scan schedule summary, and AI watchlist with WS gauge.
 *
 * Security note: all dynamic content is escaped via Admin.escapeHtml()
 * before DOM insertion. This mirrors the original coin-app.js exactly.
 */
(function () {
  'use strict';

  var Coin = Admin.Coin;

  function describeConnectivity(connectivity, errorLimit) {
    var limit = typeof errorLimit === 'number' ? errorLimit : 30;
    var meta = {
      ok: true,
      label: '미확인',
      statusText: '',
      className: 'text-gray-300',
      color: '#9ca3af',
    };
    if (!connectivity || !Object.keys(connectivity).length) {
      return meta;
    }

    var lastError = Coin.truncateText(connectivity.last_error || '', limit);
    var errorSuffix = lastError ? ' · ' + lastError : '';
    var stage = String(connectivity.last_error_stage || '');

    if (connectivity.dns_api_ok === false || connectivity.dns_ws_ok === false || stage === 'dns_resolution') {
      return {
        ok: false,
        label: 'DNS 오류' + errorSuffix,
        statusText: '빗썸 DNS 오류' + errorSuffix,
        className: 'text-red-300',
        color: '#f87171',
      };
    }
    if (connectivity.market_catalog_ok === false || stage === 'market_catalog') {
      return {
        ok: false,
        label: '마켓 카탈로그 오류' + errorSuffix,
        statusText: '빗썸 마켓 카탈로그 오류' + errorSuffix,
        className: 'text-red-300',
        color: '#f87171',
      };
    }
    if (connectivity.ticker_probe_ok === false || stage === 'ticker_probe') {
      return {
        ok: false,
        label: 'Ticker Probe 오류' + errorSuffix,
        statusText: '빗썸 ticker probe 오류' + errorSuffix,
        className: 'text-red-300',
        color: '#f87171',
      };
    }
    if (stage === 'ticker_batch') {
      return {
        ok: false,
        label: 'Ticker Batch 오류' + errorSuffix,
        statusText: '빗썸 ticker batch 오류' + errorSuffix,
        className: 'text-red-300',
        color: '#f87171',
      };
    }
    if (connectivity.public_api_ok === false || connectivity.last_error) {
      return {
        ok: false,
        label: 'Public API 오류' + errorSuffix,
        statusText: '빗썸 Public API 오류' + errorSuffix,
        className: 'text-red-300',
        color: '#f87171',
      };
    }

    return {
      ok: true,
      label: '정상',
      statusText: '정상',
      className: 'text-green-300',
      color: '#34d399',
    };
  }

  // -- Load system status --

  Coin.loadSystemStatus = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/system/status');
      var s = json.data;
      if (!s) return;
      Coin.lastSystemStatus = s;
      var tradingEnabled = s.trading_enabled ?? s.crypto_trading_enabled;
      var autonomyMode = s.autonomy_mode ?? s.crypto_autonomy_mode ?? 'SEMI_AUTO';
      var sseClients = Number(s.sse_clients ?? s.coin_sse_clients ?? 0);
      var todayTrades = Number(s.today_filled_order_count ?? 0);
      var uptime = Coin.formatDuration(Number(s.uptime_seconds ?? 0));
      var connectivity = s.bithumb_connectivity || {};

      // Header badges
      Admin.updateBadge('badge-crypto',
        s.crypto_enabled ? 'CRYPTO:ON' : 'CRYPTO:OFF',
        s.crypto_enabled ? 'green' : 'red');
      Admin.updateBadge('badge-trading',
        tradingEnabled ? '매매:ON' : '매매:OFF',
        tradingEnabled ? 'green' : 'red');

      var modeLabels = { AUTONOMOUS: '자동', FULL_AUTO: '자동', SEMI_AUTO: '반자동', MANUAL: '수동' };
      var modeColors = { AUTONOMOUS: 'green', FULL_AUTO: 'green', SEMI_AUTO: 'yellow', MANUAL: 'gray' };
      var mode = autonomyMode;
      Admin.updateBadge('badge-mode',
        modeLabels[mode] || mode,
        modeColors[mode] || 'gray');
      var recSection = document.getElementById('rec-queue-section');
      if (recSection) recSection.classList.toggle('hidden', mode !== 'SEMI_AUTO');

      // Track scan timeline data
      if (s.last_cycle_time) Coin.lastCycleTime = new Date(s.last_cycle_time).getTime();
      if (s.scan_interval_hours) Coin.scanIntervalHours = Number(s.scan_interval_hours);
      if (s.scan_interval_hours) {
        Coin.setTextContent('scan-cycle-label', Number(s.scan_interval_hours) + 'h 주기');
      }

      Coin.setInlineStatus(
        'sys-scan-engine',
        s.scheduler_running ? '동작 · ' + Number(s.scan_interval_hours || 0) + 'h 주기' : '중지',
        s.scheduler_running ? '#34d399' : '#fbbf24'
      );
      Coin.setInlineStatus(
        'sys-agent',
        s.last_cycle_status
          ? (s.agent_running ? '준비됨' : '중지') + ' · 마지막 ' + s.last_cycle_status
          : (s.agent_running ? '준비됨' : '중지'),
        s.last_cycle_error ? '#f87171' : (s.agent_running ? '#34d399' : '#fbbf24')
      );
      Coin.setInlineStatus('sys-sse', sseClients + '명 구독', sseClients > 0 ? '#34d399' : '#9ca3af');

      var realtime = s.realtime || {};
      var desiredCount = Number(realtime.desired_count || 0);
      var activeCount = Number(realtime.subscription_count || realtime.active_count || 0);
      var realtimeLabel = realtime.connected
        ? '연결 · ' + activeCount + '/' + (desiredCount || activeCount)
        : (desiredCount ? desiredCount + '종목 대기' : '감시 대기');
      Coin.setInlineStatus(
        'sys-realtime',
        realtimeLabel,
        realtime.connected ? '#34d399' : (realtime.last_connect_error ? '#f87171' : '#9ca3af')
      );

      var privateSync = s.private_sync || {};
      var privateLabel = privateSync.connected
        ? '연결' + (privateSync.last_order_message_at ? ' · 주문 ' + Coin.formatTimeAgo(privateSync.last_order_message_at) : '') + (privateSync.last_asset_message_at ? ' · 자산 ' + Coin.formatTimeAgo(privateSync.last_asset_message_at) : '')
        : (privateSync.configured === false ? '미설정' : (privateSync.last_error ? '오류 · ' + Coin.truncateText(privateSync.last_error, 24) : '대기'));
      Coin.setInlineStatus(
        'sys-private-sync',
        privateLabel,
        privateSync.connected ? '#34d399' : (privateSync.last_error ? '#f87171' : '#9ca3af')
      );
      Coin.setInlineStatus('sys-uptime', uptime, '#d1d5db');
      Coin.setInlineStatus('sys-trades-today', todayTrades + '건', todayTrades > 0 ? '#fbbf24' : '#9ca3af');
      if (typeof s.watchlist_count === 'number') {
        Coin.setTextContent('watchlist-count', s.watchlist_count + '종목');
      }

      var sessionLabel = Coin.getSessionLabel(s.market_session);
      var connectivityMeta = describeConnectivity(connectivity, 88);
      if (s.last_cycle_error) {
        Coin.setStatusText('마지막 사이클 오류 · ' + Coin.truncateText(s.last_cycle_error, 88), '#f87171');
      } else if (!connectivityMeta.ok) {
        Coin.setStatusText(connectivityMeta.statusText, connectivityMeta.color);
      } else {
        var cycleState = s.last_cycle_status ? ' · 마지막 ' + s.last_cycle_status : '';
        Coin.setStatusText(sessionLabel + ' · 스캔 ' + (s.scheduler_running ? '동작' : '중지') + cycleState, '#9ca3af');
      }

      Coin.updateScanTimeline();
      Coin.renderScanScheduleSummary(s);
      Coin.renderActionButtons();
    } catch (err) {
      console.error('[coin] system status error:', err);
    }
  };

  // -- Session label --

  Coin.getSessionLabel = function (session) {
    var labels = {
      CRYPTO_ACTIVE: '24/7 ACTIVE',
      CLOSED: '대기',
    };
    return labels[session] || session || '대기';
  };

  // -- Load agent state --

  Coin.loadAgentState = async function (options) {
    var quiet = options && options.quiet;
    try {
      var json = await Admin.fetchJSON(Admin.API + '/agent/state');
      var state = json.data || {};
      Admin.Monitor.handleAgentStateEvent(state);
    } catch (err) {
      if (!quiet) console.error('[coin] agent state error:', err);
      Coin.renderActionButtons();
    }
  };

  // -- Scan schedule summary (includes bithumb_connectivity) --

  Coin.renderScanScheduleSummary = function (status) {
    var container = document.getElementById('scan-schedule');
    if (!container || !status) return;

    var nextScanTs = Coin.lastCycleTime && Coin.scanIntervalHours
      ? new Date(Coin.lastCycleTime + Coin.scanIntervalHours * 3600 * 1000).toISOString()
      : null;
    var sessionLabel = Coin.getSessionLabel(status.market_session);
    var connectivity = status.bithumb_connectivity || {};
    var connectivityMeta = describeConnectivity(connectivity, 30);

    container.innerHTML = `
    <div class="text-xs text-gray-300 space-y-2">
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">세션</span>
        <span class="text-gray-300">${Admin.escapeHtml(sessionLabel)}</span>
      </div>
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">주기</span>
        <span class="text-gray-300">${Admin.escapeHtml(Coin.scanIntervalHours ? Coin.scanIntervalHours + '시간 고정' : '--')}</span>
      </div>
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">최근 상태</span>
        <span class="text-gray-300">${Admin.escapeHtml(status.last_cycle_status || '대기')}</span>
      </div>
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">Bithumb 연결</span>
        <span class="${connectivityMeta.className}">${Admin.escapeHtml(connectivityMeta.label)}</span>
      </div>
      <div class="flex justify-between gap-3">
        <span class="text-gray-500">다음 예정</span>
        <span class="text-gray-300">${Admin.escapeHtml(nextScanTs ? Coin.formatDateTime(nextScanTs) : '--')}</span>
      </div>
    </div>
    `;
  };

  // -- Load watchlist --

  Coin.loadWatchlist = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/watchlist');
      var data = json.data || {};
      Coin.renderWatchlist(data);
    } catch (err) {
      console.error('[coin] watchlist load error:', err);
    }
  };

  // -- Render watchlist --

  Coin.renderWatchlist = function (data) {
    var container = document.getElementById('watchlist-list');
    var dot = document.getElementById('watchlist-stream-dot');
    if (!container) return;

    var payload = Array.isArray(data) ? { symbols: data } : (data || {});
    var items = Array.isArray(payload.symbols) ? payload.symbols : [];
    var stream = payload.stream_status || null;
    Coin.setTextContent('watchlist-count', items.length + '종목');
    if (dot) {
      var dotClass = stream
        ? (stream.connected ? 'bg-green-400' : (items.length ? 'bg-red-400' : 'bg-gray-600'))
        : (items.length ? 'bg-green-400' : 'bg-gray-600');
      dot.className = 'w-1.5 h-1.5 rounded-full ' + dotClass;
    }

    var streamSummary = stream ? (function () {
      var subscriptionLimit = Number(stream.subscription_limit || 0);
      var subscriptionCount = Number(stream.subscription_count || 0);
      var pct = subscriptionLimit > 0
        ? Math.max(0, Math.min(100, Math.round((subscriptionCount / subscriptionLimit) * 100)))
        : 0;
      var statusColor = stream.connected ? '#34d399' : (stream.last_connect_error ? '#f87171' : '#9ca3af');
      var stateText = stream.connected ? '연결됨' : (stream.last_connect_error ? '연결 오류' : '대기');
      var lastSeen = stream.last_message_at ? Coin.formatTimeAgo(stream.last_message_at) : '';
      return `
      <div class="coin-watchlist-stream">
        <div class="coin-watchlist-gauge">
          <span style="min-width:18px; color:${statusColor};">WS</span>
          <span class="coin-watchlist-gauge-track"><span class="coin-watchlist-gauge-fill" style="width:${pct}%"></span></span>
          <span>${subscriptionCount}/${subscriptionLimit || 0}</span>
        </div>
        <div class="flex items-center justify-between mt-2 text-[10px] text-gray-500">
          <span style="color:${statusColor};">${Admin.escapeHtml(stateText)}</span>
          <span>${Admin.escapeHtml(lastSeen ? lastSeen + ' 수신' : '수신 이력 없음')}</span>
        </div>
      </div>
      `;
    })() : '';

    if (!items.length) {
      container.innerHTML = streamSummary + '<div style="color:#6b7280; font-size:13px; padding:12px; text-align:center;">감시 코인 없음</div>';
      return;
    }

    container.innerHTML = streamSummary + items.map(function (s) {
      var sym = typeof s === 'string' ? s : (s.symbol || '');
      var name = typeof s === 'string' ? s : (s.name || s.symbol || '');
      var reason = typeof s === 'object' && s.reason ? s.reason : '';
      var price = typeof s === 'object' && s.price != null ? Coin.formatPrice(s.price) : '';
      var changeRate = typeof s === 'object' && s.change_rate != null ? Number(s.change_rate) : null;
      var changeColor = changeRate == null ? '#6b7280' : (changeRate >= 0 ? '#34d399' : '#f87171');
      var scanSource = typeof s === 'object' ? Coin.formatCoinScanSource(s.scan_source) : '';
      var isHolding = typeof s === 'object' && !!s.is_holding;
      var isSubscribed = typeof s === 'object' && !!s.is_subscribed;
      var statusLabel = isHolding ? '보유' : (isSubscribed ? '감시' : '대기');
      var statusClass = isHolding ? 'holding' : (isSubscribed ? 'watching' : 'pending');
      var thresholds = typeof s === 'object' ? (s.thresholds || null) : null;
      var thresholdItems = [];
      if (thresholds) {
        if (thresholds.surge_pct != null) thresholdItems.push('<span class="coin-watchlist-threshold">급등 +' + Number(thresholds.surge_pct).toFixed(1) + '%</span>');
        if (thresholds.drop_pct != null) thresholdItems.push('<span class="coin-watchlist-threshold">급락 ' + Number(thresholds.drop_pct).toFixed(1) + '%</span>');
        if (thresholds.volume_spike_ratio) thresholdItems.push('<span class="coin-watchlist-threshold">거래량 x' + Number(thresholds.volume_spike_ratio).toFixed(1) + '</span>');
        if (Number(thresholds.stop_loss || 0) > 0) thresholdItems.push('<span class="coin-watchlist-threshold">SL ' + Admin.escapeHtml(Coin.formatPrice(thresholds.stop_loss)) + '</span>');
        if (Number(thresholds.take_profit || 0) > 0) thresholdItems.push('<span class="coin-watchlist-threshold">TP ' + Admin.escapeHtml(Coin.formatPrice(thresholds.take_profit)) + '</span>');
        if (Number(thresholds.trailing_stop_pct || 0) > 0) thresholdItems.push('<span class="coin-watchlist-threshold">Trail ' + Number(thresholds.trailing_stop_pct).toFixed(1) + '%</span>');
      }

      return `
      <div class="coin-watchlist-card">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 min-w-0">
              <span style="color:#e2e8f0; font-weight:600;">${Admin.escapeHtml(sym)}</span>
              ${name && sym !== name ? '<span style="color:#9ca3af; font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">' + Admin.escapeHtml(name) + '</span>' : ''}
              ${scanSource ? '<span style="color:#a78bfa; font-size:10px;">' + Admin.escapeHtml(scanSource) + '</span>' : ''}
            </div>
            ${reason ? '<div style="color:#6b7280; font-size:11px; margin-top:3px;">' + Admin.escapeHtml(reason) + '</div>' : ''}
            ${price ? '<div style="color:#9ca3af; font-size:11px; margin-top:3px;">현재가 ' + Admin.escapeHtml(price) + '</div>' : ''}
            ${thresholdItems.length ? '<div class="coin-watchlist-thresholds">' + thresholdItems.join('') + '</div>' : ''}
          </div>
          <div class="text-right shrink-0">
            <span class="coin-watchlist-chip ${statusClass}">${statusLabel}</span>
            ${changeRate != null ? '<div style="color:' + changeColor + '; font-size:11px; margin-top:6px;">' + (changeRate >= 0 ? '+' : '') + changeRate.toFixed(2) + '%</div>' : ''}
          </div>
        </div>
      </div>
      `;
    }).join('');
  };
})();
