/**
 * coin-reports.js -- Report management
 *
 * Latest report, report list, report detail view, report period
 * activities, and settings panel.
 *
 * Security note: all dynamic content is escaped via Admin.escapeHtml()
 * before DOM insertion. This mirrors the original coin-app.js exactly.
 */
(function () {
  'use strict';

  var Coin = Admin.Coin;

  // -- Load latest report --

  Coin.loadLatestReport = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/reports/latest');
      Coin.latestReportCache = json.data || null;
      if (Coin.latestReportCache && Coin.latestReportCache.id) {
        Coin.reportCacheById.set(Coin.latestReportCache.id, Coin.latestReportCache);
      }
      Coin.renderLatestReport(Coin.latestReportCache);
    } catch (err) {
      console.error('[coin] latest report error:', err);
      Coin.latestReportCache = null;
      Coin.renderLatestReport(null);
    }
  };

  // -- Render latest report sidebar --

  Coin.renderLatestReport = function (report) {
    var container = document.getElementById('latest-report');
    if (!container) return;
    if (!report) {
      container.innerHTML = '<div class="text-gray-500 text-xs py-1">최근 리포트 없음</div>';
      return;
    }

    var pnl = Number(report.total_pnl ?? 0);
    var pnlColor = pnl >= 0 ? '#34d399' : '#f87171';
    var summary = Coin.truncateText(report.market_summary || report.performance_review || report.lessons_learned || '요약 없음', 120);
    var reportSource = Coin.getReportSourceLabel(report.report_source);
    var reportTs = Coin.resolveReportTimestamp(report);
    var reportTsText = reportTs ? Coin.formatDateTime(reportTs) : '--';

    container.innerHTML = `
    <div class="space-y-2">
      <div class="flex items-center justify-between gap-2">
        <span class="text-gray-300">${Admin.escapeHtml(report.report_date || '--')}</span>
        <span style="color:${pnlColor};">${Admin.escapeHtml(Admin.formatKRW(pnl))}</span>
      </div>
      <div class="flex items-center justify-between gap-2 text-[10px]">
        <span class="text-blue-300">${Admin.escapeHtml(reportSource)}</span>
        <span class="text-gray-500">${Admin.escapeHtml(reportTsText)}</span>
      </div>
      <div class="text-[11px] text-gray-500 whitespace-pre-wrap">${Admin.escapeHtml(summary)}</div>
      <button type="button" onclick="switchView('latest-report')"
        class="w-full text-left text-[11px] text-coin-purple hover:text-blue-300 transition">
        최신 리포트 상세 보기
      </button>
    </div>
    `;
  };

  // -- Load report list --

  Coin.loadReportList = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/reports?limit=30');
      Coin.setReportCaches(json.data || []);
      Coin.renderReportList();
    } catch (err) {
      console.error('[coin] report list error:', err);
      var listEl = document.getElementById('report-list');
      if (listEl) listEl.innerHTML = '<div class="px-3 text-xs text-red-400">리포트 목록 조회 실패</div>';
    }
  };

  // -- Render report list sidebar --

  Coin.renderReportList = function () {
    var listEl = document.getElementById('report-list');
    if (!listEl) return;

    if (!Coin.reportListCache.length) {
      listEl.innerHTML = '<div class="px-3 text-xs text-gray-500">리포트 없음</div>';
      return;
    }

    listEl.innerHTML = Coin.reportListCache.map(function (report) {
      var reportId = String(report.id || '');
      var ts = Coin.resolveReportTimestamp(report);
      var title = ts ? Coin.formatDateTime(ts).replace(/\.\s*/g, '/') : String(report.report_date || '--');
      var source = Coin.getReportSourceLabel(report.report_source);
      var pnl = Coin.formatSignedKRW(report.total_pnl);
      var active = Coin.currentView === 'report'
        && Coin.currentReportSelection.mode === 'id'
        && Coin.currentReportSelection.id === reportId;
      var classes = active
        ? 'w-full text-left px-3 py-2 rounded-lg text-xs bg-coin-purple/20 border border-coin-purple/30 text-coin-purple'
        : 'w-full text-left px-3 py-2 rounded-lg text-xs text-gray-400 hover:bg-dark-700 transition';

      return `
      <button type="button" data-report-id="${Admin.escapeHtml(reportId)}"
        onclick="switchToReport('${Admin.escapeHtml(reportId)}')" class="${classes}">
        <div class="flex items-center justify-between gap-2">
          <span class="truncate">${Admin.escapeHtml(title)}</span>
          <span class="text-[10px] ${Number(report.total_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}">${Admin.escapeHtml(pnl)}</span>
        </div>
        <div class="mt-1 flex items-center justify-between gap-2 text-[10px]">
          <span class="text-blue-300">${Admin.escapeHtml(source)}</span>
          <span class="text-gray-500">${Admin.escapeHtml(String(report.market_regime || ''))}</span>
        </div>
      </button>
      `;
    }).join('');

    Coin.updateNavigationState();
  };

  // -- Load and render selected report --

  Coin.loadCurrentReportView = async function (options) {
    var forceLatest = options && options.forceLatest;
    var container = document.getElementById('chat-container');
    if (!container) return;

    Coin.cleanupStockCards();
    Coin.setActivityCount(0);
    var countEl = document.getElementById('activity-count');
    if (countEl) countEl.textContent = '0건';
    Admin.renderPlaceholder(container, 'loading', '리포트 불러오는 중...');

    try {
      var report = null;
      if (Coin.currentReportSelection.mode === 'latest') {
        if (forceLatest || !Coin.latestReportCache) {
          await Coin.loadLatestReport();
        }
        report = Coin.latestReportCache || Coin.reportListCache[0] || null;
      } else if (Coin.currentReportSelection.mode === 'id') {
        report = Coin.getSelectedCachedReport();
        if (!report) {
          await Coin.loadReportList();
          report = Coin.getSelectedCachedReport();
        }
      }

      if (!report) {
        Admin.renderPlaceholder(container, 'empty', '표시할 코인 리포트가 없습니다');
        Coin.setStatusText('코인 리포트가 없습니다', '#9ca3af');
        Coin.updateNavigationState();
        return;
      }

      if (report.id) {
        Coin.reportCacheById.set(report.id, report);
      }
      container.innerHTML = '';
      container.appendChild(Coin.createReportCard(report));
      await Coin.loadReportActivities(report, container);
      Coin.setStatusText('코인 리포트를 불러왔습니다', '#93c5fd');
      Coin.updateNavigationState();
      Admin.refreshIcons();
    } catch (err) {
      console.error('[coin] report view error:', err);
      Admin.renderPlaceholder(container, 'error', '리포트 로드 실패: ' + err.message);
      Coin.setStatusText('리포트 로드 실패 · ' + Coin.truncateText(err.message, 80), '#f87171');
    }
  };

  // -- Create comprehensive report card --

  Coin.createReportCard = function (report) {
    var div = document.createElement('div');
    div.className = 'bg-dark-700 rounded-xl p-5 border border-[#3d3350] mx-2 chat-bubble animate-fade-in';

    var stats = Coin.safeJsonParse(report.strategy_stats, {}) || {};
    var tradeEval = stats.trade_evaluation || {};
    var feedback = stats.feedback_for_next_cycle || {};
    var successPatterns = Array.isArray(stats.success_patterns) ? stats.success_patterns : [];
    var failurePatterns = Array.isArray(stats.failure_patterns) ? stats.failure_patterns : [];
    var riskAlerts = Array.isArray(stats.risk_alerts) ? stats.risk_alerts : [];
    var topPicksRaw = Coin.safeJsonParse(report.top_picks, []) || [];
    var topPicks = Array.isArray(topPicksRaw)
      ? topPicksRaw.map(function (item) {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object') return item.name || item.symbol || '';
          return '';
        }).filter(Boolean)
      : [];

    var reportSource = Coin.getReportSourceLabel(report.report_source);
    var periodStarted = report.period_started_at ? Coin.formatDateTime(report.period_started_at) : '--';
    var periodEnded = report.period_ended_at ? Coin.formatDateTime(report.period_ended_at) : '--';
    var totalPnl = Number(report.total_pnl ?? 0);
    var unrealizedPnl = Number(report.unrealized_pnl ?? 0);
    var realizedPnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
    var unrealizedPnlColor = unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400';
    var closedTrades = Number(report.win_count || 0) + Number(report.loss_count || 0);
    var winRate = closedTrades > 0 ? ((Number(report.win_count || 0) / closedTrades) * 100).toFixed(1) + '%' : '-';
    var btcDominance = report.btc_dominance != null ? Number(report.btc_dominance).toFixed(2) + '%' : '--';

    var riskHtml = riskAlerts.length
      ? `<div class="mt-3">
          <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">
            <i data-lucide="shield-alert" class="w-4 h-4"></i> 리스크 알림
          </div>
          <div class="space-y-1">
            ${riskAlerts.map(function (item) { return '<div class="text-xs text-amber-200 bg-amber-900/20 border border-amber-400/10 rounded px-2 py-1">' + Admin.escapeHtml(String(item)) + '</div>'; }).join('')}
          </div>
        </div>`
      : '';

    var patternHtml = (successPatterns.length || failurePatterns.length)
      ? `<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
          <div class="bg-dark-900 rounded-lg p-3">
            <div class="text-sm text-gray-300 mb-2 flex items-center gap-1.5">
              <i data-lucide="trending-up" class="w-4 h-4"></i> 성공 패턴
            </div>
            <div class="space-y-1">${successPatterns.length
              ? successPatterns.map(function (item) { return '<div class="text-xs text-green-300">' + Admin.escapeHtml(String(item)) + '</div>'; }).join('')
              : '<div class="text-xs text-gray-500">기록 없음</div>'}
            </div>
          </div>
          <div class="bg-dark-900 rounded-lg p-3">
            <div class="text-sm text-gray-300 mb-2 flex items-center gap-1.5">
              <i data-lucide="trending-down" class="w-4 h-4"></i> 실패 패턴
            </div>
            <div class="space-y-1">${failurePatterns.length
              ? failurePatterns.map(function (item) { return '<div class="text-xs text-red-300">' + Admin.escapeHtml(String(item)) + '</div>'; }).join('')
              : '<div class="text-xs text-gray-500">기록 없음</div>'}
            </div>
          </div>
        </div>`
      : '';

    var feedbackEntries = Object.entries(feedback).filter(function (entry) { return entry[1]; });
    var feedbackHtml = feedbackEntries.length
      ? `<div class="mt-3">
          <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">
            <i data-lucide="target" class="w-4 h-4"></i> 다음 사이클 피드백
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
            ${feedbackEntries.map(function (entry) {
              return `
              <div class="bg-dark-900 rounded p-2">
                <div class="text-[11px] text-gray-500 mb-1">${Admin.escapeHtml(String(entry[0]))}</div>
                <div class="text-xs text-gray-300 whitespace-pre-wrap">${Admin.escapeHtml(String(entry[1]))}</div>
              </div>
              `;
            }).join('')}
          </div>
        </div>`
      : '';

    div.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
      <div>
        <div class="flex items-center gap-2 text-lg font-bold text-white">
          <i data-lucide="clipboard-list" class="w-5 h-5 text-coin-purple"></i>
          코인 체크포인트 리포트
        </div>
        <div class="mt-1 text-xs text-gray-500">
          ${Admin.escapeHtml(periodStarted)} ~ ${Admin.escapeHtml(periodEnded)}
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <span class="px-2 py-1 rounded-full bg-blue-900/30 text-blue-300">${Admin.escapeHtml(reportSource)}</span>
        <span class="px-2 py-1 rounded-full bg-dark-900 text-gray-300">${Admin.escapeHtml(String(report.market_regime || 'UNKNOWN'))}</span>
        <span class="px-2 py-1 rounded-full bg-dark-900 text-gray-500">${Admin.escapeHtml(String(report.report_date || '--'))}</span>
      </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold text-blue-300">${Number(report.total_cycles || 0)}</div>
        <div class="text-xs text-gray-500">완료 사이클</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold text-purple-300">${Number(report.total_analyses || 0)}</div>
        <div class="text-xs text-gray-500">분석 완료</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold text-yellow-300">${Number(report.buy_count || 0)} / ${Number(report.sell_count || 0)}</div>
        <div class="text-xs text-gray-500">진입 / 청산</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold text-coin-gold">${Admin.escapeHtml(btcDominance)}</div>
        <div class="text-xs text-gray-500">BTC 비중</div>
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold ${realizedPnlColor}">${Admin.escapeHtml(Coin.formatSignedKRW(totalPnl))}</div>
        <div class="text-xs text-gray-500">실현 손익 · 승률 ${Admin.escapeHtml(winRate)}</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3 text-center">
        <div class="text-xl font-bold ${unrealizedPnlColor}">${Admin.escapeHtml(Coin.formatSignedKRW(unrealizedPnl))}</div>
        <div class="text-xs text-gray-500">미실현 손익 · 보유 ${Number(report.open_position_count || 0)}개</div>
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
      <div class="bg-dark-900 rounded-lg p-3">
        <div class="text-xs text-gray-500 mb-1">전체 24h 거래대금</div>
        <div class="text-sm font-medium text-gray-200">${Admin.escapeHtml(Admin.formatKRW(report.total_24h_volume || 0))}</div>
      </div>
      <div class="bg-dark-900 rounded-lg p-3">
        <div class="text-xs text-gray-500 mb-1">거래 평가</div>
        <div class="text-sm font-medium text-gray-200">
          총 ${Number(tradeEval.total_trades || 0)}건 / 수익 ${Number(tradeEval.profitable_trades || 0)}건 / 손실 ${Number(tradeEval.loss_trades || 0)}건
        </div>
      </div>
    </div>

    ${report.market_summary ? `
      <div class="mb-3">
        <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">
          <i data-lucide="waves" class="w-4 h-4"></i> 시장 요약
        </div>
        <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${Admin.escapeHtml(report.market_summary)}</div>
      </div>` : ''}

    ${report.performance_review ? `
      <div class="mb-3">
        <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">
          <i data-lucide="pie-chart" class="w-4 h-4"></i> 성과 평가
        </div>
        <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${Admin.escapeHtml(report.performance_review)}</div>
      </div>` : ''}

    ${report.lessons_learned ? `
      <div class="mb-3">
        <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">
          <i data-lucide="book-open" class="w-4 h-4"></i> 학습 포인트
        </div>
        <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${Admin.escapeHtml(report.lessons_learned)}</div>
      </div>` : ''}

    ${report.next_day_plan ? `
      <div class="mb-3">
        <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">
          <i data-lucide="compass" class="w-4 h-4"></i> 다음 사이클 계획
        </div>
        <div class="text-sm text-gray-400 bg-dark-900 rounded p-3 whitespace-pre-wrap">${Admin.escapeHtml(report.next_day_plan)}</div>
      </div>` : ''}

    ${topPicks.length ? `
      <div class="mb-3">
        <div class="flex items-center gap-1.5 text-sm font-medium text-gray-300 mb-1">
          <i data-lucide="crosshair" class="w-4 h-4"></i> 관심 코인
        </div>
        <div class="flex flex-wrap gap-2">
          ${topPicks.map(function (item) { return '<span class="px-2 py-1 rounded-full bg-coin-purple/20 text-coin-purple text-xs">' + Admin.escapeHtml(String(item)) + '</span>'; }).join('')}
        </div>
      </div>` : ''}

    ${feedbackHtml}
    ${patternHtml}
    ${riskHtml}
    `;

    return div;
  };

  // -- Load report period activities --

  Coin.loadReportActivities = async function (report, container) {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/reports/' + encodeURIComponent(report.id) + '/activities?limit=500');
      var items = Array.isArray(json.data) ? json.data : [];
      var countEl = document.getElementById('activity-count');
      if (countEl) countEl.textContent = items.length + '건';

      var section = document.createElement('div');
      section.className = 'mt-4 border-t border-[#3d3350]';

      var toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'w-full text-center text-gray-500 hover:text-gray-300 text-xs py-3 flex items-center justify-center gap-2 transition';
      toggleBtn.innerHTML = `
        <span class="activity-toggle-icon"><i data-lucide="chevron-right" class="w-3 h-3 inline-block"></i></span>
        리포트 구간 활동 로그 (${items.length}건)
      `;

      var logContainer = document.createElement('div');
      logContainer.className = 'hidden';
      logContainer.style.maxHeight = '600px';
      logContainer.style.overflowY = 'auto';

      if (items.length) {
        [].concat(items).reverse().forEach(function (activity) {
          logContainer.appendChild(Admin.Feed.createBubble
            ? Admin.Feed.createBubble(activity)
            : _createSimpleBubble(activity));
        });
      } else {
        logContainer.innerHTML = '<div class="text-center text-gray-500 text-xs py-4">해당 리포트 구간의 활동 로그가 없습니다</div>';
      }

      toggleBtn.onclick = function () {
        var isHidden = logContainer.classList.contains('hidden');
        logContainer.classList.toggle('hidden');
        var iconEl = toggleBtn.querySelector('.activity-toggle-icon');
        if (iconEl) {
          iconEl.innerHTML = isHidden
            ? '<i data-lucide="chevron-down" class="w-3 h-3 inline-block"></i>'
            : '<i data-lucide="chevron-right" class="w-3 h-3 inline-block"></i>';
        }
        Admin.refreshIcons();
      };

      section.appendChild(toggleBtn);
      section.appendChild(logContainer);
      container.appendChild(section);
      Admin.refreshIcons();
    } catch (err) {
      console.error('[coin] report activities error:', err);
      var error = document.createElement('div');
      error.className = 'mx-2 mt-4 text-xs text-red-400';
      error.textContent = '리포트 구간 활동 로그 로드 실패: ' + err.message;
      container.appendChild(error);
    }
  };

  // Fallback bubble creator if Feed doesn't expose createBubble
  function _createSimpleBubble(data) {
    var div = document.createElement('div');
    div.className = 'chat-bubble';
    var time = Admin.formatTime(data.created_at);
    div.innerHTML = `
      <div class="flex items-start gap-2 px-3 py-1.5 rounded-lg hover:bg-dark-700/50 transition group">
        <span class="text-xs text-gray-500 mt-0.5 shrink-0 w-14">${time}</span>
        <div class="flex-1 min-w-0">
          <div class="text-sm whitespace-pre-wrap">${Admin.escapeHtml(Admin.formatAdminAgentText(data.summary))}</div>
        </div>
      </div>
    `;
    return div;
  }

  // -- Settings --

  Coin.loadSettings = async function () {
    try {
      var json = await Admin.fetchJSON(Admin.API + '/settings');
      var data = json.data;
      if (!data) return;
      Coin.renderSettings(data);
    } catch (err) {
      console.error('[coin] settings load error:', err);
    }
  };

  Coin.renderSettings = function (settings) {
    var el = function (id) { return document.getElementById(id); };
    if (settings.CRYPTO_TRADING_ENABLED != null && el('set-trading')) el('set-trading').checked = !!settings.CRYPTO_TRADING_ENABLED;
    if (settings.CRYPTO_ENABLED != null && el('set-auto-scan')) el('set-auto-scan').checked = !!settings.CRYPTO_ENABLED;
    if (settings.CRYPTO_AUTONOMY_MODE && el('set-mode')) el('set-mode').value = settings.CRYPTO_AUTONOMY_MODE;
    if (settings.CRYPTO_SCAN_INTERVAL_HOURS != null && el('set-risk')) el('set-risk').value = String(settings.CRYPTO_SCAN_INTERVAL_HOURS);
  };

  Coin.updateSetting = async function (key, value) {
    try {
      await Admin.fetchJSON(Admin.API + '/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      });
      Coin.loadSettings();
      Coin.loadSystemStatus();
    } catch (err) {
      console.error('[coin] setting update error:', err);
    }
  };
})();
