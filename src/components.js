// ══════════════════════════════════════════════════════════
// AgentOS Mission Control — components.js
// Reusable factory functions. All styling via tokens.css.
// ══════════════════════════════════════════════════════════

// ── GlassCard — frosted glass container ──
function GlassCard(props) {
  var children = props.children || "";
  var className = props.className || "";
  var style = props.style || {};
  var styleStr = "";
  for (var k in style) {
    if (style.hasOwnProperty(k)) {
      styleStr += k + ": " + style[k] + "; ";
    }
  }
  return '<div class="glass-card ' + className + '" style="' +
    'background: var(--bg-glass); ' +
    'backdrop-filter: blur(var(--blur-heavy)); ' +
    '-webkit-backdrop-filter: blur(var(--blur-heavy)); ' +
    'border: 1px solid var(--border-glass); ' +
    'border-radius: var(--radius-lg); ' +
    styleStr +
    '">' + children + '</div>';
}

// ── Badge — generic label badge ──
function Badge(props) {
  var text = props.text || "";
  var color = props.color || "var(--text-muted)";
  var variant = props.variant || "subtle";
  var bg = variant === "solid" ? color : color + "20";
  var border = variant === "solid" ? "none" : "1px solid " + color + "40";
  return '<span class="badge" style="' +
    'background: ' + bg + '; ' +
    'color: ' + color + '; ' +
    'border: ' + border + '; ' +
    'padding: var(--space-1) var(--space-2); ' +
    'border-radius: var(--radius-full); ' +
    'font: 500 var(--font-size-xs) var(--font-mono); ' +
    'letter-spacing: var(--letter-spacing-wide); ' +
    'text-transform: uppercase;' +
    '">' + text + '</span>';
}

// ── StatusBadge — semantic colored badge ──
function StatusBadge(props) {
  var status = (props.status || "unknown").toLowerCase();
  var label = props.label || status;
  var colorMap = {
    ok: "var(--status-ok-text)", healthy: "var(--status-ok-text)", running: "var(--status-ok-text)",
    completed: "var(--status-ok-text)", done: "var(--status-ok-text)", success: "var(--status-ok-text)",
    connected: "var(--status-ok-text)", active: "var(--status-ok-text)", online: "var(--status-ok-text)",
    warn: "var(--status-warn-text)", warning: "var(--status-warn-text)", pending: "var(--status-warn-text)",
    blocked: "var(--status-warn-text)", degraded: "var(--status-warn-text)",
    err: "var(--status-err-text)", error: "var(--status-err-text)", failed: "var(--status-err-text)",
    offline: "var(--status-err-text)", down: "var(--status-err-text)", crashed: "var(--status-err-text)",
  };
  var bgMap = {
    ok: "var(--status-ok-bg)", healthy: "var(--status-ok-bg)", running: "var(--status-ok-bg)",
    completed: "var(--status-ok-bg)", done: "var(--status-ok-bg)", success: "var(--status-ok-bg)",
    connected: "var(--status-ok-bg)", active: "var(--status-ok-bg)", online: "var(--status-ok-bg)",
    warn: "var(--status-warn-bg)", warning: "var(--status-warn-bg)", pending: "var(--status-warn-bg)",
    blocked: "var(--status-warn-bg)", degraded: "var(--status-warn-bg)",
    err: "var(--status-err-bg)", error: "var(--status-err-bg)", failed: "var(--status-err-bg)",
    offline: "var(--status-err-bg)", down: "var(--status-err-bg)", crashed: "var(--status-err-bg)",
  };
  var color = colorMap[status] || "var(--status-muted-text)";
  var bg = bgMap[status] || "var(--status-muted-bg)";
  return '<span class="status-badge" style="' +
    'display:inline-block;padding:var(--space-1) var(--space-2);border-radius:var(--radius-full);' +
    'font:500 var(--font-size-xs) var(--font-mono);letter-spacing:var(--letter-spacing-wide);text-transform:uppercase;' +
    'background:' + bg + ';color:' + color + ';' +
    '">' + (props.dot !== false ? '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:' + color + ';margin-right:var(--space-1);vertical-align:middle;"></span>' : '') +
    label + '</span>';
}

