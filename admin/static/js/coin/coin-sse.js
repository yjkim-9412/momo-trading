// ── coin-sse.js — SSE connection for coin admin dashboard ──
//
// Security note: all innerHTML below uses only static markup or
// values pre-escaped via escapeHtml(). This mirrors the original coin-app.js exactly.

import { refreshIcons, updateBadge } from '../shared/admin-core.js';
import * as Toast from '../shared/admin-toast.js';
import * as Monitor from '../shared/admin-monitor.js';
import * as Feed from '../shared/admin-feed.js';
import { state, API, rememberFeedActivities } from './coin-state.js';
import { setStatusText } from './coin-utils.js';

// Late-bound references for functions defined in other coin modules.
// These are set by coin-main.js after all modules load.
let _loadAgentState = null;
let _loadWatchlist = null;
let _loadLatestReport = null;
let _loadReportList = null;
let _loadSystemStatus = null;
let _scheduleAccountOverviewRefresh = null;

export function registerSSEDeps(deps) {
  _loadAgentState = deps.loadAgentState;
  _loadWatchlist = deps.loadWatchlist;
  _loadLatestReport = deps.loadLatestReport;
  _loadReportList = deps.loadReportList;
  _loadSystemStatus = deps.loadSystemStatus;
  _scheduleAccountOverviewRefresh = deps.scheduleAccountOverviewRefresh;
}

// -- Disconnect banner --

export function showSSEDisconnectBanner() {
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
  refreshIcons();
}

export function removeSSEDisconnectBanner() {
  var el = document.getElementById('sse-disconnect-banner');
  if (el) el.remove();
}

// -- SSE Connection --

export function connectSSE() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }

  var es = new EventSource(API + '/stream');
  state.eventSource = es;

  es.onopen = function () {
    updateBadge('badge-sse', '연결', 'green');
    setStatusText('SSE 연결됨', '#34d399');
    removeSSEDisconnectBanner();
    Toast.requestNotificationPermission();
    setTimeout(function () {
      if (_loadAgentState) {
        _loadAgentState({ quiet: true }).then(function () {
          Monitor.render();
        });
      }
    }, 100);
  };

  es.onmessage = function (e) {
    try {
      var msg = JSON.parse(e.data);
      if (msg.type === 'connected') return;

      if (msg.type === 'agent_state') {
        Monitor.handleAgentStateEvent(msg.data);
        return;
      }

      if (msg.type === 'activity') {
        var data = msg.data || msg;
        Monitor.updateFromActivity(data);

        var importance = Toast.classifyImportance(data);
        if (importance.level !== 'NONE') {
          Toast.show('[CRYPTO] ' + importance.title + ': ' + (data.symbol || '') + ' ' + (data.summary || ''), {
            level: importance.level,
            persistent: importance.level === 'CRITICAL',
            onClick: function () { Feed.navigateToCard(data); },
          });
          if (document.hidden) {
            Toast.sendBrowserNotification({
              level: importance.level,
              title: '[CRYPTO] ' + importance.title,
              body: importance.body,
            });
            Toast.flashTitle(
              (importance.level === 'CRITICAL' ? '[긴급]' : '[알림]') + ' [CRYPTO] ' + importance.title
            );
          }
        }

        if (state.currentView === 'live') {
          Feed.appendActivity(data);
          if (importance.level === 'CRITICAL' || importance.level === 'HIGH') {
            setTimeout(function () { Feed.highlightCard(data); }, 100);
          }
        } else {
          rememberFeedActivities([data]);
        }

        if (data.phase === 'COMPLETE' && ['DECISION', 'ORDER', 'TRADE_RESULT'].indexOf(data.activity_type) !== -1) {
          if (_scheduleAccountOverviewRefresh) _scheduleAccountOverviewRefresh(2000);
        }
        if (data.phase === 'COMPLETE' && ['SCAN', 'CYCLE', 'DECISION'].indexOf(data.activity_type) !== -1) {
          if (_loadWatchlist) setTimeout(_loadWatchlist, 1500);
        }
        if (data.activity_type === 'REPORT' && data.phase === 'COMPLETE') {
          if (_loadLatestReport) setTimeout(_loadLatestReport, 800);
          if (_loadReportList) setTimeout(_loadReportList, 900);
        }
        if (['CYCLE', 'DECISION', 'ORDER', 'TRADE_RESULT'].indexOf(data.activity_type) !== -1) {
          if (_loadSystemStatus) setTimeout(_loadSystemStatus, 900);
        }
      }

      if (msg.type === 'account_changed') {
        if (_scheduleAccountOverviewRefresh) _scheduleAccountOverviewRefresh();
        if (_loadSystemStatus) setTimeout(_loadSystemStatus, 500);
      }
    } catch (err) {
      console.error('[coin-sse] parse error', err);
    }
  };

  es.onerror = function () {
    updateBadge('badge-sse', '끊김', 'red');
    setStatusText('SSE 재연결 중...', '#f87171');
    showSSEDisconnectBanner();
    setTimeout(function () {
      if (es.readyState === EventSource.CLOSED) connectSSE();
    }, 3000);
    setTimeout(function () {
      if (_loadAgentState) {
        _loadAgentState({ quiet: true }).then(function () {
          Monitor.render();
        });
      }
    }, 4000);
  };
}

// -- Cleanup on page unload --
window.addEventListener('beforeunload', function () {
  if (state.eventSource) state.eventSource.close();
});
