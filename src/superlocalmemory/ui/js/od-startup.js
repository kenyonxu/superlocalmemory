// od-startup.js: a "please wait" bar while SuperLocalMemory starts or warms up.
//
// Reads GET /health (no login, no store access) until the daemon says it is
// ready and its recall models are warm, then stops asking. While it waits, a
// bar at the top says so with the mascot, so a pane that is still empty does
// not look broken. If the daemon stops answering (a restart), the bar says it
// is reconnecting and the check starts again.
(function () {
  'use strict';

  var POLL_MS = 2000;
  var bar = null, timer = null, shownAt = 0;

  function style() {
    if (document.getElementById('slm-startup-style')) return;
    var s = document.createElement('style');
    s.id = 'slm-startup-style';
    s.textContent =
      '#slm-startup{position:sticky;top:0;z-index:60;display:flex;align-items:center;gap:12px;' +
      'padding:8px 16px;background:var(--card,#16131f);border-bottom:1px solid var(--border,#2a2540);' +
      'color:var(--fg,#eee);font-size:13px}' +
      '#slm-startup img{width:36px;height:36px;flex:0 0 auto;animation:slm-bob 1.6s ease-in-out infinite}' +
      '#slm-startup .slm-startup-text{flex:1 1 auto;min-width:0}' +
      '#slm-startup .slm-startup-sub{color:var(--fg-2,#aaa);font-size:12px}' +
      '#slm-startup .slm-startup-track{position:absolute;left:0;right:0;bottom:0;height:2px;overflow:hidden}' +
      '#slm-startup .slm-startup-track::after{content:"";position:absolute;left:-40%;width:40%;height:100%;' +
      'background:var(--violet,#8b5cf6);animation:slm-slide 1.4s ease-in-out infinite}' +
      '@keyframes slm-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}' +
      '@keyframes slm-slide{0%{left:-40%}100%{left:100%}}' +
      '@media (prefers-reduced-motion: reduce){#slm-startup img,#slm-startup .slm-startup-track::after{animation:none}}';
    document.head.appendChild(s);
  }

  function show(title, sub) {
    style();
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'slm-startup';
      bar.setAttribute('role', 'status');
      bar.setAttribute('aria-live', 'polite');
      var img = document.createElement('img');
      img.src = '/static/assets/mascot/flying-320.webp';
      img.alt = '';
      bar.appendChild(img);
      var text = document.createElement('div');
      text.className = 'slm-startup-text';
      text.appendChild(document.createElement('div'));
      var s = document.createElement('div');
      s.className = 'slm-startup-sub';
      text.appendChild(s);
      bar.appendChild(text);
      var track = document.createElement('div');
      track.className = 'slm-startup-track';
      bar.appendChild(track);
      var host = document.querySelector('main') || document.body;
      host.insertBefore(bar, host.firstChild);
      shownAt = Date.now();
    }
    bar.querySelector('.slm-startup-text > div').textContent = title;
    bar.querySelector('.slm-startup-sub').textContent = sub;
  }

  function hide() {
    if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
    bar = null;
  }

  var PLAIN = { indexes: 'the search indexes', entity_graph: 'the knowledge graph',
                recalls: 'recall', embedding: 'the embedding model', reranker: 'the ranking model' };

  function warmingNames(r) {
    var w = (r && r.recall_warmup && r.recall_warmup.warming) || [];
    if (!Array.isArray(w)) return [];
    return w.map(function (n) { return PLAIN[n] || String(n).replace(/_/g, ' '); });
  }

  function check() {
    timer = null;
    fetch('/health', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (h) {
        if (!h) {
          show('SuperLocalMemory is starting…', 'Your memories are safe. This page fills in as soon as it is ready.');
          return again();
        }
        var r = h.readiness || {};
        var warm = r.recall_warmup ? !!r.recall_warmup.warm : true;
        if (h.ready && warm && r.engine !== false) {
          hide();
          return;  // ready: stop asking until the next failure
        }
        var names = warmingNames(r);
        show('SuperLocalMemory is warming up…',
             names.length ? 'Getting ' + names.join(', ') + ' ready. Recall answers in full once this finishes.'
                          : 'Loading your memories and models. Recall answers in full once this finishes.');
        again();
      })
      .catch(function () {
        show('Reconnecting to SuperLocalMemory…', 'It may be restarting. Nothing you saved is lost.');
        again();
      });
  }

  function again() { if (!timer) timer = setTimeout(check, POLL_MS); }

  window.SLMStartup = { check: check };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', check);
  else check();
}());
