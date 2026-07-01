// ══════════════════════════════════════════════════════════
// AgentOS Mission Control — refresh.js
// Smart auto-refresh controller: SSE primary, polling fallback,
// diff-patch DOM updates, visibility awareness, exponential backoff,
// data age indicator, and user-selectable refresh modes.
//
// Architecture: ARCHITECTURE.md §A2 (Refresh Architecture) + §A4 (Diff Protocol)
// ══════════════════════════════════════════════════════════

(function () {
  'use strict';

  // ── Backoff constants ──
  var BACKOFF_STEPS = [5000, 10000, 30000, 60000];
  var STALE_THRESHOLD = 60000; // 60s — beyond this, "Stale"

  // ── Helpers ──
  function safeJSONParse(str) {
    try { return JSON.parse(str); } catch (e) { return null; }
  }

  function now() {
    return Date.now();
  }

  // ── Deep diff: compare two plain objects, produce flat diff array ──
  function computeDiff(oldState, newState, prefix) {
    prefix = prefix || '';
    var diffs = [];
    var oldKeys = oldState ? Object.keys(oldState) : [];
    var newKeys = newState ? Object.keys(newState) : [];

    // Detect changed / added keys
    for (var i = 0; i < newKeys.length; i++) {
      var key = newKeys[i];
      var fullPath = prefix ? prefix + '.' + key : key;
      var oldVal = oldState ? oldState[key] : undefined;
      var newVal = newState[key];

      if (oldVal === undefined) {
        diffs.push({ path: fullPath, value: newVal, op: 'set' });
      } else if (typeof newVal !== typeof oldVal) {
        diffs.push({ path: fullPath, value: newVal, op: 'replace' });
      } else if (typeof newVal === 'object' && newVal !== null) {
        if (Array.isArray(newVal)) {
          if (JSON.stringify(newVal) !== JSON.stringify(oldVal)) {
            diffs.push({ path: fullPath, value: newVal, op: 'replace' });
          }
        } else {
          diffs = diffs.concat(computeDiff(oldVal, newVal, fullPath));
        }
      } else if (newVal !== oldVal) {
        diffs.push({ path: fullPath, value: newVal, op: 'set' });
      }
    }

    // Detect removed keys
    for (var j = 0; j < oldKeys.length; j++) {
      var oldKey = oldKeys[j];
      if (!(oldKey in (newState || {}))) {
        var remPath = prefix ? prefix + '.' + oldKey : oldKey;
        diffs.push({ path: remPath, value: null, op: 'remove' });
      }
    }

    return diffs;
  }

  // ── Apply diff array to DOM ──
  function applyDiff(diffArray) {
    if (!diffArray || diffArray.length === 0) return;

    for (var i = 0; i < diffArray.length; i++) {
      var d = diffArray[i];
      var el = document.querySelector('[data-path="' + d.path + '"]');

      // Skip if element is being edited
      if (el) {
        if (el.hasAttribute('data-editing')) continue;
        var focused = el.querySelector(':focus');
        if (focused || el.matches(':focus')) continue;
      }

      switch (d.op) {
        case 'set':
          if (el) {
            if (typeof d.value === 'string' || typeof d.value === 'number') {
              el.textContent = String(d.value);
            }
          }
          break;
        case 'replace':
          // For complex replacements, delegate to the renderFn via a flag
          // We set a data attribute to signal a replace is needed
          if (el) {
            el.setAttribute('data-dirty', 'replace');
            el.setAttribute('data-dirty-value', JSON.stringify(d.value));
          }
          break;
        case 'remove':
          if (el) {
            el.setAttribute('data-removed', 'true');
            el.style.opacity = '0';
            el.style.transition = 'opacity 0.3s';
            setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
          }
          break;
        case 'append':
          // Handled by renderFn — too complex for generic diff
          break;
        default:
          break;
      }
    }
  }

  // ── Process replace operations: re-render changed subtrees ──
  function processReplaceOps(data, renderFn, prevData) {
    if (!renderFn || !prevData) return;
    var dirtyEls = document.querySelectorAll('[data-dirty="replace"]');
    for (var i = 0; i < dirtyEls.length; i++) {
      var el = dirtyEls[i];
      if (el.hasAttribute('data-editing')) continue;
      if (el.querySelector(':focus') || el.matches(':focus')) continue;
      // Re-render this specific subtree by calling the renderFn
      // with full data — the renderFn should handle per-element updates
      try {
        var path = el.getAttribute('data-path');
        var newVal = JSON.parse(el.getAttribute('data-dirty-value') || 'null');
        // Apply the replace by updating textContent for simple values,
        // or delegate to renderFn for complex objects
        if (typeof newVal === 'string' || typeof newVal === 'number') {
          el.textContent = String(newVal);
        }
        el.removeAttribute('data-dirty');
        el.removeAttribute('data-dirty-value');
      } catch (e) { /* ignore malformed data */ }
    }
  }

  // ── Factory: createRefreshController ──
  window.createRefreshController = function (config) {
    config = config || {};

    var MODE_SSE = 'sse';
    var MODE_POLL = 'poll';
    var MODE_MANUAL = 'manual';

    var mode = config.mode || MODE_SSE;
    var pollInterval = config.pollInterval || 10000;
    var endpoint = config.endpoint || '/api/snapshot';
    var renderFn = config.renderFn || function () {};
    var diffFn = config.diffFn || null;
    var dataAgeEl = config.dataAgeEl || null;
    var onError = config.onError || function () {};
    var onStatusChange = config.onStatusChange || function () {};

    var es = null;            // EventSource handle
    var pollTimer = null;     // setInterval handle
    var ageTimer = null;      // data-age setInterval handle
    var paused = false;
    var lastState = null;
    var lastUpdateTimestamp = 0;
    var backoffIdx = 0;
    var running = false;

    // ── Data age display ──
    function updateAgeDisplay() {
      if (!dataAgeEl) return;
      var el = typeof dataAgeEl === 'string' ? document.querySelector(dataAgeEl) : dataAgeEl;
      if (!el) return;

      if (mode === MODE_MANUAL) {
        el.textContent = 'Manual \u23CE';
        el.style.color = 'var(--text-muted)';
        return;
      }

      if (paused) {
        el.textContent = 'Paused \u23F8';
        el.style.color = 'var(--brand-amber)';
        return;
      }

      if (lastUpdateTimestamp === 0) {
        el.textContent = 'Waiting…';
        el.style.color = 'var(--text-muted)';
        return;
      }

      var age = Math.floor((now() - lastUpdateTimestamp) / 1000);

      if (mode === MODE_SSE && age < 5) {
        el.textContent = 'Live \u25CF';
        el.style.color = 'var(--brand-mint)';
      } else if (age < 10) {
        el.textContent = 'Updated ' + age + 's ago';
        el.style.color = 'var(--brand-mint)';
      } else if (age < 60) {
        el.textContent = 'Updated ' + age + 's ago';
        el.style.color = 'var(--brand-amber)';
      } else {
        var mins = Math.floor(age / 60);
        el.textContent = 'Stale (' + mins + 'm ago)';
        el.style.color = 'var(--brand-red)';
      }
    }

    function startAgeClock() {
      if (ageTimer) clearInterval(ageTimer);
      ageTimer = setInterval(updateAgeDisplay, 1000);
      updateAgeDisplay();
    }

    function stopAgeClock() {
      if (ageTimer) { clearInterval(ageTimer); ageTimer = null; }
    }

    // ── State preservation ──
    function preserveScroll() {
      var scrollY = window.scrollY;
      var scrollX = window.scrollX;
      return function restoreScroll() {
        window.scrollTo(scrollX, scrollY);
      };
    }

    // ── Visibility ──
    function handleVisibility() {
      if (document.hidden) {
        paused = true;
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        updateAgeDisplay();
      } else {
        paused = false;
        if (mode === MODE_POLL && running) {
          startPolling();
        }
        // Apply latest state
        if (lastState) {
          renderFn(lastState);
        }
        updateAgeDisplay();
      }
    }

    document.addEventListener('visibilitychange', handleVisibility);

    // ── SSE connection ──
    function connectSSE() {
      if (es) {
        try { es.close(); } catch (e) { /* ignore */ }
        es = null;
      }

      try {
        es = new EventSource('/events');

        es.addEventListener('snapshot', function (e) {
          var data = safeJSONParse(e.data);
          if (!data) return;

          var restoreScroll = preserveScroll();

          var isFirstEvent = (lastState === null);

          if (lastState) {
            var diffs = computeDiff(lastState, data);
            if (diffFn && diffs.length > 0) {
              diffFn(diffs);
            }
          }

          lastState = data;
          lastUpdateTimestamp = now();
          backoffIdx = 0; // reset backoff on success

          // Full render only on first load; subsequent updates are diff-patch
          if (isFirstEvent) {
            renderFn(data);
          }

          // Handle replace ops: re-render subtrees marked by computeDiff
          processReplaceOps(data, renderFn, lastState);

          restoreScroll();
          updateAgeDisplay();
        });

        es.onerror = function () {
          onStatusChange('sse-error');
          try { es.close(); } catch (e) { /* ignore */ }
          es = null;

          // Fall back to polling with backoff
          if (mode === MODE_SSE && running) {
            fallbackToPoll();
          }
        };

        es.onopen = function () {
          onStatusChange('sse-connected');
          updateAgeDisplay();
        };

      } catch (e) {
        onStatusChange('sse-error');
        if (mode === MODE_SSE && running) {
          fallbackToPoll();
        }
      }
    }

    function fallbackToPoll() {
      var delay = BACKOFF_STEPS[Math.min(backoffIdx, BACKOFF_STEPS.length - 1)];
      backoffIdx = Math.min(backoffIdx + 1, BACKOFF_STEPS.length - 1);

      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(function () {
        if (!running) return;
        doPollFetch(function (success) {
          if (success) {
            backoffIdx = 0;
            // Try SSE reconnect
            if (mode === MODE_SSE) {
              connectSSE();
            }
          } else {
            fallbackToPoll();
          }
        });
      }, delay);
    }

    // ── Polling ──
    function doPollFetch(cb) {
      fetch(endpoint)
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function (data) {
          var restoreScroll = preserveScroll();

          if (lastState) {
            var diffs = computeDiff(lastState, data);
            if (diffFn && diffs.length > 0) {
              diffFn(diffs);
            }
            applyDiff(diffs);
          }

          // Diff-patch updates only in polling mode; no full re-render
          // (first load is handled by caller via renderFn at startup)
          lastState = data;
          lastUpdateTimestamp = now();

          restoreScroll();
          updateAgeDisplay();

          if (cb) cb(true);
        })
        .catch(function (err) {
          onError(err);
          if (cb) cb(false);
        });
    }

    function startPolling() {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (mode !== MODE_POLL) return;
      if (document.hidden) return;

      // Initial fetch
      doPollFetch();

      // Regular interval
      pollTimer = setInterval(function () {
        if (!running) return;
        if (document.hidden) return;
        doPollFetch();
      }, pollInterval);
    }

    // ── Public API ──
    var controller = {
      start: function () {
        if (running) return;
        running = true;
        paused = document.hidden;
        backoffIdx = 0;

        startAgeClock();

        if (mode === MODE_SSE) {
          connectSSE();
        } else if (mode === MODE_POLL) {
          startPolling();
        }
        // MANUAL mode: nothing auto-starts
      },

      stop: function () {
        running = false;

        if (es) {
          try { es.close(); } catch (e) { /* ignore */ }
          es = null;
        }

        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }

        stopAgeClock();
        updateAgeDisplay();
      },

      setMode: function (newMode, newInterval) {
        var prevMode = mode;
        mode = newMode;

        // Save preference
        try {
          localStorage.setItem('hermes-refresh-mode', mode);
          if (newInterval) {
            localStorage.setItem('hermes-refresh-interval', String(newInterval));
          }
        } catch (e) { /* localStorage unavailable */ }

        // Stop old transport
        if (es) { try { es.close(); } catch (e) { /* ignore */ } es = null; }
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }

        if (newInterval && newInterval > 0) {
          pollInterval = newInterval;
        }

        onStatusChange('mode-' + mode);

        if (!running) return;

        if (mode === MODE_SSE) {
          connectSSE();
        } else if (mode === MODE_POLL) {
          startPolling();
        }
        // MANUAL: no transport started

        updateAgeDisplay();
      },

      refresh: function () {
        // Force an immediate refresh
        if (es && es.readyState === EventSource.OPEN) {
          // SSE is connected — will get next push; do immediate poll as well
          doPollFetch();
        } else {
          doPollFetch();
        }
      },

      getAge: function () {
        if (lastUpdateTimestamp === 0) return -1;
        return Math.floor((now() - lastUpdateTimestamp) / 1000);
      },

      getLastState: function () {
        return lastState;
      },

      getMode: function () {
        return mode;
      },

      isRunning: function () {
        return running;
      },

      isPaused: function () {
        return paused;
      },

      _pollInterval: pollInterval,

      destroy: function () {
        controller.stop();
        document.removeEventListener('visibilitychange', handleVisibility);
      }
    };

    // Restore saved mode preference
    try {
      var savedMode = localStorage.getItem('hermes-refresh-mode');
      var savedInterval = localStorage.getItem('hermes-refresh-interval');
      if (savedMode && (savedMode === MODE_SSE || savedMode === MODE_POLL || savedMode === MODE_MANUAL)) {
        mode = savedMode;
        if (savedInterval && savedInterval > 0) {
          pollInterval = parseInt(savedInterval, 10);
        }
      }
    } catch (e) { /* localStorage unavailable */ }

    return controller;
  };

  // ── Helper: create a refresh mode selector widget ──
  window.createRefreshSelector = function (controller, containerEl) {
    if (!containerEl) return;

    var MODES = [
      { value: 'sse', label: 'SSE Live' },
      { value: 'poll-10', label: '10s Poll' },
      { value: 'poll-30', label: '30s Poll' },
      { value: 'manual', label: 'Manual' }
    ];

    var currentMode = controller.getMode();
    var hasSSE = typeof EventSource !== 'undefined';

    var html = '<select id="refresh-selector" style="' +
      'background:var(--bg-glass);color:var(--text-primary);' +
      'border:1px solid var(--border-glass);padding:var(--space-1,4px) var(--space-2,8px);' +
      'border-radius:var(--radius-full, 20px);font:400 var(--font-size-sm, 0.78rem) var(--font-mono, monospace);' +
      'min-height:44px;min-width:44px;cursor:pointer;' +
      '">';

    for (var i = 0; i < MODES.length; i++) {
      var m = MODES[i];
      var val = m.value;
      // If SSE not supported, skip SSE option
      if (val === 'sse' && !hasSSE) continue;

      var selected = '';
      if (val === 'sse' && currentMode === 'sse') selected = ' selected';
      if (val === 'poll-10' && currentMode === 'poll') {
        if (controller._pollInterval === 10000 || !controller._pollInterval) selected = ' selected';
      }
      if (val === 'poll-30' && currentMode === 'poll') {
        if (controller._pollInterval === 30000) selected = ' selected';
      }
      if (val === 'manual' && currentMode === 'manual') selected = ' selected';

      html += '<option value="' + val + '"' + selected + '>' + m.label + '</option>';
    }
    html += '</select>';

    containerEl.innerHTML = html;

    var select = containerEl.querySelector('#refresh-selector');
    if (!select) return;

    select.addEventListener('change', function () {
      var v = select.value;
      if (v === 'sse') {
        controller.setMode('sse');
      } else if (v === 'poll-10') {
        controller.setMode('poll', 10000);
      } else if (v === 'poll-30') {
        controller.setMode('poll', 30000);
      } else if (v === 'manual') {
        controller.setMode('manual');
      }
    });
  };

})();
