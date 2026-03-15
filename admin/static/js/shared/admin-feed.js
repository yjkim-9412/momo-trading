// ── admin-feed.js — Activity feed: cards, bubbles, dividers, scroll ──
import {
  escapeHtml, formatTime, formatAdminAgentText, transformAdminAgentValue,
  getTypeColor, getProgressKey, refreshIcons, getActivityIdentity,

} from './admin-core.js';

let _config = {};

export function init(config) {
  _config = config || {};
}

// ────────────────────────────────────────────
// appendActivity
// ────────────────────────────────────────────
export function appendActivity(data, options) {
  options = options || {};
  var skipStore = options.skipStore || false;
  var container = _config.getChatContainer();
  if (!container) return;

  // Dedup + optional feed memory (unless replaying from stored state)
  if (!skipStore) {
    if (typeof _config.rememberActivity === 'function') {
      // App-level dedup + feedItems storage (e.g., multi-market feed replay)
      if (_config.rememberActivity(data) === false) return;
    } else {
      var ids = _config.getLoadedActivityIds();
      var identity = getActivityIdentity(data);
      if (ids.has(identity)) return;
      ids.add(identity);
    }
  }

  // Remove initial placeholder
  if (container.children.length === 1 && container.children[0].classList.contains('text-center')) {
    container.textContent = '';
  }

  var symbol = data.symbol;
  var isCycleActivity = data.activity_type === 'CYCLE';
  var isDailyPlan = data.activity_type === 'DAILY_PLAN';
  var isLLMCall = data.activity_type === 'LLM_CALL';

  if (!symbol || isCycleActivity || isDailyPlan) {
    if (isCycleActivity && data.phase === 'START') {
      container.appendChild(createCycleDivider(data, true));
    } else if (isCycleActivity && (data.phase === 'COMPLETE' || data.phase === 'ERROR')) {
      var startKey = 'cycle-start-' + data.cycle_id;
      var existing = container.querySelector('[data-cycle-start="' + startKey + '"]');
      if (existing) {
        var spinner = existing.querySelector('.progress-spinner');
        if (spinner) spinner.remove();
        var text = existing.querySelector('.cycle-text');
        if (text) text.textContent += ' \u2192 \uC644\uB8CC';
      }
      container.appendChild(createCycleDivider(data, false));
    } else if (isLLMCall && !symbol) {
      container.appendChild(createBubble(data));
    } else {
      container.appendChild(createBubble(data));
    }
  } else {
    // Symbol-specific -> route to stock card
    var cards = _config.getStockCards();
    var cardKey = (data.cycle_id || 'ev') + ':' + symbol;
    var card = cards[cardKey];

    // Fallback: find an existing in-progress card for same symbol
    if (!card) {
      var keys = Object.keys(cards);
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        if (k.endsWith(':' + symbol) && (!cards[k].outcome || cards[k].outcome === 'progress')) {
          card = cards[k];
          cards[cardKey] = card;
          break;
        }
      }
    }

    if (!card) {
      if (isLLMCall) return; // LLM_CALL without existing card -> skip
      card = createStockCard(symbol, data);
      cards[cardKey] = card;
      container.appendChild(card.element);
    }

    addStepToCard(card, data);
    updateCardHeader(card);
  }

  _config.incActivityCount();
  var countEl = document.getElementById('activity-count');
  if (countEl) countEl.textContent = _config.getActivityCount() + '\uAC74';

  if (!_config.getAutoScroll()) {
    _config.setMissedCount((_config.getMissedCount() || 0) + 1);
    updateScrollBadge();
  }

  if (_config.getAutoScroll()) {
    container.scrollTop = container.scrollHeight;
  }
  refreshIcons();

  if (_config.onActivityAppended) _config.onActivityAppended(data);
}

