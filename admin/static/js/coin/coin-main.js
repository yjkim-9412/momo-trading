/**
 * coin-main.js -- Initialization, view switching, UI controls, and global exports
 *
 * DOMContentLoaded handler, view switching, feed management,
 * scroll management, sidebar/section toggles, scan timeline,
 * metric helpers, and window-level onclick handler exports.
 *
 * Security note: all dynamic content is escaped via Admin.escapeHtml()
 * before DOM insertion. This mirrors the original coin-app.js exactly.
 */
(function () {
  'use strict';

  var Coin = Admin.Coin;

  // -- View switching --

  Coin.switchView = function (view) {
    if (view === 'live') {
      Coin.currentView = 'live';
      Coin.currentReportSelection = { mode: null, id: null };
      Coin.updateNavigationState();
      Coin.updateMainPanelChrome();
      if (Coin.feedState.loaded) {
        Coin.renderLiveFeedFromState({ restoreScroll: true });
      } else {
        Coin.loadTodayActivities();
      }
      return;
    }

    Coin.currentView = 'report';
    Coin.currentReportSelection = {
      mode: view === 'latest-report' ? 'latest' : Coin.currentReportSelection.mode,
      id: view === 'latest-report' ? null : Coin.currentReportSelection.id,
    };
    Coin.updateNavigationState();
    Coin.updateMainPanelChrome();
    Coin.loadCurrentReportView();
  };

  Coin.switchToReport = function (reportId) {
    Coin.currentView = 'report';
    Coin.currentReportSelection = { mode: 'id', id: reportId };
    Coin.updateNavigationState();
    Coin.updateMainPanelChrome();
    Coin.loadCurrentReportView();
  };

  Coin.handleMainClear = function () {
    if (Coin.currentView === 'live') {
      Coin.clearChat();
      return;
    }
    Coin.switchView('live');
  };

  Coin.refreshCurrentView = function () {
    if (Coin.currentView === 'live') {
      Coin.loadTodayActivities();
      return;
    }
    Coin.loadCurrentReportView({ forceLatest: Coin.currentReportSelection.mode === 'latest' });
  };

  // -- Log filter --

  Coin.setLogFilter = function (filter) {
    if (Coin.currentLogFilter === filter) return;
    Coin.currentLogFilter = filter;
    document.querySelectorAll('.log-filter-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.filter === filter);
    });
    Object.values(Coin.getStockCards()).forEach(function (card) {
      Coin.applyFilterToCard(card);
    });
    document.querySelectorAll('.chat-bubble').forEach(function (el) {
      if (filter === 'SIGNAL') {
        el.style.display = (el.textContent.indexOf('매수') !== -1 || el.textContent.indexOf('매도') !== -1 || el.textContent.indexOf('BUY') !== -1 || el.textContent.indexOf('SELL') !== -1)
          ? ''
          : 'none';
      } else {
        el.style.display = '';
      }
    });
    if (Coin.autoScroll) {
      var container = document.getElementById('chat-container');
      if (container) container.scrollTop = container.scrollHeight;
    }
  };

  Coin.applyFilterToCard = function (card) {
    if (Coin.currentLogFilter === 'ALL') {
      card.element.style.display = '';
      return;
    }
    card.element.style.display = ['buy', 'sell', 'hold'].indexOf(card.outcome) !== -1 ? '' : 'none';
  };

  // -- Feed management --

  Coin.clearChat = function () {
    if (!confirm('화면을 비울까요? (DB는 유지됩니다)')) return;
    var container = document.getElementById('chat-container');
    if (!container) return;
    container.innerHTML = '<div class="text-center text-gray-500 text-sm py-8">화면을 비웠습니다. 새 활동이 들어오면 여기에 표시됩니다.</div>';
    Coin.resetFeedState({ preserveLoaded: true });
    Coin.cleanupStockCards();
    Coin.autoScroll = true;
    Coin.missedCount = 0;
    Coin.feedState.loaded = true;
    Coin.setActivityCount(0);
    Coin.setTextContent('activity-count', '0건');
    Coin.updateFeedLoadMore();
    Coin.updateScrollFab();
    Coin.setStatusText('활동 피드를 비웠습니다', '#9ca3af');
  };

  Coin.clearFeed = function () { Coin.clearChat(); };
  Coin.refreshFeed = function () { Coin.loadTodayActivities(); };

  // -- Feed data loading --

  Coin.loadTodayActivities = async function () {
    var container = document.getElementById('chat-container');
    if (!container) return;

    Coin.autoScroll = true;
    Coin.missedCount = 0;
    Admin.renderPlaceholder(container, 'loading', '불러오는 중...');
    Coin.cleanupStockCards();
    Coin.resetFeedState();
    Coin.updateFeedLoadMore();
    Coin.updateScrollFab();

    try {
      var json = await Admin.fetchJSON(Coin.buildActivityFeedUrl({ limit: 100 }));
      var feed = json.data || {};
      var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
      Coin.rememberFeedActivities(page);
      Coin.feedState.feedTradingDate = feed.resolved_trading_date || null;
      Coin.feedState.feedHasMore = !!feed.has_more;
      Coin.feedState.feedCursor = feed.next_cursor || null;
      Coin.feedState.loaded = true;
      Coin.renderLiveFeedFromState();
      Coin.setStatusText('현재 거래일 활동을 불러왔습니다', '#9ca3af');
    } catch (err) {
      Admin.renderPlaceholder(container, 'error', '로드 실패: ' + err.message);
      Coin.updateFeedLoadMore();
      Coin.setStatusText('활동 피드 로드 실패 · ' + Coin.truncateText(err.message, 48), '#f87171');
    }
  };

  Coin.loadMoreActivities = async function () {
    if (Coin.feedState.feedLoading || !Coin.feedState.feedHasMore || !Coin.feedState.feedCursor) return;

    var container = document.getElementById('chat-container');
    if (!container) return;

    var previousScrollHeight = container.scrollHeight;
    var previousScrollTop = container.scrollTop;
    Coin.feedState.feedLoading = true;
    Coin.updateFeedLoadMore();

    try {
      var json = await Admin.fetchJSON(Coin.buildActivityFeedUrl({
        limit: 100,
        targetDate: Coin.feedState.feedTradingDate,
        beforeCreatedAt: Coin.feedState.feedCursor.before_created_at,
        beforeId: Coin.feedState.feedCursor.before_id,
      }));
      var feed = json.data || {};
      var page = Array.isArray(feed.items) ? [].concat(feed.items).reverse() : [];
      Coin.rememberFeedActivities(page, { prepend: true });
      Coin.feedState.feedTradingDate = feed.resolved_trading_date || Coin.feedState.feedTradingDate;
      Coin.feedState.feedHasMore = !!feed.has_more;
      Coin.feedState.feedCursor = feed.next_cursor || null;
      Coin.renderLiveFeedFromState({
        preserveViewport: true,
        previousScrollHeight: previousScrollHeight,
        previousScrollTop: previousScrollTop,
      });
    } catch (err) {
      Admin.Toast.show('이전 내역 로드 실패: ' + err.message, 'error');
    } finally {
      Coin.feedState.feedLoading = false;
      Coin.updateFeedLoadMore();
    }
  };

  // -- Feed URL builder --

  Coin.buildActivityFeedUrl = function (options) {
    if (!options) options = {};
    var params = new URLSearchParams();
    params.set('limit', String(options.limit || 100));
    if (options.targetDate) params.set('target_date', options.targetDate);
    if (options.beforeCreatedAt) params.set('before_created_at', options.beforeCreatedAt);
    if (options.beforeId) params.set('before_id', options.beforeId);
    return Admin.API + '/activities/feed?' + params.toString();
  };

  // -- Feed load more bar --

  Coin.updateFeedLoadMore = function () {
    var bar = document.getElementById('feed-load-more-bar');
    var btn = document.getElementById('feed-load-more-btn');
    var meta = document.getElementById('feed-load-more-meta');
    if (!bar || !btn || !meta) return;

    var label = btn.querySelector('span');
    if (Coin.currentView !== 'live') {
      bar.classList.add('hidden');
      btn.disabled = false;
      if (label) label.textContent = '이전 내역 더보기';
      meta.textContent = '';
      return;
    }

    var canShow = Coin.feedState.loaded && (Coin.feedState.feedHasMore || Coin.feedState.feedLoading) && Coin.isNearTop;
    if (!canShow) {
      bar.classList.add('hidden');
      btn.disabled = false;
      if (label) label.textContent = '이전 내역 더보기';
    } else {
      bar.classList.remove('hidden');
      btn.disabled = Coin.feedState.feedLoading;
      if (label) label.textContent = Coin.feedState.feedLoading ? '불러오는 중...' : '이전 내역 더보기';
    }

    meta.textContent = Coin.feedState.feedTradingDate ? Coin.feedState.feedTradingDate + ' 거래일' : '';
  };

  // -- Render live feed from state --

  Coin.renderLiveFeedFromState = function (options) {
    if (!options) options = {};
    var restoreScroll = options.restoreScroll || false;
    var preserveViewport = options.preserveViewport || false;
    var previousScrollHeight = options.previousScrollHeight || 0;
    var previousScrollTop = options.previousScrollTop || 0;

    if (Coin.currentView !== 'live') return;
    var container = document.getElementById('chat-container');
    if (!container) return;

    Coin.cleanupStockCards();
    container.innerHTML = '';
    Coin.setActivityCount(0);

    var visibleActivities = Admin.filterResolvedStarts(Coin.feedState.feedItems);
    if (!visibleActivities.length) {
      Admin.renderPlaceholder(container, 'empty', '현재 거래일 활동이 없습니다');
    } else {
      visibleActivities.forEach(function (activity) {
        Admin.Feed.appendActivity(activity, { skipStore: true });
      });
    }

    Coin.feedState.loaded = true;
    Coin.feedState.activityCount = visibleActivities.length;
    var countEl = document.getElementById('activity-count');
    if (countEl) countEl.textContent = Coin.getActivityCount() + '건';
    Coin.updateFeedLoadMore();
    Admin.refreshIcons();

    requestAnimationFrame(function () {
      if (preserveViewport) {
        var nextHeight = container.scrollHeight;
        container.scrollTop = previousScrollTop + (nextHeight - previousScrollHeight);
        return;
      }
      if (restoreScroll) {
        container.scrollTop = Coin.feedState.scrollPos || 0;
        return;
      }
      container.scrollTop = container.scrollHeight;
    });
  };

  // -- Scroll management --

  Coin.setupFeedScroll = function () {
    var container = document.getElementById('chat-container');
    if (!container) return;

    container.addEventListener('scroll', function () {
      Coin.feedState.scrollPos = container.scrollTop;
      Coin.isNearTop = container.scrollTop < 64;
      var distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      if (distFromBottom < 40) {
        Coin.autoScroll = true;
        Coin.missedCount = 0;
        Coin.updateScrollFab();
      } else {
        Coin.autoScroll = false;
      }
      Coin.updateFeedLoadMore();
    });
  };

  Coin.updateScrollFab = function () {
    var fab = document.getElementById('scroll-to-bottom');
    var badge = document.getElementById('scroll-fab-badge');
    if (!fab || !badge) return;
    if (Coin.missedCount > 0) {
      badge.textContent = Coin.missedCount > 99 ? '99+' : String(Coin.missedCount);
      badge.classList.add('visible');
      fab.classList.remove('hidden');
    } else {
      badge.classList.remove('visible');
      fab.classList.add('hidden');
    }
  };

  // -- Timer cleanup --

  Coin.cleanupStockCards = function () {
    Object.values(Coin.feedState.stockCards).forEach(function (card) {
      if (card.liveTimer) clearInterval(card.liveTimer);
    });
    Coin.feedState.stockCards = {};
  };

  // -- Sidebar / section toggles --

  Coin.toggleLeftSidebar = function () {
    var sb = document.getElementById('left-sidebar');
    if (sb) sb.classList.toggle('collapsed');
  };

  Coin.toggleRightSidebar = function () {
    var sb = document.getElementById('right-sidebar');
    if (sb) sb.classList.toggle('collapsed');
  };

  Coin.toggleSection = function (name) {
    var arrow = document.getElementById(name + '-arrow');
    var body = document.getElementById(name + '-list');
    if (!body) return;
    body.classList.toggle('collapsed-section');
    if (arrow) arrow.classList.toggle('collapsed-icon');
  };

  Coin.toggleSettings = function () {
    var body = document.getElementById('settings-body');
    var arrow = document.getElementById('settings-arrow');
    if (!body) return;
    var hidden = body.classList.toggle('hidden');
    if (arrow) arrow.style.transform = hidden ? 'rotate(-90deg)' : 'rotate(0deg)';
  };

  // -- Navigation state --

  Coin.updateNavigationState = function () {
    var navLive = document.getElementById('nav-live');
    var navLatest = document.getElementById('nav-latest-report');
    var baseClass = 'w-full text-left px-3 py-2 rounded-lg text-sm text-gray-400 hover:bg-dark-700 flex items-center gap-2 transition';
    var activeClass = 'w-full text-left px-3 py-2 rounded-lg text-sm font-medium bg-blue-900/30 text-blue-300 flex items-center gap-2';

    if (navLive) navLive.className = Coin.currentView === 'live' ? activeClass : baseClass;
    if (navLatest) {
      navLatest.className = (Coin.currentView === 'report' && Coin.currentReportSelection.mode === 'latest')
        ? activeClass
        : baseClass;
    }

    document.querySelectorAll('#report-list [data-report-id]').forEach(function (item) {
      var isActive = Coin.currentView === 'report'
        && Coin.currentReportSelection.mode === 'id'
        && item.dataset.reportId === Coin.currentReportSelection.id;
      item.className = isActive
        ? 'w-full text-left px-3 py-2 rounded-lg text-xs bg-coin-purple/20 border border-coin-purple/30 text-coin-purple'
        : 'w-full text-left px-3 py-2 rounded-lg text-xs text-gray-400 hover:bg-dark-700 transition';
    });
  };

  Coin.updateMainPanelChrome = function () {
    var titleEl = document.getElementById('main-panel-title');
    var filterEl = document.getElementById('feed-filter-track');
    var clearBtn = document.getElementById('btn-main-clear');
    var clearIcon = document.getElementById('main-clear-icon');
    var clearLabel = document.getElementById('main-clear-label');
    var refreshBtn = document.getElementById('btn-main-refresh');
    var refreshIcon = document.getElementById('main-refresh-icon');
    var refreshLabel = document.getElementById('main-refresh-label');

    if (titleEl) {
      titleEl.innerHTML = Coin.currentView === 'live'
        ? '<i data-lucide="activity" class="w-3.5 h-3.5 text-coin-purple"></i> Activity Feed'
        : '<i data-lucide="clipboard-list" class="w-3.5 h-3.5 text-coin-purple"></i> Coin Reports';
    }
    if (filterEl) filterEl.classList.toggle('hidden', Coin.currentView !== 'live');

    if (clearBtn && clearIcon && clearLabel) {
      clearBtn.title = Coin.currentView === 'live' ? '화면 비우기' : '실시간 모니터링으로 복귀';
      clearBtn.setAttribute('aria-label', clearBtn.title);
      clearIcon.setAttribute('data-lucide', Coin.currentView === 'live' ? 'trash-2' : 'activity');
      clearLabel.textContent = Coin.currentView === 'live' ? '클리어' : '라이브 복귀';
    }
    if (refreshBtn && refreshIcon && refreshLabel) {
      refreshBtn.title = Coin.currentView === 'live' ? '새로고침' : '현재 리포트 다시 불러오기';
      refreshBtn.setAttribute('aria-label', refreshBtn.title);
      refreshIcon.setAttribute('data-lucide', 'refresh-cw');
      refreshLabel.textContent = Coin.currentView === 'live' ? '새로고침' : '리포트 새로고침';
    }

    Admin.refreshIcons();
  };

  // -- Scan timeline --

  Coin.updateScanTimeline = function () {
    var progressBar = document.getElementById('scan-progress-fill');
    var countdown = document.getElementById('scan-countdown');
    var lastTimeEl = document.getElementById('scan-last-time');
    var nextTimeEl = document.getElementById('scan-next-time');
    if (!progressBar && !countdown) return;

    if (!Coin.lastCycleTime || !Coin.scanIntervalHours) {
      if (countdown) countdown.textContent = '스캔 정보 없음';
      if (progressBar) progressBar.style.width = '0%';
      if (lastTimeEl) lastTimeEl.textContent = '마지막: --';
      if (nextTimeEl) nextTimeEl.textContent = '다음: --';
      return;
    }

    var intervalMs = Coin.scanIntervalHours * 3600 * 1000;
    var nextScanTime = Coin.lastCycleTime + intervalMs;
    var now = Date.now();
    var elapsed = now - Coin.lastCycleTime;
    var remaining = Math.max(0, nextScanTime - now);
    var progress = Math.min(100, (elapsed / intervalMs) * 100);

    if (progressBar) {
      progressBar.style.width = progress + '%';
      if (progress < 50) {
        progressBar.style.background = '#34d399';
      } else if (progress < 80) {
        progressBar.style.background = '#fbbf24';
      } else {
        progressBar.style.background = '#f87171';
      }
    }

    if (countdown) {
      if (remaining <= 0) {
        countdown.textContent = '스캔 예정 시간 경과 (곧 실행)';
        countdown.style.color = '#f87171';
      } else {
        countdown.textContent = '다음 스캔 ' + Coin.formatDuration(remaining / 1000) + ' 후';
        countdown.style.color = '#d1d5db';
      }
    }

    if (lastTimeEl) lastTimeEl.textContent = '마지막: ' + Coin.formatDateTime(Coin.lastCycleTime);
    if (nextTimeEl) nextTimeEl.textContent = '다음: ' + Coin.formatDateTime(nextScanTime);
  };

  Coin.startScanCountdown = function () {
    if (Coin.scanCountdownTimer) clearInterval(Coin.scanCountdownTimer);
    Coin.scanCountdownTimer = setInterval(function () {
      Coin.updateScanTimeline();
      Coin.updateRecCountdowns();
    }, 1000);
  };

  // -- Metric helpers --

  Coin.scheduleAccountOverviewRefresh = function (delay) {
    if (delay == null) delay = 250;
    if (Coin.accountRefreshTimer) clearTimeout(Coin.accountRefreshTimer);
    Coin.accountRefreshTimer = setTimeout(function () {
      Coin.accountRefreshTimer = null;
      Coin.loadBalance();
    }, delay);
  };

  Coin.flashMetricDelta = function (deltaId, deltaValue) {
    var el = document.getElementById(deltaId);
    if (!el) return;

    var absDelta = Math.abs(Number(deltaValue || 0));
    if (absDelta < 1) {
      el.textContent = '';
      el.className = 'metric-delta';
      return;
    }

    var isPositive = deltaValue > 0;
    el.textContent = (isPositive ? '+' : '-') + Admin.formatKRW(absDelta);
    el.className = 'metric-delta show ' + (isPositive ? 'positive' : 'negative');

    if (Coin.metricDeltaTimers[deltaId]) clearTimeout(Coin.metricDeltaTimers[deltaId]);
    Coin.metricDeltaTimers[deltaId] = setTimeout(function () {
      el.className = 'metric-delta';
      el.textContent = '';
      delete Coin.metricDeltaTimers[deltaId];
    }, 3000);
  };

  Coin.updateMetricValue = function (id, deltaId, numericValue, formattedValue, threshold) {
    if (threshold == null) threshold = 1;
    var el = document.getElementById(id);
    if (!el) return;

    var prevValue = Number(el.dataset.value);
    el.textContent = formattedValue;
    el.dataset.value = String(numericValue);
    if (!Number.isNaN(prevValue) && Math.abs(numericValue - prevValue) >= threshold) {
      Coin.flashMetricDelta(deltaId, numericValue - prevValue);
    }
  };

  // ================================================================
  // -- Global window exports (onclick handlers from coin.html) --
  // ================================================================

  window.switchView = function (v) { Admin.Coin.switchView(v); };
  window.toggleLeftSidebar = function () { Admin.Coin.toggleLeftSidebar(); };
  window.toggleRightSidebar = function () { Admin.Coin.toggleRightSidebar(); };
  window.toggleSection = function (n) { Admin.Coin.toggleSection(n); };
  window.toggleSettings = function () { Admin.Coin.toggleSettings(); };
  window.updateSetting = function (k, v) { Admin.Coin.updateSetting(k, v); };
  window.setLogFilter = function (f) { Admin.Coin.setLogFilter(f); };
  window.toggleMonitorExpand = function () { Admin.Monitor.toggleExpand(); };
  window.loadMoreActivities = function () { Admin.Coin.loadMoreActivities(); };
  window.scrollToBottom = function () { Admin.Feed.scrollToBottom(); };
  window.handleMainClear = function () { Admin.Coin.handleMainClear(); };
  window.refreshCurrentView = function () { Admin.Coin.refreshCurrentView(); };
  window.toggleDetail = function (id) { Admin.Feed.toggleDetail(id); };
  window.approveRecommendation = function (id) { Admin.Coin.approveRecommendation(id); };
  window.rejectRecommendation = function (id) { Admin.Coin.rejectRecommendation(id); };
  window.switchToReport = function (id) { Admin.Coin.switchToReport(id); };

  // ================================================================
  // -- DOMContentLoaded --
  // ================================================================

  document.addEventListener('DOMContentLoaded', function () {
    // Setup
    Coin.setupFeedScroll();
    Coin.renderActionButtons();
    Coin.updateNavigationState();
    Coin.updateMainPanelChrome();
    var fab = document.getElementById('scroll-to-bottom');
    if (fab) fab.addEventListener('click', function () { Admin.Feed.scrollToBottom(); });
    var scanBtn = document.getElementById('btn-trigger-scan');
    if (scanBtn) scanBtn.addEventListener('click', Coin.triggerScan);
    var reportBtn = document.getElementById('btn-generate-report');
    if (reportBtn) reportBtn.addEventListener('click', Coin.generateManualReport);

    // SSE
    Coin.connectSSE();

    // Initial loads
    Coin.loadSystemStatus();
    Coin.loadAgentState({ quiet: true }).then(function () { Admin.Monitor.render(); });
    Coin.loadBalance();
    Coin.loadRecommendations();
    Coin.loadWatchlist();
    Coin.loadSettings();
    Coin.loadTodayActivities();
    Coin.loadLatestReport();
    Coin.loadReportList();

    // Polling intervals
    setInterval(Coin.loadBalance, 30000);           // 30s
    setInterval(Coin.loadRecommendations, 10000);   // 10s
    setInterval(Coin.loadSystemStatus, 60000);      // 60s
    setInterval(Coin.loadAgentState, 15000);        // 15s
    setInterval(Coin.loadWatchlist, 120000);         // 2min
    setInterval(Coin.loadLatestReport, 300000);      // 5min
    setInterval(Coin.loadReportList, 300000);        // 5min

    // Countdown ticker (scan timeline + recommendation expiry)
    Coin.startScanCountdown();
  });
})();
