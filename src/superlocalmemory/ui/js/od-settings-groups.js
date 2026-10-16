// Extracted od-settings.js feature scope; initialized once with the pane helpers.
(function () {
  'use strict';
  var ctx;
  var tokenError, getToken, sendAuthenticated, requireSuccess, authRequest, authPut, authPost, failed, readOk, toast, q, el, setSt, showRestartNote, makeSwitch, makeSel, makeTin, makeSuggest, makeRange, makeBtn, makeSt, bRow, restartDaemonFromNote, makeRestartNote, drawDecay, makeRow, makeGrp, P, ID;
  window.SLMSettingsGroups = function (helpers) {
    ctx = helpers;
    ({ tokenError, getToken, sendAuthenticated, requireSuccess, authRequest, authPut, authPost, failed, readOk, toast, q, el, setSt, showRestartNote, makeSwitch, makeSel, makeTin, makeSuggest, makeRange, makeBtn, makeSt, bRow, restartDaemonFromNote, makeRestartNote, drawDecay, makeRow, makeGrp, P, ID } = ctx);
    return { buildStorage:buildStorage, loadStorage:loadStorage, saveStorage:saveStorage, buildScope:buildScope, loadScope:loadScope, buildRuntime:buildRuntime, loadRuntime:loadRuntime, buildCapture:buildCapture, loadCapture:loadCapture, buildRecall:buildRecall, loadRecall:loadRecall, buildInvoke:buildInvoke, loadInvoke:loadInvoke, buildForgetting:buildForgetting, loadForgetting:loadForgetting, saveForgetting:saveForgetting, buildEvolution:buildEvolution, loadEvolution:loadEvolution, buildBackup:buildBackup, loadBackup:loadBackup, saveBackupConfig:saveBackupConfig, buildMesh:buildMesh, loadMesh:loadMesh, buildTrust:buildTrust, loadTrust:loadTrust, saveTrust:saveTrust, buildRateLimit:buildRateLimit, _rlSetLb:_rlSetLb, loadRateLimit:loadRateLimit, saveRateLimit:saveRateLimit, buildDaemon:buildDaemon, loadDaemon:loadDaemon, saveDaemon:saveDaemon };
  };
    var _captureConfig = {};
  function buildStorage() {
    var gBk = makeSel('stor-graph', [
      { v:'auto',   l:'auto (recommended)' },
      { v:'sqlite', l:'SQLite' },
      { v:'cozo',   l:'CozoDB' }
    ]);
    var vBk = makeSel('stor-vec', [
      { v:'auto',       l:'auto (recommended)' },
      { v:'sqlite-vec', l:'sqlite-vec' },
      { v:'lancedb',    l:'LanceDB' }
    ]);
    var dirInp = makeTin('stor-dir', '~/.superlocalmemory');
    dirInp.disabled = true;
    Object.assign(dirInp.style, { opacity:'.55', cursor:'not-allowed' });
    var saveBtn  = makeBtn('stor-save', 'Save Backends', true);
    var st       = makeSt('stor');
    var restNote = makeRestartNote('stor');
    saveBtn.addEventListener('click', saveStorage);
    return makeGrp('Storage Backends', 'operations', [
      makeRow('Graph backend', 'Engine for relationship and knowledge-graph queries', 'graph_backend', gBk),
      makeRow('Vector backend', 'Engine for semantic similarity search', 'vector_backend', vBk),
      makeRow('Base directory', 'Where SLM stores its database files (read-only)', 'base_dir', dirInp),
      makeRow('Actions', '', '', bRow(saveBtn, st, restNote))
    ]);
  }
  function loadStorage() {
    fetch('/api/v3/storage/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var g = q('stor-graph'); if (g) g.value = d.graph_backend  || 'auto';
        var v = q('stor-vec');   if (v) v.value = d.vector_backend || 'auto';
        var dir = q('stor-dir'); if (dir) dir.value = d.base_dir || '';
      }).catch(function () {});
  }
  function saveStorage() {
    setSt('stor', 'Saving…', null);
    showRestartNote('stor', false);
    authPut('/api/v3/storage/config', {
      graph_backend:  q('stor-graph') ? q('stor-graph').value : 'auto',
      vector_backend: q('stor-vec')   ? q('stor-vec').value   : 'auto'
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        setSt('stor', 'Saved', true);
        toast('Storage backends saved');
        showRestartNote('stor', !!d.restart_required);
        loadStorage();
      }).catch(failed('stor', 'Storage save'));
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 4 — Memory Scope
     GET/PUT /api/v3/scope/config
  ═══════════════════════════════════════════════════════════ */
  function buildScope() {
    var defSel = makeSel('scope-def', [
      { v:'personal', l:'Personal (recommended)' },
      { v:'shared',   l:'Shared' },
      { v:'global',   l:'Global' }
    ]);
    var shSw = makeSwitch('scope-shared', false);
    var glSw = makeSwitch('scope-global', false);
    var st = makeSt('scope');
    function save() {
      authPut('/api/v3/scope/config', {
        default_scope:         q('scope-def') ? q('scope-def').value : 'personal',
        recall_include_shared: shSw.classList.contains('on'),
        recall_include_global: glSw.classList.contains('on')
      }).then(function (r) { return r.json(); })
        .then(function (d) {
          setSt('scope', d.success !== false ? 'Applied' : (d.error || 'Not applied'), d.success !== false);
          if (d.success !== false) loadScope();
        })
        .catch(failed('scope', 'Scope save', loadScope));
    }
    defSel.addEventListener('change', save);
    shSw.addEventListener('od-toggle', save);
    glSw.addEventListener('od-toggle', save);
    return makeGrp('Memory Scope (auto-saves)', 'memories', [
      makeRow('Default write scope', 'Where new memories are stored — Personal is privacy-safe', 'scope.default_scope', defSel),
      makeRow('Include shared memories in recall', 'Surface memories shared across your agents', 'scope.recall_include_shared', shSw),
      makeRow('Include global memories in recall', 'Surface system-wide shared facts', 'scope.recall_include_global', glSw),
      makeRow('Status', '', '', st)
    ]);
  }
  function loadScope() {
    fetch('/api/v3/scope/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var def = q('scope-def'); if (def) def.value = d.default_scope || 'personal';
        var sh = q('scope-shared');
        if (sh) { sh.classList.toggle('on', !!d.recall_include_shared); sh.setAttribute('aria-checked', String(!!d.recall_include_shared)); }
        var gl = q('scope-global');
        if (gl) { gl.classList.toggle('on', !!d.recall_include_global); gl.setAttribute('aria-checked', String(!!d.recall_include_global)); }
      }).catch(function () {});
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP — Recall & Memory Behaviour (v3.8.2, live-apply, no restart)
     PUT /api/v3/runtime/config  {retrieval:{top_k,use_cross_encoder},
                                   injection:{enabled,core_block_enabled,
                                              core_block_max_facts}}
  ═══════════════════════════════════════════════════════════ */
  function buildRuntime() {
    var topkWrap = makeRange('rt-topk', 1, 200, 1, 20);
    var topkInp  = topkWrap.querySelector('input');
    var xencSw   = makeSwitch('rt-xenc', true);
    var injSw    = makeSwitch('rt-inj', true);
    var cbSw     = makeSwitch('rt-cb', true);
    var cbWrap   = makeRange('rt-cbmax', 0, 50, 1, 5);
    var cbInp    = cbWrap.querySelector('input');
    var st = makeSt('rt');
    function save() {
      authPut('/api/v3/runtime/config', {
        retrieval: {
          top_k: Number(topkInp.value),
          use_cross_encoder: xencSw.classList.contains('on')
        },
        injection: {
          enabled: injSw.classList.contains('on'),
          core_block_enabled: cbSw.classList.contains('on'),
          core_block_max_facts: Number(cbInp.value)
        }
      }).then(function (r) { return r.json(); })
        .then(function (d) {
          setSt('rt', d.success !== false ? 'Applied' : (d.error || 'Not applied'), d.success !== false);
          if (d.success !== false) loadRuntime();
        })
        .catch(failed('rt', 'Recall settings save', loadRuntime));
    }
    // Ranges save on release ('change'), not each drag tick ('input').
    topkInp.addEventListener('change', save);
    cbInp.addEventListener('change', save);
    xencSw.addEventListener('od-toggle', save);
    injSw.addEventListener('od-toggle', save);
    cbSw.addEventListener('od-toggle', save);
    return makeGrp('Recall & Memory Behaviour (auto-saves, no restart)', 'operations', [
      makeRow('Recall depth', 'Memories each recall considers — higher is more thorough, a little slower', 'retrieval.top_k', topkWrap),
      makeRow('Reranker', 'Re-scores results for quality (uses ~200MB RAM, adds ~200ms)', 'retrieval.use_cross_encoder', xencSw),
      makeRow('Inject memories into prompts', 'Master switch for automatic memory injection', 'injection.enabled', injSw),
      makeRow('Core-memory block', 'Prepend your most important facts to every prompt', 'injection.core_block_enabled', cbSw),
      makeRow('Core-block size', 'Max facts in the core-memory block', 'injection.core_block_max_facts', cbWrap),
      makeRow('Status', '', '', st)
    ]);
  }
  function loadRuntime() {
    fetch('/api/v3/runtime/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.config) return;
        var rt = d.config.retrieval || {}, inj = d.config.injection || {};
        function setRange(id, val) {
          var i = q(id); if (i == null) return;
          i.value = val; var disp = q(id + '-disp'); if (disp) disp.textContent = val;
        }
        function setSw(id, on) {
          var s = q(id); if (!s) return;
          s.classList.toggle('on', !!on); s.setAttribute('aria-checked', String(!!on));
        }
        if (rt.top_k != null) setRange('rt-topk', rt.top_k);
        setSw('rt-xenc', rt.use_cross_encoder);
        setSw('rt-inj', inj.enabled);
        setSw('rt-cb', inj.core_block_enabled);
        if (inj.core_block_max_facts != null) setRange('rt-cbmax', inj.core_block_max_facts);
      }).catch(function () {});
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 5 — Auto-Capture
  ═══════════════════════════════════════════════════════════ */
  function buildCapture() {
    var sw    = makeSwitch('cap-en',  true);
    var swDec = makeSwitch('cap-dec', true);
    var swBug = makeSwitch('cap-bug', true);
    function save() {
      // Merge with full loaded config to preserve server-side fields the UI doesn't expose
      var body = Object.assign({}, _captureConfig, {
        enabled:           sw.classList.contains('on'),
        capture_decisions: swDec.classList.contains('on'),
        capture_bugs:      swBug.classList.contains('on')
      });
      authPut('/api/v3/auto-capture/config', body)
        .then(function () { loadCapture(); }).catch(failed(null, 'Auto-capture save', loadCapture));
    }
    sw.addEventListener('od-toggle', save);
    swDec.addEventListener('od-toggle', save);
    swBug.addEventListener('od-toggle', save);
    return makeGrp('Auto-Capture', 'brain', [
      makeRow('Enable auto-capture', 'Silently remember key facts from agent sessions', 'auto_capture.enabled', sw),
      makeRow('Capture decisions', 'Architecture and design choices', 'auto_capture.capture_decisions', swDec),
      makeRow('Capture bug fixes', 'Error patterns and their resolutions', 'auto_capture.capture_bugs', swBug)
    ]);
  }
  function loadCapture() {
    fetch('/api/v3/auto-capture/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.config) return;
        _captureConfig = Object.assign({}, d.config);
        var c = d.config;
        var map = { 'cap-en': c.enabled, 'cap-dec': c.capture_decisions, 'cap-bug': c.capture_bugs };
        Object.keys(map).forEach(function (k) {
          var e = q(k); if (!e) return;
          e.classList.toggle('on', !!map[k]);
          e.setAttribute('aria-checked', String(!!map[k]));
        });
      }).catch(function () {});
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 6 — Auto-Recall
  ═══════════════════════════════════════════════════════════ */
  function buildRecall() {
    var sw     = makeSwitch('rec-en',   true);
    var swSess = makeSwitch('rec-sess', true);
    function save() {
      authPut('/api/v3/auto-recall/config', {
          enabled:          sw.classList.contains('on'),
          on_session_start: swSess.classList.contains('on')
        }).then(function () { loadRecall(); }).catch(failed(null, 'Auto-recall save', loadRecall));
    }
    sw.addEventListener('od-toggle', save);
    swSess.addEventListener('od-toggle', save);
    return makeGrp('Auto-Recall', 'brain', [
      makeRow('Enable auto-recall', 'Inject relevant memory into agent context', 'auto_recall.enabled', sw),
      makeRow('Recall on session start', 'Run recall automatically when a new session begins', 'auto_recall.on_session_start', swSess)
    ]);
  }
  function loadRecall() {
    fetch('/api/v3/auto-recall/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.config) return;
        var c = d.config;
        var en = q('rec-en');
        if (en) { en.classList.toggle('on', !!c.enabled); en.setAttribute('aria-checked', String(!!c.enabled)); }
        var se = q('rec-sess');
        if (se) { se.classList.toggle('on', !!c.on_session_start); se.setAttribute('aria-checked', String(!!c.on_session_start)); }
      }).catch(function () {});
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 7 — Auto-Invoke (FOK Threshold)
  ═══════════════════════════════════════════════════════════ */
  function buildInvoke() {
    var sw    = makeSwitch('ai-en',   true);
    var range = makeRange('ai-fok', '0', '1', '0.01', '0.12');
    var arSw  = makeSwitch('ai-actr', false);
    var st    = makeSt('ai');
    function save() {
      authPut('/api/v3/auto-invoke/config', {
          enabled:    sw.classList.contains('on'),
          min_score:  parseFloat(q('ai-fok') ? q('ai-fok').value : '0.12'),
          act_r_mode: arSw.classList.contains('on')
        }).then(function (r) { return r.json(); })
        .then(function () { setSt('ai', 'Saved', true); loadInvoke(); })
        .catch(failed('ai', 'Auto-invoke save', loadInvoke));
    }
    sw.addEventListener('od-toggle', save);
    arSw.addEventListener('od-toggle', save);
    range.querySelector('input').addEventListener('change', save);
    return makeGrp('Auto-Invoke (FOK Threshold)', 'optimize', [
      makeRow('Enable auto-invoke', 'Run recall automatically when relevance score is high enough', 'auto_invoke.enabled', sw),
      makeRow('FOK threshold', '0 = always recall · 1 = never recall', 'auto_invoke.min_score', range),
      makeRow('ACT-R mode', 'Biologically-inspired memory activation formula', 'auto_invoke.act_r_mode', arSw),
      makeRow('Status', '', '', st)
    ]);
  }
  function loadInvoke() {
    fetch('/api/v3/auto-invoke/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var en = q('ai-en');
        if (en) { en.classList.toggle('on', !!d.enabled); en.setAttribute('aria-checked', String(!!d.enabled)); }
        var score = String(d.min_score != null ? d.min_score : 0.12);
        var fok = q('ai-fok'); if (fok) fok.value = score;
        var fokD = q('ai-fok-disp'); if (fokD) fokD.textContent = parseFloat(score).toFixed(2);
        var ar = q('ai-actr');
        if (ar) { ar.classList.toggle('on', !!d.act_r_mode); ar.setAttribute('aria-checked', String(!!d.act_r_mode)); }
      }).catch(function () {});
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 8 — Forgetting Engine
     GET/PUT /api/v3/forgetting/config + GET /api/v3/forgetting/stats
  ═══════════════════════════════════════════════════════════ */
  function buildForgetting() {
    var onSw   = makeSwitch('forg-en',  false);
    var immSw  = makeSwitch('forg-imm', true);
    var arcR   = makeRange('forg-arc', '0', '1', '0.01', '0.20');
    var fgtR   = makeRange('forg-fgt', '0', '1', '0.01', '0.05');
    var intInp = makeTin('forg-int', '30', 'number');
    intInp.style.minWidth = '80px'; intInp.style.maxWidth = '100px';
    var statsEl = el('span', { id: P + '-forg-stats', text:'Loading…' },
      { fontSize:'12px', color:'var(--fg-2)', fontFamily:'var(--font-mono)' });
    var kapR = makeRange('forg-kap', '0', '5', '0.1', '2.0');
    var decayEl = window.odDecayPreview ? window.odDecayPreview.build() : el('span');
    var saveBtn = makeBtn('forg-save', 'Save Forgetting', true);
    var st = makeSt('forg');
    var restNote = makeRestartNote('forg');
    saveBtn.addEventListener('click', saveForgetting);
    kapR.querySelector('input').addEventListener('input', function () {
      drawDecay(parseFloat(this.value));
    });
    kapR.querySelector('input').addEventListener('change', saveForgetting);
    onSw.addEventListener('od-toggle',  saveForgetting);
    immSw.addEventListener('od-toggle', saveForgetting);
    arcR.querySelector('input').addEventListener('change', saveForgetting);
    fgtR.querySelector('input').addEventListener('change', saveForgetting);
    intInp.addEventListener('change', saveForgetting);
    return makeGrp('Forgetting Engine', 'health', [
      makeRow('Enable forgetting', 'Gradually fade old or rarely-used memories (recommended on)', 'forgetting.enabled', onSw),
      makeRow('Protect important memories', 'Core memories are immune from being forgotten', 'forgetting.core_memory_immune', immSw),
      makeRow('Archive threshold', 'Memories below this strength move to cold archive', 'forgetting.archive_threshold', arcR),
      makeRow('Forget threshold', 'Memories below this strength are permanently deleted', 'forgetting.forget_threshold', fgtR),
      makeRow('Run every (minutes)', 'How often the forgetting engine runs in the background', 'forgetting.scheduler_interval_minutes', intInp),
      makeRow('Fade untrusted sources faster (\u03ba)',
              'Memories from sources you trust less decay quicker. 0 = every memory fades at the same rate.',
              'forgetting.trust_kappa', kapR),
      makeRow('Decay preview', 'Retention over 3 days at three source-trust levels',
              '', decayEl),
      makeRow('Memory zone stats', '', 'forgetting.stats', statsEl),
      makeRow('Actions', '', '', bRow(saveBtn, st, restNote))
    ]);
  }
  function loadForgetting() {
    fetch('/api/v3/forgetting/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var en = q('forg-en');
        if (en) { en.classList.toggle('on', !!d.enabled); en.setAttribute('aria-checked', String(!!d.enabled)); }
        var imm = q('forg-imm');
        if (imm) { imm.classList.toggle('on', d.core_memory_immune !== false); imm.setAttribute('aria-checked', String(d.core_memory_immune !== false)); }
        var arc = q('forg-arc');
        if (arc) { arc.value = d.archive_threshold || 0.2; var dA = q('forg-arc-disp'); if (dA) dA.textContent = Number(d.archive_threshold || 0.2).toFixed(2); }
        var fgt = q('forg-fgt');
        if (fgt) { fgt.value = d.forget_threshold || 0.05; var dF = q('forg-fgt-disp'); if (dF) dF.textContent = Number(d.forget_threshold || 0.05).toFixed(2); }
        var it = q('forg-int'); if (it) it.value = d.scheduler_interval_minutes || 30;
        // == null, not ||: kappa 0 is a real setting (trust-modulation off) and
        // a falsy check would silently redraw it as the 2.0 default.
        var kap = q('forg-kap');
        var kv = (d.trust_kappa == null) ? 2.0 : Number(d.trust_kappa);
        if (kap) {
          kap.value = kv;
          var dK = q('forg-kap-disp'); if (dK) dK.textContent = kv.toFixed(2);
        }
        drawDecay(kv);
      }).catch(function () {});
    fetch('/api/v3/forgetting/stats').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var e = q('forg-stats'); if (!d || !e) return;
        var parts = [];
        if (d.total != null) parts.push('total: ' + d.total);
        if (d.zones) {
          ['active','warm','cold','archive','forgotten'].forEach(function (z) {
            if (d.zones[z] != null) parts.push(z + ': ' + d.zones[z]);
          });
        }
        e.textContent = parts.join(' · ') || 'No data';
      }).catch(function () {});
  }
  function saveForgetting() {
    setSt('forg', 'Saving…', null);
    showRestartNote('forg', false);
    authPut('/api/v3/forgetting/config', {
      enabled:                    q('forg-en')  ? q('forg-en').classList.contains('on')  : false,
      core_memory_immune:         q('forg-imm') ? q('forg-imm').classList.contains('on') : true,
      archive_threshold:  parseFloat(q('forg-arc') ? q('forg-arc').value : '0.20'),
      forget_threshold:   parseFloat(q('forg-fgt') ? q('forg-fgt').value : '0.05'),
      scheduler_interval_minutes: parseInt(q('forg-int') ? q('forg-int').value : '30') || 30,
      trust_kappa: q('forg-kap') ? parseFloat(q('forg-kap').value) : 2.0
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        setSt('forg', 'Saved', true);
        toast('Forgetting settings saved');
        showRestartNote('forg', !!d.restart_required);
        loadForgetting();
      })
      .catch(failed('forg', 'Forgetting save'));
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 9 — Skill Evolution
     POST /api/evolution/enable|disable  (toggle);  POST /api/evolution/config (backend)
  ═══════════════════════════════════════════════════════════ */
  function buildEvolution() {
    var sw = makeSwitch('evo-en', false);
    var bkSel = makeSel('evo-bk', [
      { v:'auto',      l:'auto (detect)' }, { v:'claude',    l:'claude' },
      { v:'ollama',    l:'ollama' },         { v:'anthropic', l:'anthropic' },
      { v:'openai',    l:'openai' }
    ]);
    var saveBtn = makeBtn('evo-save', 'Save', true);
    var st = makeSt('evo');
    sw.addEventListener('od-toggle', function () {
      var on = sw.classList.contains('on');
      authPost(on ? '/api/evolution/enable' : '/api/evolution/disable', {})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok === false) {
            toast('Failed: ' + (d.error || 'no reason given'), true);
            sw.classList.toggle('on', !on);
            sw.setAttribute('aria-checked', String(!on));
          } else {
            toast(on ? 'Evolution engine enabled' : 'Evolution engine disabled');
            loadEvolution();
          }
        }).catch(failed(null, 'Evolution toggle', function () {
          sw.classList.toggle('on', !on);
          sw.setAttribute('aria-checked', String(!on));
        }));
    });
    saveBtn.addEventListener('click', function () {
      setSt('evo', 'Saving…', null);
      authPost('/api/evolution/config', { backend: q('evo-bk') ? q('evo-bk').value : 'auto' })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d.ok === false) throw new Error(d.error || 'Save failed'); setSt('evo', 'Saved', true); loadEvolution(); })
        .catch(failed('evo', 'Evolution save'));
    });
    return makeGrp('Skill Evolution', 'skill', [
      makeRow('Enable evolution engine', 'Off by default — makes background LLM calls to improve skills', 'evolution.enabled', sw),
      makeRow('Backend', 'auto = cheapest capable model in your current mode', 'evolution.backend', bkSel),
      makeRow('Actions', '', '', bRow(saveBtn, st))
    ]);
  }
  function loadEvolution() {
    fetch('/api/evolution/status').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var sw = q('evo-en');
        if (sw) { sw.classList.toggle('on', !!d.enabled); sw.setAttribute('aria-checked', String(!!d.enabled)); }
        var bk = q('evo-bk');
        if (bk && d.config && d.config.backend_setting) bk.value = d.config.backend_setting;
      }).catch(function () {});
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 10 — Backup
  ═══════════════════════════════════════════════════════════ */
  function buildBackup() {
    var swEn   = makeSwitch('bk-en', true);
    var intSel = makeSel('bk-int', [{ v:'24', l:'Daily (24 h)' }, { v:'168', l:'Weekly (168 h)' }]);
    var maxInp = makeTin('bk-max', '10', 'number');
    maxInp.style.minWidth = '80px'; maxInp.style.maxWidth = '100px';
    var saveBtn = makeBtn('bk-save', 'Save Backup Config', true);
    var nowBtn  = makeBtn('bk-now',  'Backup Now');
    var st = makeSt('bk');
    swEn.addEventListener('od-toggle', saveBackupConfig);
    saveBtn.addEventListener('click', saveBackupConfig);
    nowBtn.addEventListener('click', function () {
      setSt('bk', 'Creating…', null);
      authPost('/api/backup/recovery/create', {}).then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.success) throw new Error(d.message || d.error || 'the backup was not created');
          setSt('bk', 'Done: ' + (d.filename || ''), true);
          toast('Backup created');
          loadBackup();
        }).catch(failed('bk', 'Backup'));
    });
    var cloudBtn = el('button', { type:'button' });
    cloudBtn.className = 'btn sm ghost';
    cloudBtn.textContent = 'Backup & Cloud →';
    cloudBtn.addEventListener('click', function () {
      if (typeof window.odOpenBackupDashboard === 'function') {
        window.odOpenBackupDashboard();
      } else {
        toast('Backup dashboard module not loaded yet', true);
      }
    });
    return makeGrp('Backup', 'health', [
      makeRow('Auto-backup enabled', '', 'backup.enabled', swEn),
      makeRow('Backup interval', '', 'backup.interval_hours', intSel),
      makeRow('Max backups to keep', '', 'backup.max_backups', maxInp),
      makeRow('Actions', '', '', bRow(saveBtn, nowBtn, st)),
      makeRow('Cloud backup', 'Connect GitHub or Google Drive', '', cloudBtn)
    ]);
  }
  function loadBackup() {
    fetch('/api/backup/status').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var en = q('bk-en');
        if (en) { en.classList.toggle('on', d.enabled !== false); en.setAttribute('aria-checked', String(d.enabled !== false)); }
        var it = q('bk-int'); if (it) it.value = (d.interval_hours || 168) <= 24 ? '24' : '168';
        var mx = q('bk-max'); if (mx) mx.value = d.max_backups || 10;
      }).catch(function () {});
  }
  function saveBackupConfig() {
    setSt('bk', 'Saving…', null);
    authPost('/api/backup/configure', {
        interval_hours: parseInt(q('bk-int') ? q('bk-int').value : '168'),
        max_backups:    parseInt(q('bk-max') ? q('bk-max').value : '10'),
        enabled:        q('bk-en') ? q('bk-en').classList.contains('on') : true
      }).then(function (r) { return r.json(); })
      .then(function (d) { if (d.success === false) throw new Error(d.message || 'Save failed'); setSt('bk', 'Saved', true); toast('Backup settings saved'); loadBackup(); })
      .catch(failed('bk', 'Backup save'));
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 11 — Mesh Network
     GET/PUT /api/v3/mesh/config   {enabled}
  ═══════════════════════════════════════════════════════════ */
  function buildMesh() {
    var sw = makeSwitch('mesh-en', false);
    var st = makeSt('mesh');
    var restNote = makeRestartNote('mesh');
    sw.addEventListener('od-toggle', function () {
      showRestartNote('mesh', false);
      authPut('/api/v3/mesh/config', { enabled: sw.classList.contains('on') })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          setSt('mesh', 'Saved', true);
          showRestartNote('mesh', !!d.restart_required);
        })
        .catch(failed('mesh', 'Mesh save', loadMesh));
    });
    return makeGrp('Mesh Network', 'mesh', [
      makeRow('Enable mesh sync', 'Synchronise memories across devices on the same local network', 'mesh.enabled', sw),
      makeRow('Status', '', '', bRow(st, restNote))
    ]);
  }
  function loadMesh() {
    fetch('/api/v3/mesh/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var sw = q('mesh-en');
        if (sw) { sw.classList.toggle('on', !!d.enabled); sw.setAttribute('aria-checked', String(!!d.enabled)); }
      }).catch(function () {});
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 12 — Trust & Source Quality
     GET/PUT /api/v3/trust/config  {use_trust_weighting, trust_first_party, promotion_min_trust}
  ═══════════════════════════════════════════════════════════ */
  function buildTrust() {
    var twSw = makeSwitch('trust-tw', true);
    var fpSw = makeSwitch('trust-fp', false);
    var pmR  = makeRange('trust-pm', '0', '1', '0.01', '0.5');
    var saveBtn = makeBtn('trust-save', 'Save Trust', true);
    var st = makeSt('trust');
    var restNote = makeRestartNote('trust');
    saveBtn.addEventListener('click', saveTrust);
    twSw.addEventListener('od-toggle', saveTrust);
    fpSw.addEventListener('od-toggle', saveTrust);
    pmR.querySelector('input').addEventListener('change', saveTrust);
    return makeGrp('Trust & Source Quality', 'lock', [
      makeRow('Weight recall by trust', 'Higher-trust sources appear first in recall results', 'trust.use_trust_weighting', twSw),
      makeRow('Prefer first-party agents', 'Your own Claude/Gemini sessions rank higher than third-party', 'trust.trust_first_party', fpSw),
      makeRow('Promotion min trust', 'Min trust score required to promote a memory to long-term storage', 'trust.promotion_min_trust', pmR),
      makeRow('Actions', '', '', bRow(saveBtn, st, restNote))
    ]);
  }
  function loadTrust() {
    fetch('/api/v3/trust/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var tw = q('trust-tw');
        if (tw) { tw.classList.toggle('on', d.use_trust_weighting !== false); tw.setAttribute('aria-checked', String(d.use_trust_weighting !== false)); }
        var fp = q('trust-fp');
        if (fp) { fp.classList.toggle('on', !!d.trust_first_party); fp.setAttribute('aria-checked', String(!!d.trust_first_party)); }
        var pm = q('trust-pm');
        if (pm) { pm.value = d.promotion_min_trust != null ? d.promotion_min_trust : 0.5; var pmD = q('trust-pm-disp'); if (pmD) pmD.textContent = Number(d.promotion_min_trust != null ? d.promotion_min_trust : 0.5).toFixed(2); }
      }).catch(function () {});
  }
  function saveTrust() {
    setSt('trust', 'Saving…', null);
    showRestartNote('trust', false);
    authPut('/api/v3/trust/config', {
      use_trust_weighting: q('trust-tw') ? q('trust-tw').classList.contains('on') : true,
      trust_first_party:   q('trust-fp') ? q('trust-fp').classList.contains('on') : false,
      promotion_min_trust: parseFloat(q('trust-pm') ? q('trust-pm').value : '0.5')
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        setSt('trust', 'Saved', true);
        showRestartNote('trust', !!d.restart_required);
        loadTrust();
      })
      .catch(failed('trust', 'Trust save'));
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 12b — Rate Limits (task #47)
     GET/PUT /api/v3/ratelimit  {write, read, window} → applied at runtime
  ═══════════════════════════════════════════════════════════ */
  function buildRateLimit() {
    var wInp = makeTin('rl-write', '100', 'number');
    wInp.style.minWidth = '90px'; wInp.style.maxWidth = '120px';
    var rInp = makeTin('rl-read', '300', 'number');
    rInp.style.minWidth = '90px'; rInp.style.maxWidth = '120px';
    var winInp = makeTin('rl-window', '60', 'number');
    winInp.style.minWidth = '90px'; winInp.style.maxWidth = '120px';
    var lbInfo = el('span', { text: '—' }, { color:'var(--fg-2)', fontSize:'12px' });
    lbInfo.id = P + '-rl-lb';
    var saveBtn = makeBtn('rl-save', 'Save Limits', true);
    var st = makeSt('rl');
    saveBtn.addEventListener('click', saveRateLimit);
    return makeGrp('Rate Limits', 'lock', [
      makeRow('Write limit (per window)', 'Max write requests per client per window — raise this for heavy multi-system load', 'ratelimit.write', wInp),
      makeRow('Read limit (per window)', 'Max read requests per client per window', 'ratelimit.read', rInp),
      makeRow('Window (seconds)', 'Length of the sliding rate-limit window', 'ratelimit.window', winInp),
      makeRow('Loopback (derived)', 'The local dashboard gets a generous multiple of these limits — updates automatically', 'ratelimit.loopback', lbInfo),
      makeRow('Actions', 'Applied immediately — no restart needed', '', bRow(saveBtn, st))
    ]);
  }
  function _rlSetLb(d) {
    var lb = q('rl-lb');
    if (lb && d) {
      lb.textContent = 'write ' + (d.loopback_write != null ? d.loopback_write : '—') +
        ' · read ' + (d.loopback_read != null ? d.loopback_read : '—');
    }
  }
  function loadRateLimit() {
    fetch('/api/v3/ratelimit').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var w = q('rl-write');   if (w) w.value = d.write != null ? d.write : 100;
        var r = q('rl-read');    if (r) r.value = d.read != null ? d.read : 300;
        var win = q('rl-window'); if (win) win.value = d.window != null ? d.window : 60;
        _rlSetLb(d);
      }).catch(function () {});
  }
  function saveRateLimit() {
    setSt('rl', 'Saving…', null);
    authPut('/api/v3/ratelimit', {
      write:  parseInt(q('rl-write')  ? q('rl-write').value  : '100') || 100,
      read:   parseInt(q('rl-read')   ? q('rl-read').value   : '300') || 300,
      window: parseInt(q('rl-window') ? q('rl-window').value : '60')  || 60
    }).then(function (r) { return r.json(); })
      .then(function (d) { setSt('rl', 'Saved', true); _rlSetLb(d); loadRateLimit(); })
      .catch(failed('rl', 'Rate limit save'));
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 13 — Daemon
     GET/PUT /api/v3/daemon/config  {idle_timeout, port, legacy_port, enable_legacy_port}
     PUT returns restart_required
  ═══════════════════════════════════════════════════════════ */
  function buildDaemon() {
    var portInp = makeTin('dmn-port', '8765', 'number');
    portInp.style.minWidth = '90px'; portInp.style.maxWidth = '120px';
    var lpInp = makeTin('dmn-lport', '8767', 'number');
    lpInp.style.minWidth = '90px'; lpInp.style.maxWidth = '120px';
    var lpSw    = makeSwitch('dmn-lpen', true);
    var toInp   = makeTin('dmn-timeout', '0', 'number');
    toInp.style.minWidth = '90px'; toInp.style.maxWidth = '120px';
    var saveBtn  = makeBtn('dmn-save', 'Save Daemon', true);
    var st       = makeSt('dmn');
    var restNote = makeRestartNote('dmn');
    var livePort = document.createElement('span'); livePort.id = 'od-s-dmn-live-port'; livePort.textContent = 'Checking…';
    saveBtn.addEventListener('click', saveDaemon);
    return makeGrp('Daemon (port changes require restart)', 'settings', [
      makeRow('Live API port', 'The port this daemon is using now', '', livePort),
      makeRow('API port after restart', 'Saved setting; an environment port override takes precedence', 'daemon.port', portInp),
      makeRow('Legacy port', 'Backward-compatible API port for older integrations', 'daemon.legacy_port', lpInp),
      makeRow('Enable legacy port', 'Keep the old API port open alongside the main port', 'daemon.enable_legacy_port', lpSw),
      makeRow('Idle timeout (seconds)', '0 = daemon runs forever · >0 = auto-stop after N idle seconds', 'daemon.idle_timeout', toInp),
      makeRow('Actions', '', '', bRow(saveBtn, st, restNote))
    ]);
  }
  function loadDaemon() {
    fetch('/api/v3/daemon/config').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var p = q('dmn-port');    if (p) p.value = d.configured_port || d.port || 8765;
        var live = q('dmn-live-port'); if (live) live.textContent = (window.location.port || d.port || '—') + (d.configured_port && d.configured_port !== d.port ? ' · environment override' : '');
        var l = q('dmn-lport');   if (l) l.value = d.legacy_port || 8767;
        var s = q('dmn-lpen');
        if (s) { s.classList.toggle('on', d.enable_legacy_port !== false); s.setAttribute('aria-checked', String(d.enable_legacy_port !== false)); }
        var t = q('dmn-timeout'); if (t) t.value = d.idle_timeout != null ? d.idle_timeout : 0;
      }).catch(function () {});
  }
  function saveDaemon() {
    setSt('dmn', 'Saving…', null);
    showRestartNote('dmn', false);
    authPut('/api/v3/daemon/config', {
      port:               parseInt(q('dmn-port')    ? q('dmn-port').value    : '8765') || 8765,
      legacy_port:        parseInt(q('dmn-lport')   ? q('dmn-lport').value   : '8767') || 8767,
      enable_legacy_port: q('dmn-lpen') ? q('dmn-lpen').classList.contains('on') : true,
      idle_timeout:       parseInt(q('dmn-timeout') ? q('dmn-timeout').value : '0') || 0
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        setSt('dmn', 'Saved', true);
        toast('Daemon settings saved');
        showRestartNote('dmn', !!d.restart_required);
        loadDaemon();
      }).catch(failed('dmn', 'Daemon save'));
  }

  /* ═══════════════════════════════════════════════════════════
     Answer check (4.1.18) — on-device Laya, hosted Jev, or off.
     Chrome (group card, search row) lives here like every other section;
     the actual controls are rendered by answer-check.js via the
     window.SLMAnswerCheck.renderInto(mount) hook, so the feature's own
     logic (API calls, validation, confirms) stays in its own file.
  ═══════════════════════════════════════════════════════════ */

}());