// ────────────────────────────────────────────
// createCycleDivider
// ────────────────────────────────────────────
export function createCycleDivider(data, isStart) {
  var div = document.createElement('div');
  div.className = isStart ? 'cycle-divider cycle-start' : 'cycle-divider cycle-end';
  var summary = formatAdminAgentText(data.summary);
  if (isStart) {
    div.setAttribute('data-cycle-start', 'cycle-start-' + data.cycle_id);
    var sp = document.createElement('span');
    sp.className = 'progress-spinner';
    div.appendChild(sp);
    var txt = document.createElement('span');
    txt.className = 'cycle-text';
    txt.textContent = summary;
    div.appendChild(txt);
  } else {
    var time = formatTime(data.created_at);
    var elapsed = data.execution_time_ms ? ' (' + (data.execution_time_ms / 1000).toFixed(1) + '\uCD08)' : '';
    var left = document.createElement('span');
    left.textContent = summary + elapsed;
    var right = document.createElement('span');
    right.className = 'text-gray-500';
    right.textContent = time;
    div.appendChild(left);
    div.appendChild(right);
  }
  return div;
}

// ────────────────────────────────────────────
// createStockCard
// ────────────────────────────────────────────
export function createStockCard(symbol, firstActivity) {
  var el = document.createElement('div');
  el.className = 'stock-card outcome-progress';

  var nameMatch = (firstActivity.summary || '').match(/\[([^\]]+)\]/);
  var stockName = nameMatch ? nameMatch[1] : symbol;
  if (/^TIER\d/i.test(stockName)) stockName = symbol;

  // Pick card icon from theme or default (apps set _config.cardTheme)
  var theme = _config.cardTheme || {};
  var cardIcon = theme.icon || 'bar-chart-2';
  var badgeClass = theme.progressBadge || 'bg-purple-900/40 text-purple-300';

  var header = document.createElement('div');
  header.className = 'stock-card-header';

  // Build header content with DOM
  var iconEl = document.createElement('i');
  iconEl.setAttribute('data-lucide', cardIcon);
  iconEl.className = 'w-4 h-4 text-gray-400 shrink-0';
  header.appendChild(iconEl);

  var nameSpan = document.createElement('span');
  nameSpan.className = 'text-sm font-medium text-white flex-1 truncate';
  nameSpan.appendChild(document.createTextNode(stockName + ' '));
  var symSpan = document.createElement('span');
  symSpan.className = 'text-gray-500 text-xs';
  symSpan.textContent = symbol;
  nameSpan.appendChild(symSpan);
  header.appendChild(nameSpan);

  // Product badge placeholder (stock only)
  var productBadge = document.createElement('span');
  productBadge.className = 'stock-product-badge';
  header.appendChild(productBadge);

  // Outcome badge
  var outcomeSpan = document.createElement('span');
  outcomeSpan.className = 'stock-outcome flex items-center gap-1 text-xs px-2 py-0.5 rounded ' + badgeClass;
  var progressSpinner = document.createElement('span');
  progressSpinner.className = 'progress-spinner';
  progressSpinner.style.cssText = 'width:10px;height:10px;border-width:1.5px;margin-right:2px';
  outcomeSpan.appendChild(progressSpinner);
  outcomeSpan.appendChild(document.createTextNode('\uBD84\uC11D \uC911'));
  header.appendChild(outcomeSpan);

  // Elapsed
  var elapsedSpan = document.createElement('span');
  elapsedSpan.className = 'stock-elapsed text-xs text-gray-400';
  header.appendChild(elapsedSpan);

  // Expand arrow
  var expandSpan = document.createElement('span');
  expandSpan.className = 'stock-expand text-gray-500 text-xs transition-transform';
  expandSpan.style.transform = 'rotate(-90deg)';
  var chevron = document.createElement('i');
  chevron.setAttribute('data-lucide', 'chevron-down');
  chevron.className = 'w-4 h-4';
  expandSpan.appendChild(chevron);
  header.appendChild(expandSpan);

  // Body
  var body = document.createElement('div');
  body.className = 'stock-card-body';

  var steps = document.createElement('div');
  steps.className = 'stock-card-steps';
  body.appendChild(steps);

  el.appendChild(header);
  el.appendChild(body);

  var card = {
    element: el,
    headerEl: header,
    bodyEl: body,
    stepsEl: steps,
    activities: [],
    symbol: symbol,
    stockName: stockName,
    outcome: null,
    productContext: null,
    confidence: null,
    totalElapsed: 0,
    isOpen: false,
    startTime: Date.now(),
    liveTimer: null,
  };

  header.onclick = function () { toggleCardBody(card); };

  card.liveTimer = setInterval(function () {
    if (card.outcome && card.outcome !== 'progress') {
      clearInterval(card.liveTimer);
      card.liveTimer = null;
      return;
    }
    var elapsed = ((Date.now() - card.startTime) / 1000).toFixed(0);
    var el = card.headerEl.querySelector('.stock-elapsed');
    if (el) el.textContent = elapsed + '\uCD08';
  }, 1000);

  return card;
}