// ── StatCard — metric display ──
function StatCard(props) {
  var label = props.label || "";
  var value = props.value || "";
  var accent = props.accent || "var(--text-primary)";
  var subtext = props.subtext || "";
  var barWidth = props.barWidth || "100%";
  return '<div class="stat-card" style="' +
    'background: var(--bg-glass); ' +
    'backdrop-filter: blur(var(--blur-heavy)); ' +
    '-webkit-backdrop-filter: blur(var(--blur-heavy)); ' +
    'border: 1px solid var(--border-glass); ' +
    'border-radius: var(--radius-lg); ' +
    'padding: var(--space-4); ' +
    'position: relative;' +
    '">' +
    '<div style="font: 400 var(--font-size-xs) var(--font-mono); color: var(--text-muted); letter-spacing: var(--letter-spacing-wide); text-transform: uppercase;">' + label + '</div>' +
    '<div style="font: 700 var(--font-size-2xl) var(--font-display); color: ' + accent + ';">' + value + '</div>' +
    (subtext ? '<div style="font: 400 var(--font-size-sm) var(--font-mono); color: var(--text-muted); margin-top: var(--space-1);">' + subtext + '</div>' : '') +
    '<div style="position: absolute; bottom: 0; left: 0; height: 2px; width: ' + barWidth + '; background: ' + accent + '; border-radius: var(--radius-full);"></div>' +
    '</div>';
}

