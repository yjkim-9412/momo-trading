/**
 * app-sse.js — SSE connection with multi-market routing
 *
 * Manages the EventSource connection, routes activity/agent_state/account_changed
 * events to the correct market scope, and handles toast/notification dispatch.
 *
 * NOTE: innerHTML usage here mirrors the original app.js patterns for building
 * disconnect banners from trusted static content (no user input).
 */
(function () {
  'use strict';

  var App = Admin.App;
  var currentEventSource = null;

  // ── Cleanup on page unload ──
  window.addEventListener('beforeunload', function () {
    if (currentEventSource) currentEventSource.close();
  });

  // ── Main SSE connection ──
  App.connectSSE = function connectSSE() {
    if (currentEventSource) {
      currentEventSource.close();
      currentEventSource = null;
    }
    var es = new EventSource(Admin.API + '/stream');
    currentEventSource = es;

    es.onopen = function () {
      App.setStatus('connected', 'SSE \uC5F0\uACB0\uB428');
      Admin.updateBadge('badge-sse', '\uC5F0\uACB0', 'green');
      removeSSEDisconnectBanner();
      Admin.Toast.requestNotificationPermission();
    };

    es.onmessage = function (e) {
      try {
        var msg = JSON.parse(e.data);
        if (msg.type === 'connected') return;

        // Agent pipeline monitor events
        if (msg.type === 'agent_state') {
          Admin.Monitor.handleAgentStateEvent(msg.data);
          return;
        }

        if (msg.type === 'activity') {
          var eventScope = (msg.data && msg.data.market_scope) || 'KRX';
          var scopeKey = eventScope === 'KRX' ? 'KRX' : 'US';
          var myScope = App.currentScope();

          // Agent Monitor routing (all markets)
          Admin.Monitor.updateFromActivity(msg.data);

          // Importance classification (all markets)
          var importance = Admin.Toast.classifyImportance(msg.data);
          if (importance.level !== 'NONE') {
            var scopeLabel = scopeKey === 'KRX' ? '\uAD6D\uB0B4' : '\uD574\uC678';
            var fullTitle = '[' + scopeLabel + '] ' + importance.title;

            Admin.Toast.show(fullTitle + ': ' + (msg.data.symbol || '') + ' ' + (msg.data.summary || ''), {
              level: importance.level,
              persistent: importance.level === 'CRITICAL',
              onClick: function () { Admin.Feed.navigateToCard(msg.data); },
            });

            if (document.hidden) {
              Admin.Toast.sendBrowserNotification({ level: importance.level, title: fullTitle, body: importance.body });
              Admin.Toast.flashTitle((importance.level === 'CRITICAL' ? '\uD83D\uDD34' : '\uD83D\uDFE1') + ' ' + fullTitle);
            }
          }

          // Activity feed routing
          if (App.currentView === 'live') {
            if (scopeKey === myScope) {
              Admin.Feed.appendActivity(msg.data);
              if (importance.level === 'CRITICAL' || importance.level === 'HIGH') {
                setTimeout(function () { Admin.Feed.highlightCard(msg.data); }, 100);
              }
            } else {
              var buf = App.activityBuffer[scopeKey];
              if (buf) {
                buf.push(msg.data);
                if (buf.length > App.BUFFER_MAX) buf.splice(0, buf.length - App.BUFFER_MAX);
                App.updateBackgroundBadge(scopeKey, buf.length);
              }
            }
          }

          // Account refresh on trade events
          if (msg.data && msg.data.phase === 'COMPLETE' &&
              ['DECISION', 'ORDER', 'TRADE_RESULT'].indexOf(msg.data.activity_type) !== -1) {
            if (scopeKey === myScope) setTimeout(App.loadMarketAccountInfo, 2000);
          }

          // Watchlist refresh on scan/cycle/decision events
          if (msg.data && msg.data.phase === 'COMPLETE' &&
              ['SCAN', 'CYCLE', 'DECISION'].indexOf(msg.data.activity_type) !== -1) {
            if (scopeKey === myScope) setTimeout(App.loadWatchlist, 1500);
          }
        }

        if (msg.type === 'account_changed') {
          App.loadMarketAccountInfo();
          App.loadWatchlist();
        }
      } catch (err) {
        console.error('SSE parse error', err);
      }
    };

    es.onerror = function () {
      App.setStatus('disconnected', 'SSE \uC7AC\uC5F0\uACB0 \uC911...');
      Admin.updateBadge('badge-sse', '\uB04A\uAE40', 'red');
      showSSEDisconnectBanner();
      setTimeout(function () {
        if (es.readyState === EventSource.CLOSED) App.connectSSE();
      }, 3000);
      // Re-bootstrap monitor on reconnect
      setTimeout(App.initAgentMonitor, 4000);
    };
  };

  // ── SSE disconnect banner (trusted static content) ──
  function showSSEDisconnectBanner() {
    if (document.getElementById('sse-disconnect-banner')) return;
    var main = document.querySelector('main');
    if (!main) return;
    var banner = document.createElement('div');
    banner.id = 'sse-disconnect-banner';
    banner.className = 'sse-disconnect-banner shrink-0';
    // Static trusted markup — no user input
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', 'wifi-off');
    icon.className = 'w-4 h-4';
    banner.appendChild(icon);
    banner.appendChild(document.createTextNode(' \uC2E4\uC2DC\uAC04 \uC5F0\uACB0\uC774 \uB04A\uACBC\uC2B5\uB2C8\uB2E4. \uC7AC\uC5F0\uACB0 \uC911...'));
    main.insertBefore(banner, main.children[1]); // After filter bar
    Admin.refreshIcons();
  }

  function removeSSEDisconnectBanner() {
    var banner = document.getElementById('sse-disconnect-banner');
    if (banner) banner.remove();
  }

  // ── Status bar helper ──
  App.setStatus = function (_state, text) {
    var el = document.getElementById('status-text');
    if (el) el.textContent = text;
  };

  // ── Background badge for inactive market tab ──
  App.updateBackgroundBadge = function (scope, count) {
    var badge = document.getElementById('market-badge-' + scope);
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count > 99 ? '99+' : String(count);
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  };

  // ── Agent monitor bootstrap ──
  App.initAgentMonitor = async function () {
    try {
      var res = await fetch(Admin.API + '/agent/state');
      var json = await res.json();
      if (json.data) {
        ['KRX', 'US'].forEach(function (scope) {
          var d = json.data[scope];
          if (d && d.cycle_active) {
            App.monitorState[scope].cycleActive = true;
            App.monitorState[scope].cycleId = d.cycle_id;
            App.monitorState[scope].startedAt = d.started_at ? new Date(d.started_at).getTime() : Date.now();
            App.monitorState[scope].scannedCount = d.scanned_count || 0;
            App.monitorState[scope].analyzedCount = d.analyzed_count || 0;
          }
        });
      }
    } catch (e) {
      console.warn('Agent monitor bootstrap failed:', e);
    }
    Admin.Monitor.render();
  };
})();