// ────────────────────────────────────────────
// addStepToCard
// ────────────────────────────────────────────
export function addStepToCard(card, data) {
  card.activities.push(data);

  // Extract product_context (stock-only, first occurrence wins)
  if (!card.productContext && _config.extractProductContext) {
    var pc = _config.extractProductContext(data);
    if (pc) {
      card.productContext = pc;
      var badgeEl = card.headerEl.querySelector('.stock-product-badge');
      if (badgeEl && _config.renderProductBadge) {
        badgeEl.insertAdjacentHTML('beforeend', _config.renderProductBadge(pc));
      }
    }
  }

  var progressKey = getProgressKey(data);

  // START -> compact progress indicator
  if (data.phase === 'START') {
    var step = document.createElement('div');
    step.className = 'stock-step';
    step.setAttribute('data-progress-key', progressKey);
    var time = formatTime(data.created_at);
    var label = formatAdminAgentText((data.summary || '').replace(/\uC2DC\uC791$/, '').trim());

    var timeSpan = document.createElement('span');
    timeSpan.className = 'text-xs text-gray-500 shrink-0 w-14';
    timeSpan.textContent = time;
    step.appendChild(timeSpan);

    var spinner = document.createElement('span');
    spinner.className = 'progress-spinner';
    spinner.style.cssText = 'width:10px;height:10px;border-width:1.5px';
    step.appendChild(spinner);

    var labelSpan = document.createElement('span');
    labelSpan.className = 'text-xs text-gray-400';
    labelSpan.textContent = label + '...';
    step.appendChild(labelSpan);

    card.stepsEl.appendChild(step);
    return;
  }

  // COMPLETE/ERROR -> remove matching START spinner
  if (data.phase === 'COMPLETE' || data.phase === 'ERROR') {
    var existingStart = card.stepsEl.querySelector('[data-progress-key="' + progressKey + '"]');
    if (existingStart) existingStart.remove();
  }

  // Create step element
  var stepEl = document.createElement('div');
  stepEl.className = 'stock-step';
  var timeStr = formatTime(data.created_at);
  var typeColor = getTypeColor(data.activity_type);
  var elapsedStr = data.execution_time_ms ? (data.execution_time_ms / 1000).toFixed(1) + '\uCD08' : '';

  // Build step HTML using insertAdjacentHTML (admin-only page, all content is escaped)
  var html = '<span class="text-xs text-gray-500 shrink-0 w-14">' + timeStr + '</span>'
    + '<div class="flex-1 min-w-0">'
    + '<div class="text-xs">' + escapeHtml(formatAdminAgentText(data.summary)) + '</div>';

  // Meta line
  var meta = [];
  if (data.llm_provider) meta.push('<span class="text-' + typeColor + '-400">' + escapeHtml(data.llm_provider) + '</span>');
  if (elapsedStr) meta.push(elapsedStr);
  if (data.confidence != null) {
    var pct = Math.round(data.confidence * 100);
    var confColor = pct >= 70 ? 'text-green-400' : pct >= 40 ? 'text-yellow-400' : 'text-red-400';
    meta.push('<span class="' + confColor + ' font-medium">\uC2E0\uB8B0\uB3C4 ' + pct + '%</span>');
  }
  if (meta.length) {
    html += '<div class="text-xs text-gray-400 mt-0.5">' + meta.join(' \u00B7 ') + '</div>';
  }

  // Detail (expandable)
  if (data.detail) {
    var detailId = 'sd-' + Math.random().toString(36).slice(2, 8);
    var isLLMCall = data.activity_type === 'LLM_CALL';
    var productStripHtml = (_config.renderProductStrip && _config.extractProductContext)
      ? (_config.renderProductStrip(_config.extractProductContext(data)) || '')
      : '';
    html += '<button onclick="event.stopPropagation(); window.__feedToggleDetail(\'' + detailId + '\')" class="text-xs text-gray-500 hover:text-gray-300 mt-0.5 flex items-center gap-1">'
      + (isLLMCall ? '<i data-lucide="message-square" class="w-3 h-3"></i> LLM \uB300\uD654' : '<i data-lucide="chevron-down" class="w-3 h-3"></i> \uC0C1\uC138')
      + '</button>'
      + '<div id="' + detailId + '" class="detail-content mt-1 text-xs bg-dark-900/50 rounded p-2 text-gray-400">'
      + productStripHtml
      + (isLLMCall ? formatLLMConversation(data.detail) : '<div class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">' + formatDetail(data.detail, data.activity_type) + '</div>')
      + '</div>';
  }

  // Error
  if (data.error_message) {
    html += '<div class="text-xs text-red-400 mt-0.5">' + escapeHtml(formatAdminAgentText(data.error_message)) + '</div>';
  }

  html += '</div>';
  stepEl.insertAdjacentHTML('beforeend', html);
  card.stepsEl.appendChild(stepEl);
}

