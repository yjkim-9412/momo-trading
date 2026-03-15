// ── admin-toast.js — Toast notifications, browser notifications, title flash ──
import { ORIGINAL_TITLE } from './admin-core.js';

let _titleFlashInterval = null;
let _notificationTagPrefix = 'momo';

export function setNotificationPrefix(prefix) {
  _notificationTagPrefix = prefix;
}

/**
 * Show a toast notification.
 *
 * Signature variants:
 *   show('message', 'success')              — legacy (type string)
 *   show('message', 'info', 5000)           — legacy (type + duration)
 *   show('message', { type, level, duration, persistent, onClick })
 */
export function show(message, optsOrType, legacyDuration) {
  var opts = typeof optsOrType === 'string'
    ? { type: optsOrType, duration: legacyDuration }
    : (optsOrType || {});
  var type = opts.type || 'info';
  var level = opts.level;
  var duration = opts.duration;
  var persistent = opts.persistent || false;
  var onClick = opts.onClick;

  var container = document.getElementById('toast-container');
  if (!container) return;

  var toast = document.createElement('div');
  var cls = level === 'CRITICAL' ? 'toast-critical' : level === 'HIGH' ? 'toast-high' : 'toast-' + type;
  toast.className = 'toast ' + cls;

  var dur = persistent ? 0 : (duration || (level === 'HIGH' ? 8000 : 3000));

  if (persistent || onClick) {
    var inner = document.createElement('div');
    inner.className = 'flex items-center justify-between gap-2';
    var span = document.createElement('span');
    span.className = 'flex-1';
    span.textContent = message;
    inner.appendChild(span);
    if (persistent) {
      var dismiss = document.createElement('button');
      dismiss.className = 'toast-dismiss text-white/60 hover:text-white ml-2';
      dismiss.textContent = '\u2715';
      dismiss.addEventListener('click', function (e) {
        e.stopPropagation();
        _removeToast(toast);
      });
      inner.appendChild(dismiss);
    }
    toast.appendChild(inner);
  } else {
    toast.textContent = message;
  }

  if (onClick) {
    toast.style.cursor = 'pointer';
    toast.addEventListener('click', function (e) {
      if (!e.target.closest('.toast-dismiss')) onClick();
    });
  }

  container.prepend(toast);
  while (container.children.length > 5) _removeToast(container.lastChild);

  if (dur > 0) {
    window.setTimeout(function () { toast.classList.add('toast-fade-out'); }, Math.max(0, dur - 300));
    window.setTimeout(function () { _removeToast(toast); }, dur);
  }
}

function _removeToast(el) {
  if (el && el.parentNode) el.remove();
}

function _parseActivityDetail(detail) {
  if (!detail) return null;
  if (typeof detail === 'object') return detail;
  try {
    return JSON.parse(detail);
  } catch (e) {
    return null;
  }
}

function _isHoldLikeTier1Summary(summary, summaryUpper) {
  return summaryUpper.includes('HOLD')
    || summary.includes('스킵')
    || summary.includes('관망')
    || summary.includes('보류')
    || summary.includes('미승인');
}

function _buildTier1Signal(symbol, summary, recommendation) {
  if (recommendation === 'BUY' || recommendation === 'SELL') {
    return {
      level: 'HIGH',
      title: recommendation === 'BUY' ? '\uB9E4\uC218 \uC2E0\uD638' : '\uB9E4\uB3C4 \uC2E0\uD638',
      body: (symbol || '') + ' \u2014 ' + (summary || ''),
    };
  }
  return { level: 'NONE' };
}

/**
 * Classify an activity's importance for notification routing.
 * Returns { level: 'CRITICAL'|'HIGH'|'MEDIUM'|'INFO'|'NONE', title?, body? }
 */
export function classifyImportance(data) {
  var t = data.activity_type;
  var p = data.phase;
  var summary = data.summary || '';
  var s = summary.toUpperCase();
  var detail = _parseActivityDetail(data.detail);
  var recommendation = detail && typeof detail.recommendation === 'string'
    ? detail.recommendation.toUpperCase()
    : '';

  if ((t === 'ORDER' || t === 'TRADE_RESULT') && p === 'COMPLETE') {
    return {
      level: 'CRITICAL',
      title: (s.includes('\uB9E4\uB3C4') || s.includes('SELL')) ? '\uB9E4\uB3C4 \uCCB4\uACB0' : '\uB9E4\uC218 \uCCB4\uACB0',
      body: (data.symbol || '') + ' \u2014 ' + summary,
    };
  }

  if (t === 'TIER1_ANALYSIS' && p === 'COMPLETE') {
    if (recommendation === 'HOLD' || _isHoldLikeTier1Summary(summary, s)) {
      return { level: 'NONE' };
    }

    if (recommendation === 'BUY' || recommendation === 'SELL') {
      return _buildTier1Signal(data.symbol, summary, recommendation);
    }

    if (s.includes('BUY') || s.includes('SELL')) {
      return _buildTier1Signal(data.symbol, summary, s.includes('BUY') ? 'BUY' : 'SELL');
    }
  }

  if (t === 'TIER2_REVIEW' && p === 'COMPLETE' && s.includes('\uBBF8\uC2B9\uC778')) {
    return {
      level: 'HIGH',
      title: 'TIER2 \uBBF8\uC2B9\uC778',
      body: (data.symbol || '') + ' \u2014 ' + summary,
    };
  }

  if (t === 'DECISION' && p === 'COMPLETE') {
    return {
      level: 'HIGH',
      title: '\uC8FC\uBB38 \uC2E4\uD589',
      body: (data.symbol || '') + ' \u2014 ' + summary,
    };
  }

  return { level: 'NONE' };
}

export function sendBrowserNotification(info) {
  if (!document.hidden) return;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  try {
    var n = new Notification(info.title, {
      body: info.body,
      tag: _notificationTagPrefix + '-' + info.level + '-' + Date.now(),
      requireInteraction: info.level === 'CRITICAL',
    });
    n.onclick = function () { window.focus(); n.close(); };
  } catch (e) { /* Notification not supported */ }
}

export function requestNotificationPermission() {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

export function flashTitle(alertText) {
  if (_titleFlashInterval) return;
  var showAlert = true;
  _titleFlashInterval = setInterval(function () {
    document.title = showAlert ? alertText : ORIGINAL_TITLE;
    showAlert = !showAlert;
  }, 1000);
}

export function stopTitleFlash() {
  if (!_titleFlashInterval) return;
  clearInterval(_titleFlashInterval);
  _titleFlashInterval = null;
  document.title = ORIGINAL_TITLE;
}

// Stop flashing when the user returns to the tab
document.addEventListener('visibilitychange', function () {
  if (!document.hidden) stopTitleFlash();
});
