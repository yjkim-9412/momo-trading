/**
 * admin-toast.js — Toast notifications, importance classification, browser
 * notifications, and tab title flash.
 *
 * Depends on: admin-core.js (Admin namespace must already exist)
 *
 * Attaches:
 *   Admin.Toast.show(message, optsOrType, legacyDuration)
 *   Admin.Toast._removeToast(el)
 *   Admin.Toast.classifyImportance(data)
 *   Admin.Toast.sendBrowserNotification(info)
 *   Admin.Toast.requestNotificationPermission()
 *   Admin.Toast.flashTitle(alertText)
 *   Admin.Toast.stopTitleFlash()
 */
(function () {
  'use strict';

  var _titleFlashInterval = null;

  var Toast = {};

  /**
   * Show a toast notification.
   *
   * Signature variants:
   *   show('message', 'success')              — legacy (type string)
   *   show('message', 'info', 5000)           — legacy (type + duration)
   *   show('message', { type, level, duration, persistent, onClick })
   */
  Toast.show = function (message, optsOrType, legacyDuration) {
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
          Toast._removeToast(toast);
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
    while (container.children.length > 5) Toast._removeToast(container.lastChild);

    if (dur > 0) {
      window.setTimeout(function () { toast.classList.add('toast-fade-out'); }, Math.max(0, dur - 300));
      window.setTimeout(function () { Toast._removeToast(toast); }, dur);
    }
  };

  Toast._removeToast = function (el) {
    if (el && el.parentNode) el.remove();
  };

  /**
   * Classify an activity's importance for notification routing.
   * Returns { level: 'CRITICAL'|'HIGH'|'MEDIUM'|'INFO'|'NONE', title?, body? }
   */
  Toast.classifyImportance = function (data) {
    var t = data.activity_type;
    var p = data.phase;
    var s = (data.summary || '').toUpperCase();

    if ((t === 'ORDER' || t === 'TRADE_RESULT') && p === 'COMPLETE') {
      return {
        level: 'CRITICAL',
        title: (s.includes('\uB9E4\uB3C4') || s.includes('SELL')) ? '\uB9E4\uB3C4 \uCCB4\uACB0' : '\uB9E4\uC218 \uCCB4\uACB0',
        body: (data.symbol || '') + ' \u2014 ' + (data.summary || ''),
      };
    }

    if (t === 'TIER1_ANALYSIS' && p === 'COMPLETE' && (s.includes('BUY') || s.includes('SELL'))) {
      return {
        level: 'HIGH',
        title: s.includes('BUY') ? '\uB9E4\uC218 \uC2E0\uD638' : '\uB9E4\uB3C4 \uC2E0\uD638',
        body: (data.symbol || '') + ' \u2014 ' + (data.summary || ''),
      };
    }

    if (t === 'TIER2_REVIEW' && p === 'COMPLETE' && s.includes('\uBBF8\uC2B9\uC778')) {
      return {
        level: 'HIGH',
        title: 'TIER2 \uBBF8\uC2B9\uC778',
        body: (data.symbol || '') + ' \u2014 ' + (data.summary || ''),
      };
    }

    if (t === 'DECISION' && p === 'COMPLETE') {
      return {
        level: 'HIGH',
        title: '\uC8FC\uBB38 \uC2E4\uD589',
        body: (data.symbol || '') + ' \u2014 ' + (data.summary || ''),
      };
    }

    return { level: 'NONE' };
  };

  Toast.sendBrowserNotification = function (info) {
    if (!document.hidden) return;
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
    try {
      var n = new Notification(info.title, {
        body: info.body,
        tag: Admin.notificationTagPrefix + '-' + info.level + '-' + Date.now(),
        requireInteraction: info.level === 'CRITICAL',
      });
      n.onclick = function () { window.focus(); n.close(); };
    } catch (e) { /* Notification not supported */ }
  };

  Toast.requestNotificationPermission = function () {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  };

  Toast.flashTitle = function (alertText) {
    if (_titleFlashInterval) return;
    var show = true;
    _titleFlashInterval = setInterval(function () {
      document.title = show ? alertText : Admin.ORIGINAL_TITLE;
      show = !show;
    }, 1000);
  };

  Toast.stopTitleFlash = function () {
    if (!_titleFlashInterval) return;
    clearInterval(_titleFlashInterval);
    _titleFlashInterval = null;
    document.title = Admin.ORIGINAL_TITLE;
  };

  // Stop flashing when the user returns to the tab
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) Toast.stopTitleFlash();
  });

  // ── Expose ──
  window.Admin.Toast = Toast;
})();