// ────────────────────────────────────────────
// updateCardHeader
// ────────────────────────────────────────────
export function updateCardHeader(card) {
  var outcome = 'progress';
  var outcomeText = '<span class="progress-spinner" style="width:10px;height:10px;border-width:1.5px;margin-right:2px"></span>\uBD84\uC11D \uC911';

  // Theme colors
  var theme = _config.cardTheme || {};
  var outcomeBg = theme.progressBadge || 'bg-purple-900/40 text-purple-300';
  var buyBg = theme.buyBadge || 'bg-red-900/40 text-red-300';
  var sellBg = theme.sellBadge || 'bg-blue-900/40 text-blue-300';
  var holdBg = theme.holdBadge || 'bg-gray-700/60 text-gray-400';
  var errorBg = theme.errorBadge || 'bg-yellow-900/40 text-yellow-300';
  var totalMs = 0;

  card.activities.forEach(function (a) {
    if (a.execution_time_ms) totalMs += a.execution_time_ms;

    // Error
    if (a.phase === 'ERROR' || a.error_message) {
      outcome = 'error';
      outcomeText = '<i data-lucide="x-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uC624\uB958';
      outcomeBg = errorBg;
    }

    // SKIP
    if (a.phase === 'SKIP' && outcome !== 'error') {
      outcome = 'hold';
      outcomeText = '<i data-lucide="skip-forward" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uC2A4\uD0B5';
      outcomeBg = holdBg;
    }

    // Tier1
    if (a.activity_type === 'TIER1_ANALYSIS' && a.phase === 'COMPLETE') {
      var summ = a.summary || '';
      if (summ.includes('HOLD') || summ.includes('\uAD00\uB9DD') || summ.includes('\uC2E4\uD328')) {
        outcome = 'hold';
        outcomeText = summ.includes('\uC2E4\uD328')
          ? '<i data-lucide="alert-triangle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uBD84\uC11D \uC2E4\uD328'
          : '<i data-lucide="pause-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> HOLD';
        outcomeBg = holdBg;
      } else if (summ.includes('BUY') || summ.includes('\uB9E4\uC218')) {
        outcome = 'buy';
        outcomeText = '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uB9E4\uC218';
        outcomeBg = buyBg;
      } else if (summ.includes('SELL') || summ.includes('\uB9E4\uB3C4')) {
        outcome = 'sell';
        outcomeText = '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uB9E4\uB3C4';
        outcomeBg = sellBg;
      }
    }

    // Tier2
    if (a.activity_type === 'TIER2_REVIEW' && a.phase === 'COMPLETE') {
      if ((a.summary || '').includes('\uBBF8\uC2B9\uC778')) {
        outcome = outcome !== 'error' ? 'hold' : outcome;
        outcomeText = '<i data-lucide="minus-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uBBF8\uC2B9\uC778';
        outcomeBg = holdBg;
      }
    }

    // Strategy eval
    if (a.activity_type === 'STRATEGY_EVAL' && a.phase === 'COMPLETE') {
      var s = a.summary || '';
      if ((s.includes('HOLD') || s.includes('\uC2A4\uD0B5')) && outcome !== 'error') {
        outcome = 'hold';
        outcomeText = '<i data-lucide="pause-circle" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> HOLD';
        outcomeBg = holdBg;
      }
    }

    // Decision / Order / Trade Result
    if (a.activity_type === 'DECISION' || a.activity_type === 'ORDER' || a.activity_type === 'TRADE_RESULT') {
      var sm = a.summary || '';
      var isSell = outcome === 'sell' || sm.includes('SELL') || sm.includes('\uB9E4\uB3C4');
      if (a.phase === 'COMPLETE' && (sm.includes('\uC8FC\uBB38 \uC811\uC218') || sm.includes('\uCCB4\uACB0'))) {
        outcome = isSell ? 'sell' : 'buy';
        outcomeText = isSell
          ? '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uB9E4\uB3C4 \uC644\uB8CC'
          : '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uB9E4\uC218 \uC644\uB8CC';
        outcomeBg = isSell ? sellBg : buyBg;
      } else if (sm.includes('\uC8FC\uBB38 \uC2E4\uD589')) {
        if (outcome !== 'buy' && outcome !== 'sell') {
          outcome = isSell ? 'sell' : 'buy';
          outcomeText = isSell
            ? '<i data-lucide="trending-down" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uB9E4\uB3C4'
            : '<i data-lucide="trending-up" class="w-3 h-3 inline-block mb-0.5 mr-0.5"></i> \uB9E4\uC218';
          outcomeBg = isSell ? sellBg : buyBg;
        }
      }
    }

    // Confidence
    if (a.confidence != null) {
      card.confidence = a.confidence;
    }
  });

  card.outcome = outcome;
  card.totalElapsed = totalMs;

  // Update outcome badge (uses insertAdjacentHTML for lucide icons)
  var outcomeEl = card.headerEl.querySelector('.stock-outcome');
  if (outcomeEl) {
    outcomeEl.className = 'stock-outcome flex items-center gap-1.5 text-xs px-2 py-0.5 rounded ' + outcomeBg;
    outcomeEl.textContent = '';
    outcomeEl.insertAdjacentHTML('beforeend', outcomeText);
  }

  // Update elapsed
  if (outcome !== 'progress') {
    if (card.liveTimer) {
      clearInterval(card.liveTimer);
      card.liveTimer = null;
    }
    var elapsedEl = card.headerEl.querySelector('.stock-elapsed');
    if (elapsedEl && totalMs > 0) {
      elapsedEl.textContent = (totalMs / 1000).toFixed(1) + '\uCD08';
    }
  }

  card.element.className = 'stock-card outcome-' + outcome;
  applyFilterToCard(card);
}

