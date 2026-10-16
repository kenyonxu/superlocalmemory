// od-polish.js: presentation helpers. No product logic, no API calls.
//
//   - labels each table cell with its column name so tables can reflow into
//     stacked cards on narrow screens (CSS in theme-slm.css);
//   - adds the mascot to empty states that are rendered by other panes;
//   - places a small mascot at the bottom of the sidebar;
//   - gives loading and error messages a mascot (moving while it waits).
(function () {
  'use strict';

  function labelTables(root) {
    (root || document).querySelectorAll('table.tbl, table.table').forEach(function (t) {
      var heads = [];
      var hr = t.tHead && t.tHead.rows[0];
      if (!hr) return;
      for (var i = 0; i < hr.cells.length; i++) heads.push((hr.cells[i].textContent || '').trim());
      t.classList.add('slm-stack');
      for (var b = 0; b < t.tBodies.length; b++) {
        var rows = t.tBodies[b].rows;
        for (var r = 0; r < rows.length; r++) {
          var cells = rows[r].cells;
          if (cells.length === 1 && cells[0].colSpan > 1) continue;
          for (var c = 0; c < cells.length; c++) {
            if (!cells[c].hasAttribute('data-label') && heads[c]) cells[c].setAttribute('data-label', heads[c]);
          }
        }
      }
    });
  }

  var EMPTY = [
    ['No peers connected', 'peek'],
    ['No sessions connected right now', 'peek'],
    ['No mesh activity recorded', 'peek'],
    ['No multi-agent memory yet', 'laptop'],
    ['No skill lineage yet', 'pointing'],
    ['No skill performance data yet', 'pointing'],
    ['No memories found', 'magnifier'],
    ['No stored memory matched', 'magnifier'],
    ['No messages from this peer yet', 'peek']
  ];

  function decorateEmpty(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll('.tab-pane div, .tab-pane p, .tab-pane h3, .tab-pane h4, .tab-pane li');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.children.length > 2 || n.hasAttribute('data-mascot') || n.closest('[data-mascot]')) continue;
      var txt = (n.textContent || '').trim();
      if (txt.length > 400) continue;
      for (var k = 0; k < EMPTY.length; k++) {
        if (txt.indexOf(EMPTY[k][0]) !== 0) continue;
        // an inner element carries the same message: decorate that one instead
        var pat = EMPTY[k][0];
        if (Array.prototype.some.call(n.children, function (c) { return (c.textContent || '').trim().indexOf(pat) === 0; })) break;
        var host = n;
        // lift to the block that holds the whole empty message
        if (host.tagName !== 'DIV' || host.children.length === 0) host = n.parentNode && n.parentNode.children.length <= 3 ? n.parentNode : n;
        if (host.hasAttribute('data-mascot') || host.querySelector('[data-mascot]') || host.querySelector('img.slm-empty-mascot')) break;
        host.setAttribute('data-mascot', EMPTY[k][1]);
        host.classList.add('slm-empty-host');
        var img = document.createElement('img');
        img.className = 'slm-empty-mascot-top';
        img.src = '/static/assets/mascot/' + EMPTY[k][1] + '-320.webp';
        img.alt = ''; img.width = 88; img.height = 88; img.loading = 'lazy';
        host.insertBefore(img, host.firstChild);
        break;
      }
    }
  }

  // A tab count that has not loaded yet reads "…": hide it until it has a number.
  // Loading and error messages written by any pane get the mascot too, so a
  // wait looks like a wait (a gently moving mascot) and a failure looks calm
  // rather than broken. Text only decides; no pane has to opt in.
  var LOADING = /^(Loading|Reading|Searching|Checking|Fetching)\b.{0,70}(…|\.\.\.)$/;
  var TROUBLE = /^(Could not|Couldn['’]t|Unavailable|That did not work|Failed to|Search is unavailable|Search could not|.{0,60} could not be read)/;

  function polishStyle() {
    if (document.getElementById('slm-polish-style')) return;
    var s = document.createElement('style');
    s.id = 'slm-polish-style';
    s.textContent =
      '.slm-state-mascot{width:40px;height:40px;vertical-align:middle;margin-right:10px;display:inline-block}' +
      '.slm-state-mascot.loading{animation:slm-polish-bob 1.6s ease-in-out infinite}' +
      '@keyframes slm-polish-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}' +
      '@media (prefers-reduced-motion: reduce){.slm-state-mascot.loading{animation:none}}';
    document.head.appendChild(s);
  }

  function decorateStates(root) {
    var nodes = (root || document).querySelectorAll('.tab-pane div, .tab-pane p, .tab-pane span, .tab-pane td, main div, main p');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var had = n.querySelector(':scope > img.slm-state-mascot');
      if (n.children.length - (had ? 1 : 0) > 1 || n.closest('#slm-startup') ||
          n.closest('button,a,label,option,select')) continue;
      var txt = (n.textContent || '').trim();
      if (txt.length > 200) continue;
      var kind = LOADING.test(txt) ? 'loading' : (TROUBLE.test(txt) ? 'trouble' : '');
      if (!kind) { if (had) had.remove(); continue; }
      if (had && had.classList.contains(kind)) continue;
      if (had) had.remove();
      if (n.querySelector('img, svg, canvas')) continue;
      polishStyle();
      var img = document.createElement('img');
      img.className = 'slm-state-mascot ' + kind;
      img.src = '/static/assets/mascot/' + (kind === 'loading' ? 'flying' : 'peek') + '-320.webp';
      img.alt = ''; img.width = 40; img.height = 40;
      n.insertBefore(img, n.firstChild);
    }
  }

  function tidyCounts() {
    document.querySelectorAll('.tab .cnt').forEach(function (c) {
      var pending = (c.textContent || '').trim() === '\u2026';
      if (pending !== c.hasAttribute('hidden')) { if (pending) c.setAttribute('hidden', ''); else c.removeAttribute('hidden'); }
    });
  }

  function sidebarMascot() {
    var foot = document.getElementById('od-sidebar-foot');
    if (!foot || document.querySelector('.sidebar-mascot')) return;
    var img = document.createElement('img');
    img.className = 'sidebar-mascot';
    img.src = '/static/assets/mascot/hero-320.webp';
    img.alt = ''; img.width = 64; img.height = 64; img.loading = 'lazy';
    foot.parentNode.insertBefore(img, foot);
  }

  var pending = false;
  function run() { pending = false; labelTables(); decorateEmpty(); decorateStates(); sidebarMascot(); tidyCounts(); }
  function schedule() { if (!pending) { pending = true; setTimeout(run, 150); } }
  function start() {
    run();
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  }
  window.SLMPolish = { labelTables: labelTables };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { setTimeout(start, 80); });
  else setTimeout(start, 80);
}());
