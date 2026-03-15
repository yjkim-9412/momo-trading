/**
 * coin-sse.js -- SSE connection for coin admin dashboard
 *
 * Single-market SSE stream handling: agent_state, activity,
 * account_changed events. Disconnect banner management.
 *
 * Security note: all innerHTML below uses only static markup or
 * values pre-escaped via Admin.escapeHtml(). This mirrors the
 * original coin-app.js exactly.
 */
(function () {
  'use strict';

  var Coin = Admin.Coin;

  // -- Disconnect banner --

  Coin.showSSEDisconnectBanner = function () {
    if (document.getElementById('sse-disconnect-banner')) return;
    var main = document.querySelector('main');
    if (!main) return;
    var banner = document.createElement('div');
    banner.id = 'sse-disconnect-banner';
    banner.className = 'sse-disconnect-banner shrink-0';
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', 'wifi-off');
    icon.className = 'w-4 h-4';
    banner.appendChild(icon);
    banner.appendChild(document.createTextNode(' 실시간 연결이 끊겼습니다. 재연결 중...'));
    main.insertBefore(banner, main.children[1]);
    Admin.refreshIcons();
  };

  Coin.removeSSEDisconnectBanner = function () {
    var el = document.getElementById('sse-disconnect-banner');
    if (el) el.remove();
  };

  // -- SSE Connection --

  Coin.connectSSE = function () {
    if (Coin.eventSource) {
      Coin.eventSource.close();
      Coin.eventSource = null;
    }

    var es = new EventSource(Admin.API + '/stream');
    Coin.eventSource = es;

    es.onopen = function () {
      Admin.updateBadge('badge-sse', '연결', 'green');
      Coin.setStatusText('SSE 연결됨', '#34d399');
      Coin.removeSSEDisconnectBanner();
      Admin.Toast.requestNotificationPermission();
      setTimeout(function () {
        Coin.loadAgentState({ quiet: true }).then(function () {
          Admin.Monitor.render();
        });
      }, 100);
    };

    es.onmessage = function (e) {
      try {
        var msg = JSON.parse(e.data);
        if (msg.type === 'connected') return;

        if (msg.type === 'agent_state') {
          Admin.Monitor.handleAgentStateEvent(msg.data);
          return;
        }

        if (msg.type === 'activity') {
          var data = msg.data || msg;
          Admin.Monitor.updateFromActivity(data);

          var importance = Admin.Toast.classifyImportance(data);
          if (importance.level !== 'NONE') {
            Admin.Toast.show('[CRYPTO] ' + importance.title + ': ' + (data.symbol || '') + ' ' + (data.summary || ''), {
              level: importance.level,
              persistent: importance.level === 'CRITICAL',
              onClick: function () { Admin.Feed.navigateToCard(data); },
            });
            if (document.hidden) {
              Admin.Toast.sendBrowserNotification({
                level: importance.level,
                title: '[CRYPTO] ' + importance.title,
                body: importance.body,
              });
              Admin.Toast.flashTitle(
                (importance.level === 'CRITICAL' ? '[긴급]' : '[알림]') + ' [CRYPTO] ' + importance.title
              );
            }
          }

          if (Coin.currentView === 'live') {
            Admin.Feed.appendActivity(data);
            if (importance.level === 'CRITICAL' || importance.level === 'HIGH') {
              setTimeout(function () { Admin.Feed.highlightCard(data); }, 100);
            }
          } else {
            Coin.rememberFeedActivities([data]);
          }

          if (data.phase === 'COMPLETE' && ['DECISION', 'ORDER', 'TRADE_RESULT'].indexOf(data.activity_type) !== -1) {
            Coin.scheduleAccountOverviewRefresh(2000);
          }
          if (data.phase === 'COMPLETE' && ['SCAN', 'CYCLE', 'DECISION'].indexOf(data.activity_type) !== -1) {
            setTimeout(Coin.loadWatchlist, 1500);
          }
          if (data.activity_type === 'REPORT' && data.phase === 'COMPLETE') {
            setTimeout(Coin.loadLatestReport, 800);
            setTimeout(Coin.loadReportList, 900);
          }
          if (['CYCLE', 'DECISION', 'ORDER', 'TRADE_RESULT'].indexOf(data.activity_type) !== -1) {
            setTimeout(Coin.loadSystemStatus, 900);
          }
        }

        if (msg.type === 'account_changed') {
          Coin.scheduleAccountOverviewRefresh();
        }
      } catch (err) {
        console.error('[coin-sse] parse error', err);
      }
    };

    es.onerror = function () {
      Admin.updateBadge('badge-sse', '끊김', 'red');
      Coin.setStatusText('SSE 재연결 중...', '#f87171');
      Coin.showSSEDisconnectBanner();
      setTimeout(function () {
        if (es.readyState === EventSource.CLOSED) Coin.connectSSE();
      }, 3000);
      setTimeout(function () {
        Coin.loadAgentState({ quiet: true }).then(function () {
          Admin.Monitor.render();
        });
      }, 4000);
    };
  };

  // -- Cleanup on page unload --
  window.addEventListener('beforeunload', function () {
    if (Coin.eventSource) Coin.eventSource.close();
  });
})();