// ────────────────────────────────────────────
// applyFilterToCard
// ────────────────────────────────────────────
export function applyFilterToCard(card) {
  var filter = _config.getCurrentLogFilter ? _config.getCurrentLogFilter() : 'ALL';
  if (filter === 'ALL') {
    card.element.style.display = '';
    return;
  }
  card.element.style.display = ['buy', 'sell', 'hold'].includes(card.outcome) ? '' : 'none';
}

// ────────────────────────────────────────────
// toggleCardBody
// ────────────────────────────────────────────
export function toggleCardBody(card) {
  card.isOpen = !card.isOpen;
  card.bodyEl.classList.toggle('open', card.isOpen);
  var arrow = card.headerEl.querySelector('.stock-expand');
  if (arrow) arrow.style.transform = card.isOpen ? 'rotate(0deg)' : 'rotate(-90deg)';
}

// ────────────────────────────────────────────
// createBubble
// ────────────────────────────────────────────
export function createBubble(data) {
  var div = document.createElement('div');
  div.className = 'chat-bubble';

  var time = formatTime(data.created_at);
  var typeColor = getTypeColor(data.activity_type);

  var html = '<div class="flex items-start gap-2 px-3 py-1.5 rounded-lg hover:bg-dark-700/50 transition group">'
    + '<span class="text-xs text-gray-500 mt-0.5 shrink-0 w-14">' + time + '</span>'
    + '<div class="flex-1 min-w-0">'
    + '<div class="text-sm whitespace-pre-wrap">' + escapeHtml(formatAdminAgentText(data.summary)) + '</div>';

  var meta = [];
  if (data.llm_provider) meta.push('<span class="text-' + typeColor + '-400">' + escapeHtml(data.llm_provider) + '</span>');
  if (data.execution_time_ms) meta.push((data.execution_time_ms / 1000).toFixed(1) + '\uCD08');
  if (data.confidence != null) meta.push('\uC2E0\uB8B0\uB3C4 ' + Math.round(data.confidence * 100) + '%');
  if (meta.length) {
    html += '<div class="flex items-center gap-3 mt-0.5 text-xs text-gray-500">' + meta.join(' | ') + '</div>';
  }

  if (data.detail) {
    var detailId = 'detail-' + (data.id || Math.random().toString(36).slice(2, 8));
    var isLLMCall = data.activity_type === 'LLM_CALL';
    html += '<button onclick="window.__feedToggleDetail(\'' + detailId + '\')" class="text-xs text-gray-500 hover:text-gray-300 mt-1 flex items-center gap-1">'
      + (isLLMCall ? '<i data-lucide="message-square" class="w-3 h-3"></i> LLM \uB300\uD654 \uBCF4\uAE30' : '<i data-lucide="chevron-down" class="w-3 h-3"></i> \uC0C1\uC138 \uBCF4\uAE30')
      + '</button>'
      + '<div id="' + detailId + '" class="detail-content mt-1 text-xs bg-dark-900 rounded p-2 text-gray-400">'
      + (isLLMCall ? formatLLMConversation(data.detail) : '<div class="whitespace-pre-wrap break-all max-h-96 overflow-y-auto">' + formatDetail(data.detail, data.activity_type) + '</div>')
      + '</div>';
  }

  if (data.error_message) {
    html += '<div class="text-xs text-red-400 mt-1">' + escapeHtml(formatAdminAgentText(data.error_message)) + '</div>';
  }

  html += '</div></div>';
  div.insertAdjacentHTML('beforeend', html);
  return div;
}