// ── DataTable — array-of-arrays table ──
function DataTable(props) {
  var headers = props.headers || [];
  var rows = props.rows || [];
  var opts = props.opts || {};
  var monospace = opts.monospace !== false;
  var compact = opts.compact || false;
  var maxHeight = opts.maxHeight || "";
  var scrollable = opts.scrollable || false;
  var pad = compact ? "var(--space-1) var(--space-2)" : "var(--space-2) var(--space-3)";
  var font = "var(--font-size-sm) " + (monospace ? "var(--font-mono)" : "var(--font-display)");
  var html = '<div' + (maxHeight ? ' style="max-height:' + maxHeight + ';overflow-y:auto;"' : '') + '>';
  if (scrollable) {
    html = '<div class="table-scroll table-scroll-ios">';
  }
  html += '<table class="data-table" style="width:100%;border-collapse:collapse;font:' + font + ';">';
  html += '<thead><tr style="position:sticky;top:0;z-index:var(--z-sticky);">';
  for (var i = 0; i < headers.length; i++) {
    var stickyClass = (scrollable && i === 0) ? ' table-sticky-col' : '';
    html += '<th class="' + stickyClass.trim() + '" style="text-align:left;padding:' + pad + ';color:var(--dim);font:600 var(--font-size-xs) var(--font-mono);text-transform:uppercase;letter-spacing:var(--letter-spacing-wide);border-bottom:1px solid var(--border);background:var(--bg-base);">' + headers[i] + '</th>';
  }
  html += '</tr></thead><tbody>';
  for (var r = 0; r < rows.length; r++) {
    html += '<tr style="border-bottom:1px solid var(--border-glass);transition:background var(--transition-fast);">';
    for (var c = 0; c < headers.length; c++) {
      var val = rows[r][c] != null ? rows[r][c] : "—";
      var tdStickyClass = (scrollable && c === 0) ? ' table-sticky-col' : '';
      html += '<td class="' + tdStickyClass.trim() + '" style="padding:' + pad + ';color:var(--text);">' + val + '</td>';
    }
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  if (rows.length === 0) {
    html = '<div style="padding:var(--space-6);text-align:center;color:var(--dim);font:var(--font-size-sm) var(--font-display);">No data</div>';
  }
  return html;
}

// ── PulseDot — animated status indicator ──
var _pulseStyleInjected = false;
function PulseDot(props) {
  var status = (props.status || "ok").toLowerCase();
  var colorMap = {
    ok: "var(--status-ok-text)", running: "var(--status-ok-text)", online: "var(--status-ok-text)", healthy: "var(--status-ok-text)",
    warn: "var(--status-warn-text)", degraded: "var(--status-warn-text)", pending: "var(--status-warn-text)",
    err: "var(--status-err-text)", down: "var(--status-err-text)", offline: "var(--status-err-text)",
  };
  var color = colorMap[status] || "var(--status-muted-text)";
  var size = props.size || "10px";
  var pulse = props.pulse !== false;
  var html = '<span class="pulse-dot" style="' +
    'display:inline-block;width:' + size + ';height:' + size + ';border-radius:var(--radius-full);' +
    'background:' + color + ';' +
    (pulse ? 'animation:pulse-dot 2s ease-in-out infinite;' : '') +
    '"></span>';
  if (!_pulseStyleInjected) {
    html += '<style>@keyframes pulse-dot{0%,100%{opacity:1;box-shadow:0 0 0 0 ' + color + ';}50%{opacity:0.6;box-shadow:0 0 0 3px ' + color + '20;}}</style>';
    _pulseStyleInjected = true;
  }
  return html;
}

// ── Timestamp — smart relative time display ──
function Timestamp(props) {
  var iso = props.iso;
  if (!iso) return '<span style="color:var(--dim)">\u2014</span>';
  if (iso.startsWith("t+")) return '<span style="color:var(--dim);font:var(--font-size-sm) var(--font-mono);">' + iso + '</span>';
  try {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '<span style="color:var(--dim)">' + iso + '</span>';
    var now = new Date();
    var diff = Math.floor((now - d) / 1000);
    var abs = Math.abs(diff);
    var suffix = diff >= 0 ? "ago" : "from now";
    var rel;
    if (abs < 5) rel = "just now";
    else if (abs < 60) rel = abs + "s " + suffix;
    else if (abs < 3600) rel = Math.floor(abs / 60) + "m " + suffix;
    else if (abs < 86400) rel = Math.floor(abs / 3600) + "h " + suffix;
    else if (abs < 604800) rel = Math.floor(abs / 86400) + "d " + suffix;
    else rel = d.toLocaleDateString();
    var absStr = d.toLocaleString(void 0, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    var color = abs > 86400 ? "var(--dim)" : abs > 3600 ? "var(--text)" : "var(--status-active)";
    return '<span class="timestamp" title="' + d.toISOString() + '" style="color:' + color + ';font:var(--font-size-sm) var(--font-mono);">' + rel + ' <span style="color:var(--dim);font-size:var(--text-xs);">(' + absStr + ')</span></span>';
  } catch (e) {
    return '<span style="color:var(--dim)">' + iso + '</span>';
  }
}

// ── ProgressBar — horizontal fill bar ──
function ProgressBar(props) {
  var pct = props.pct || 0;
  var color = props.color || "var(--brand-cyan)";
  var height = props.height || "6px";
  return '<div style="background: rgba(255,255,255,0.05); border-radius: var(--radius-full); height: ' + height + '; overflow: hidden;">' +
    '<div style="height: 100%; width: ' + pct + '%; background: ' + color + '; border-radius: var(--radius-full); transition: width var(--transition-normal);"></div>' +
    '</div>';
}

// ── ThinBar — vertical bar sparkline ──
function ThinBar(props) {
  var values = props.values || [];
  var color = props.color || "var(--brand-cyan)";
  var html = '<div style="display: flex; gap: 2px; align-items: flex-end; height: 32px;">';
  for (var i = 0; i < values.length; i++) {
    var v = values[i];
    var h = Math.max(2, v * 0.32);
    var opacity = v > 0 ? 0.85 : 0.15;
    html += '<div style="width: calc(100% / 7 - 2px); height: ' + h + 'px; background: ' + color + '; border-radius: var(--radius-sm); opacity: ' + opacity + ';"></div>';
  }
  html += '</div>';
  return html;
}

// ── DonutChart — conic-gradient ring ──
function DonutChart(props) {
  var slices = props.slices || [];
  var total = props.total || 0;
  var colors = props.colors || ["var(--brand-cyan)", "var(--brand-mint)", "var(--brand-violet)", "var(--brand-teal)", "var(--brand-violet)"];
  if (total === 0) {
    return '<div style="width:130px;height:130px;border-radius:50%;background:conic-gradient(rgba(255,255,255,0.05) 0% 100%);display:flex;align-items:center;justify-content:center;"><span style="color:var(--text-muted);font:400 var(--font-size-sm) var(--font-mono);">0</span></div>';
  }
  var gradient = [];
  var offset = 0;
  for (var i = 0; i < slices.length; i++) {
    var pct = (slices[i] / total) * 100;
    gradient.push(colors[i % colors.length] + " " + offset + "% " + (offset + pct) + "%");
    offset += pct;
  }
  return '<div style="width:130px;height:130px;border-radius:50%;background:conic-gradient(' + gradient.join(",") + ');display:flex;align-items:center;justify-content:center;"><span style="color:var(--text-primary);font:700 var(--font-size-lg) var(--font-display);">' + total + '</span></div>';
}

// ── DataPath — annotate an element with a data-path for diff-patch targeting ──
// Wraps content in a <span> or <div> with data-path="..." attribute.
// Used by refresh.js diff-patch engine for surgical DOM updates.
// Props: { path: "gateway.pid", tag: "span" (default), content: "12345", attrs: {} }
function DataPath(props) {
  var path = props.path || "";
  var tag = props.tag || "span";
  var content = props.content !== undefined ? props.content : "";
  var attrs = props.attrs || {};
  var attrStr = ' data-path="' + path + '"';
  for (var k in attrs) {
    if (attrs.hasOwnProperty(k)) {
      attrStr += ' ' + k + '="' + attrs[k] + '"';
    }
  }
  return '<' + tag + attrStr + '>' + content + '</' + tag + '>';
}
