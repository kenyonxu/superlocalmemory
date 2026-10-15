// od-help.js: plain-language help for every pane.
//
//   1. a one-line "what is this" under each pane title, ending in a
//      "Read the guide" link to the matching page in docs/ on GitHub;
//   2. info buttons (i) next to the numbers, badges and controls that are not
//      obvious. They work on hover, keyboard focus and tap;
//   3. a "Help & docs" panel opened from the sidebar footer.
//
// Every sentence here comes from the README or docs/*.md. Display only: it
// reads and writes nothing about your memory.
(function () {
  'use strict';

  var DOCS = 'https://github.com/qualixar/superlocalmemory/blob/main/docs/';
  var REPO = 'https://github.com/qualixar/superlocalmemory';

  var PANES = {
    'dashboard-pane': ['A quick look at how much your memory holds, which apps share it, and a box to ask it a question.', 'getting-started.md'],
    'graph-pane': ['A map of the people, places and things found in your memories and how they connect.', 'getting-started.md'],
    'entities-pane': ['The people, organisations, concepts and events found in your memories, each with a timeline of how it changed.', 'recall.md'],
    'agents-pane': ['Which AI apps saved what. Each app stamps its own name on every memory it writes.', 'plugins.md'],
    'memories-pane': ['Everything you have saved. Search it, filter it, and see why each memory was kept.', 'recall.md'],
    'brain-pane': ['A local view of what your memory has learned from use. It only observes and never changes your answers.', 'recall.md'],
    'health-pane': ['How your memory is doing. It fills in missing details on older memories while your computer is idle.', 'memory-health.md'],
    'operations-pane': ['Access rules, audit records and erasure tools for your memory.', 'compliance.md'],
    'skills-pane': ['Shows how well your AI skills are working and can draft improved versions for you to approve.', 'skill-evolution.md'],
    'loops-pane': ['Evidence from Bounded Loops runs. Bounded Loops is a separate, optional product; this page only shows what was observed.', 'bounded-loops-bridge.md'],
    'answercheck-pane': ['Lets your memory say it does not know instead of guessing. Try a question and see the verdict.', 'answer-check.md'],
    'mesh-pane': ['A live view of the AI sessions connected to this memory.', 'getting-started.md'],
    'settings-pane': ['Choose how SuperLocalMemory runs and what it is allowed to do.', 'configuration.md'],
    'optimize-pane': ['Settings that can reduce how much text your AI assistant has to read again. Savings depend on how you work.', 'optimize-overview.md'],
    'mcp-pane': ['The tools your AI apps can use to reach this memory.', 'mcp-tools.md'],
    'apps-pane': ['AI apps and bots that share this memory. Connect one, check it, or disconnect it.', 'plugins.md'],
    'media-pane': ['Add pictures and PDFs so you can find them by what they show or say. Off until you turn it on.', 'pictures-and-documents.md'],
    'botmsg-pane': ['What bots and web chats sent through the mesh. Treat messages from outside this computer as untrusted text.', 'web-agents/README.md'],
    'backup-pane': ['Safety copies of your memory: restore points taken before updates, and optional cloud backup.', 'restore-points.md']
  };

  var TIPS = [
    { sel: '#kpi-mem .label', find: 'memories', tip: 'Everything saved in the current profile. Each memory is split into smaller facts that recall can find.' },
    { sel: '#kpi-docs .label', find: 'documents', tip: 'Pictures and PDF pages you added. This is off until you turn it on in Documents & Images.' },
    { sel: '#kpi-graph .label', find: 'graph', tip: 'The people, organisations and things found in your memories. Connections show how they relate.' },
    { sel: '#kpi-apps .label', find: 'connected', tip: 'AI apps and bots that share this one memory, such as Claude Code or Cursor.' },
    { sel: '#kpi-ac .label', find: 'answer check', tip: 'A second look that lets your memory say "That isn’t in SuperLocalMemory" instead of guessing.' },
    { sel: '#kpi-health .label', find: 'memory health', tip: 'Fills in missing details on older memories in the background while your computer is idle, and offers repairs that need your say-so.' },
    { sel: '#dash-chip-mode', find: 'mode', tip: 'Mode A makes no model-provider call. Anything that sends data out is a choice you make.', after: true },
    { sel: '#dash-chip-profile', find: 'profile', tip: 'Profiles keep separate sets of memories inside one installation.', after: true },
    { sel: '#dashboard-pane .dash-kv .list-row:last-child > .muted', find: 'switch mode', tip: 'Mode A makes no model-provider call. Anything that sends data out is a choice you make.' },
    { sel: '#memories-pane .tab', find: 'saved views', tip: 'A question you ask often, saved so you can run it again with one click.', after: true },
    { sel: '#memories-pane .tab', find: 'recall lab', tip: 'Ask a question and see the same result an AI assistant would get, and why each memory was chosen.', after: true },
    { sel: '#memories-pane th', find: 'kind', tip: 'What sort of thing a memory is, such as a decision or a rule. Recall uses it to answer "what did we decide" with decisions.' },
    { sel: '#memories-pane .seg', find: 'mine', tip: 'Mine is this profile. Shared and Global memories show up only after you turn on shared recall, which is off by default.', after: true },
    { sel: '#memories-pane label', find: 'show closest', tip: 'When nothing answers the search exactly, list the closest matches anyway.' },
    { sel: '#backup-pane h2', find: 'backup', tip: 'Backs up your memory databases to Google Drive and GitHub. Credentials are kept in your computer’s keychain when one is available.' },
    { sel: '#apps-pane h2', find: 'connected apps', tip: 'Connecting an app writes SuperLocalMemory into that tool’s settings so it can use your memory.' }
  ];

  var guideBase = DOCS;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function link(href, text) {
    var a = el('a', 'help-link', text);
    a.href = href; a.target = '_blank'; a.rel = 'noopener';
    return a;
  }

  // ----- pane subtitle -----
  function ensureStrip(paneId) {
    var pane = document.getElementById(paneId);
    var spec = PANES[paneId];
    if (!pane || !spec) return;
    if (pane.querySelector('[data-pane-help]')) return;
    var strip = el('p', 'pane-help');
    strip.setAttribute('data-pane-help', paneId);
    strip.appendChild(el('span', null, spec[0] + ' '));
    strip.appendChild(link(guideBase + spec[1], 'Read the guide'));
    if (paneId === 'dashboard-pane') {
      var heroText = pane.querySelector('.dash-hero-text');
      if (heroText) { heroText.insertBefore(strip, heroText.querySelector('.dash-chips')); }
      return;
    }
    var head = pane.querySelector('.page-head');
    if (!head) {
      var h = pane.querySelector('h1, h2, h3');
      if (h) {
        head = h;
        var par = h.parentNode;
        // a heading inside a flex row (title + badge): place the line under the row
        if (par && par !== pane && par.children.length > 1) {
          var d = window.getComputedStyle(par).display;
          if (d.indexOf('flex') >= 0 || d.indexOf('grid') >= 0) head = par;
        }
        if (head.nextElementSibling && head.nextElementSibling.tagName === 'P') head = head.nextElementSibling;
      }
    }
    if (head && head.parentNode) head.parentNode.insertBefore(strip, head.nextSibling);
    else pane.insertBefore(strip, pane.firstChild);
  }

  // ----- info tips -----
  var tipBox = null, openFor = null, uid = 0;
  function box() {
    if (tipBox) return tipBox;
    tipBox = el('div', 'info-pop');
    tipBox.id = 'slm-info-pop'; tipBox.setAttribute('role', 'tooltip'); tipBox.hidden = true;
    document.body.appendChild(tipBox);
    return tipBox;
  }
  function show(btn) {
    var b = box();
    b.textContent = btn.getAttribute('data-tip');
    b.hidden = false;
    var r = btn.getBoundingClientRect();
    var w = Math.min(280, window.innerWidth - 24);
    b.style.maxWidth = w + 'px';
    var left = Math.max(12, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 12));
    b.style.left = left + 'px';
    var below = r.bottom + 8;
    b.style.top = below + 'px';
    var bh = b.offsetHeight;
    if (below + bh > window.innerHeight - 8) b.style.top = Math.max(8, r.top - bh - 8) + 'px';
    btn.setAttribute('aria-describedby', 'slm-info-pop');
    openFor = btn;
  }
  function hide() {
    if (!tipBox || tipBox.hidden) return;
    tipBox.hidden = true;
    if (openFor) openFor.removeAttribute('aria-describedby');
    openFor = null;
  }
  function makeTip(text, label) {
    var b = el('button', 'info-tip');
    b.type = 'button';
    b.setAttribute('data-tip', text);
    b.setAttribute('aria-label', 'More info about ' + label + ': ' + text);
    b.id = 'slm-tip-' + (++uid);
    b.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="4.9" r="0.95" fill="currentColor"/><path d="M8 7.3v4.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    return b;
  }
  document.addEventListener('mouseover', function (e) { var t = e.target.closest && e.target.closest('.info-tip'); if (t) show(t); });
  document.addEventListener('mouseout', function (e) { var t = e.target.closest && e.target.closest('.info-tip'); if (t && openFor === t && document.activeElement !== t) hide(); });
  document.addEventListener('focusin', function (e) { var t = e.target.closest && e.target.closest('.info-tip'); if (t) show(t); });
  document.addEventListener('focusout', function (e) { if (e.target.closest && e.target.closest('.info-tip')) hide(); });
  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('.info-tip');
    if (t) { e.preventDefault(); e.stopPropagation(); if (openFor === t && !tipBox.hidden && t.getAttribute('data-pinned') === '1') { t.removeAttribute('data-pinned'); hide(); } else { show(t); t.setAttribute('data-pinned', '1'); } return; }
    document.querySelectorAll('.info-tip[data-pinned]').forEach(function (n) { n.removeAttribute('data-pinned'); });
    hide();
  }, true);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { hide(); closePanel(); } });
  window.addEventListener('scroll', hide, true);

  function applyTips() {
    TIPS.forEach(function (d) {
      document.querySelectorAll(d.sel).forEach(function (n) {
        var txt = (n.textContent || '').trim().toLowerCase();
        if (txt.indexOf(d.find) !== 0 && !(d.find === 'documents' && txt.indexOf('documents') === 0)) return;
        if (n.getAttribute('data-has-tip')) return;
        n.setAttribute('data-has-tip', '1');
        var label = (n.textContent || '').trim().split('\n')[0].slice(0, 40);
        var b = makeTip(d.tip, label);
        if (d.after && n.parentNode) n.parentNode.insertBefore(b, n.nextSibling);
        else n.appendChild(b);
      });
    });
  }

  // ----- Help & docs panel -----
  var panel = null, scrim = null, lastFocus = null;
  function jump(paneId, focusSel) {
    closePanel();
    var navLink = document.querySelector('.nav-link[data-tab="' + paneId + '"]');
    if (navLink) navLink.click();
    if (focusSel) setTimeout(function () { var t = document.querySelector(focusSel); if (t) { t.scrollIntoView({ block: 'center' }); t.focus(); } }, 250);
  }
  function build() {
    if (panel) return panel;
    scrim = el('div', 'help-scrim'); scrim.hidden = true;
    scrim.addEventListener('click', closePanel);
    panel = el('aside', 'help-panel'); panel.id = 'slm-help-panel';
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-labelledby', 'slm-help-title'); panel.hidden = true;

    var head = el('div', 'help-head');
    head.appendChild(el('h3', null, 'Help & docs')).id = 'slm-help-title';
    var close = el('button', 'btn icon ghost help-close', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Close help');
    close.addEventListener('click', closePanel);
    head.appendChild(close);
    panel.appendChild(head);

    var body = el('div', 'help-body');
    var intro = el('div', 'help-intro');
    var img = el('img', 'help-mascot'); img.src = '/static/assets/mascot/pointing-320.webp'; img.alt = ''; img.width = 88; img.height = 88; img.loading = 'lazy';
    intro.appendChild(img);
    intro.appendChild(el('p', null, 'SuperLocalMemory is one memory that every AI app you use can share. It lives on your computer, and it tells you plainly when it does not know an answer. Decisions, documents, screenshots and PDFs can all be found by asking in your own words.'));
    body.appendChild(intro);

    body.appendChild(el('h4', null, 'Read more'));
    var links = el('ul', 'help-links');
    [['Website', 'https://www.superlocalmemory.com'],
     ['Docs folder on GitHub', REPO + '/tree/main/docs'],
     ['README', REPO + '/blob/main/README.md'],
     ['Setup guide for AI agents (SETUP_FOR_AGENTS.md)', REPO + '/blob/main/SETUP_FOR_AGENTS.md'],
     ['Wiki', REPO + '/wiki']].forEach(function (l) {
      var li = el('li'); li.appendChild(link(l[1], l[0])); links.appendChild(li);
    });
    body.appendChild(links);

    body.appendChild(el('h4', null, 'Where do I…'));
    var ul = el('ul', 'help-where');
    [['Save a memory', 'dashboard-pane', '#quick-store-input', 'Dashboard, “Remember something”'],
     ['Ask a question', 'dashboard-pane', '#daily-question', 'Dashboard, “Ask your memory”'],
     ['Add pictures or PDFs', 'media-pane', null, 'Documents & Images'],
     ['Connect an app', 'apps-pane', null, 'Connected apps'],
     ['Fix Memory health', 'health-pane', null, 'Health'],
     ['Erase data', 'operations-pane', null, 'Governance']].forEach(function (w) {
      var li = el('li');
      var b = el('button', 'help-jump'); b.type = 'button';
      b.appendChild(el('b', null, w[0]));
      b.appendChild(el('span', 'muted', w[3]));
      b.addEventListener('click', function () { jump(w[1], w[2]); });
      li.appendChild(b); ul.appendChild(li);
    });
    body.appendChild(ul);
    panel.appendChild(body);
    document.body.appendChild(scrim); document.body.appendChild(panel);
    return panel;
  }
  function openPanel() {
    build();
    lastFocus = document.activeElement;
    panel.hidden = false; scrim.hidden = false;
    requestAnimationFrame(function () { panel.classList.add('open'); scrim.classList.add('on'); });
    var btn = document.getElementById('od-help-btn'); if (btn) btn.setAttribute('aria-expanded', 'true');
    var c = panel.querySelector('.help-close'); if (c) c.focus();
  }
  function closePanel() {
    if (!panel || panel.hidden) return;
    panel.classList.remove('open'); scrim.classList.remove('on');
    panel.hidden = true; scrim.hidden = true;
    var btn = document.getElementById('od-help-btn'); if (btn) btn.setAttribute('aria-expanded', 'false');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function mountButton() {
    var foot = document.getElementById('od-sidebar-foot');
    if (!foot) return false;
    if (document.getElementById('od-help-btn')) return true;
    var b = el('button', 'help-open');
    b.type = 'button'; b.id = 'od-help-btn';
    b.setAttribute('aria-haspopup', 'dialog'); b.setAttribute('aria-expanded', 'false');
    b.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M6.1 6.2a2 2 0 1 1 3 1.7c-.7.4-1.1.8-1.1 1.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="8" cy="11.6" r=".9" fill="currentColor"/></svg>';
    b.appendChild(document.createTextNode(' Help & docs'));
    b.addEventListener('click', function () { if (panel && !panel.hidden) closePanel(); else openPanel(); });
    foot.appendChild(b);
    return true;
  }

  var pending = false;
  function refresh() {
    pending = false;
    Object.keys(PANES).forEach(ensureStrip);
    applyTips();
    mountButton();
  }
  function schedule() { if (pending) return; pending = true; setTimeout(refresh, 120); }

  function start() {
    refresh();
    var root = document.querySelector('.content') || document.body;
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    void root;
  }
  window.SLMHelp = { open: openPanel, close: closePanel, refresh: refresh };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { setTimeout(start, 50); });
  else setTimeout(start, 50);
}());