// ────────────────────────────────────────────
// toggleDetail
// ────────────────────────────────────────────
export function toggleDetail(id) {
  var el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('open');
  var btn = el.previousElementSibling;
  if (btn && btn.tagName === 'BUTTON') {
    btn.setAttribute('aria-expanded', String(el.classList.contains('open')));
  }
}

// Expose toggleDetail globally for inline onclick handlers in generated HTML
window.__feedToggleDetail = toggleDetail;

// ────────────────────────────────────────────
// formatDetail
// ────────────────────────────────────────────
export function formatDetail(detail, activityType) {
  if (!detail) return '';
  try {
    var obj = transformAdminAgentValue(typeof detail === 'string' ? JSON.parse(detail) : detail);
    var indicators = obj && obj.market_context && obj.market_context.indicators;
    if (activityType === 'TIER1_ANALYSIS' && indicators) {
      var recentPrice = obj.market_context.current_price != null
        ? (_config.priceFormatter ? _config.priceFormatter(obj.market_context.current_price) : String(obj.market_context.current_price))
        : '-';
      return '<table class="tech-table mb-2">'
        + '<tr><th colspan="4" class="text-left">Technical Snapshot</th></tr>'
        + '<tr>'
        + '<td class="label">Price</td><td>' + recentPrice + '</td>'
        + '<td class="label">RSI(14)</td><td>' + (indicators.rsi != null ? Number(indicators.rsi).toFixed(1) : '-') + '</td>'
        + '</tr>'
        + '<tr>'
        + '<td class="label">MACD</td><td>' + (indicators.macd != null ? Number(indicators.macd).toFixed(2) : '-') + ' / Sig ' + (indicators.macd_signal != null ? Number(indicators.macd_signal).toFixed(2) : '-') + '</td>'
        + '<td class="label">BB \uC704\uCE58</td><td>' + (indicators.bollinger_band_position != null ? (Number(indicators.bollinger_band_position) * 100).toFixed(1) + '%' : '-') + '</td>'
        + '</tr>'
        + '<tr>'
        + '<td class="label">Vol Ratio</td><td>' + (indicators.volume_ratio != null ? Number(indicators.volume_ratio).toFixed(1) + 'x' : '-') + '</td>'
        + '<td class="label">SMA</td><td>5 ' + (indicators.sma_5 != null ? Number(indicators.sma_5).toFixed(0) : '-') + ' / 20 ' + (indicators.sma_20 != null ? Number(indicators.sma_20).toFixed(0) : '-') + '</td>'
        + '</tr>'
        + '</table>'
        + '<pre class="whitespace-pre-wrap text-gray-400">' + escapeHtml(JSON.stringify(obj, null, 2)) + '</pre>';
    }
    return '<pre class="whitespace-pre-wrap text-gray-400">' + escapeHtml(JSON.stringify(obj, null, 2)) + '</pre>';
  } catch (e) {
    return '<pre class="whitespace-pre-wrap text-gray-400">' + escapeHtml(formatAdminAgentText(String(detail))) + '</pre>';
  }
}

// ────────────────────────────────────────────
// formatLLMConversation  (collapsible sections)
// ────────────────────────────────────────────
export function formatLLMConversation(detail) {
  var obj = detail;
  try {
    if (typeof detail === 'string') obj = JSON.parse(detail);
    obj = transformAdminAgentValue(obj);
  } catch (e) {
    return '<pre class="whitespace-pre-wrap text-gray-400">' + escapeHtml(formatAdminAgentText(String(detail))) + '</pre>';
  }

  var systemPrompt = obj.llm_system_prompt || obj.system_prompt || obj.system || '';
  var prompt = obj.llm_prompt || obj.prompt || '';
  var response = obj.llm_response || obj.response || obj.raw_response || '';
  var model = obj.llm_model || obj.model || '';
  var html = '';

  var makeCollapsible = function (role, bodyText, defaultOpen) {
    var id = 'llm-' + Math.random().toString(36).slice(2, 8);
    var bodyClass = defaultOpen ? 'llm-body open' : 'llm-body';
    var typeClass = 'type-' + role.toLowerCase();
    return '<div class="llm-msg ' + typeClass + '">'
      + '<div class="llm-role" onclick="document.getElementById(\'' + id + '\').classList.toggle(\'open\'); this.querySelector(\'i\').classList.toggle(\'rotate-180\')">'
      + '<span>' + role + '</span>'
      + '<i data-lucide="chevron-down" class="w-3.5 h-3.5 transition-transform duration-200' + (defaultOpen ? ' rotate-180' : '') + '"></i>'
      + '</div>'
      + '<div id="' + id + '" class="' + bodyClass + '">' + escapeHtml(formatAdminAgentText(bodyText)) + '</div>'
      + '</div>';
  };

  if (model) html += '<div class="llm-model-tag">' + escapeHtml(String(model)) + '</div>';
  if (systemPrompt) html += makeCollapsible('SYSTEM', systemPrompt, false);
  if (prompt) html += makeCollapsible('PROMPT', prompt, false);
  if (response) html += makeCollapsible('RESPONSE', response, true);

  return '<div class="llm-conversation">' + (html || '<div class="text-gray-500">\uD45C\uC2DC\uD560 LLM \uC0C1\uC138\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4.</div>') + '</div>';
}

// ────────────────────────────────────────────
// setLogFilter
// ────────────────────────────────────────────
export function setLogFilter(filter) {
  // Update filter buttons
  document.querySelectorAll('.log-filter-btn').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });

  // Apply to all tracked cards
  var cards = _config.getStockCards();
  Object.values(cards).forEach(function (card) {
    applyFilterToCard(card);
  });

  // Also apply to standalone bubbles
  document.querySelectorAll('.chat-bubble').forEach(function (el) {
    if (filter === 'SIGNAL') {
      el.style.display = el.textContent.includes('\uB9E4\uC218') || el.textContent.includes('\uB9E4\uB3C4') || el.textContent.includes('BUY') || el.textContent.includes('SELL')
        ? ''
        : 'none';
    } else {
      el.style.display = '';
    }
  });

  // Scroll to bottom after filter change
  if (_config.getAutoScroll()) {
    var container = _config.getChatContainer();
    if (container) container.scrollTop = container.scrollHeight;
  }
}

// ────────────────────────────────────────────
// scrollToBottom / updateScrollBadge
// ────────────────────────────────────────────
export function scrollToBottom() {
  var container = _config.getChatContainer();
  if (!container) return;
  container.scrollTop = container.scrollHeight;
  _config.setAutoScroll(true);
  _config.setMissedCount(0);
  updateScrollBadge();
}

export function updateScrollBadge() {
  var badge = document.getElementById('scroll-fab-badge');
  var fab = document.getElementById('scroll-to-bottom');
  if (!badge) return;
  var missed = _config.getMissedCount();
  if (missed > 0) {
    badge.textContent = missed > 99 ? '99+' : String(missed);
    badge.classList.add('visible');
    if (fab) fab.classList.remove('hidden');
  } else {
    badge.classList.remove('visible');
    if (fab) fab.classList.add('hidden');
  }
}

// ────────────────────────────────────────────
// highlightCard / navigateToCard
// ────────────────────────────────────────────
export function highlightCard(data) {
  if (!data || !data.symbol) return;
  var cards = _config.getStockCards();
  var card = null;
  var values = Object.values(cards);
  for (var i = 0; i < values.length; i++) {
    if (values[i].symbol === data.symbol) { card = values[i]; break; }
  }
  if (!card || !card.element) return;
  card.element.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.element.classList.add('highlighted');
  setTimeout(function () { card.element.classList.remove('highlighted'); }, 3000);
}

export function navigateToCard(data) {
  if (!data) return;
  highlightCard(data);
}

// ────────────────────────────────────────────
// Feed-level getActivityIdentity (shared dedup logic)
// ────────────────────────────────────────────
export { getActivityIdentity } from './admin-core.js';
