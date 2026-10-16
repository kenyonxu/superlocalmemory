// Extracted od-settings.js feature scope; initialized once with the pane helpers.
(function () {
  'use strict';
  var ctx;
  var tokenError, getToken, sendAuthenticated, requireSuccess, authRequest, authPut, authPost, failed, readOk, toast, q, el, setSt, showRestartNote, makeSwitch, makeSel, makeTin, makeSuggest, makeRange, makeBtn, makeSt, bRow, restartDaemonFromNote, makeRestartNote, drawDecay, makeRow, makeGrp, P, ID;
  window.SLMSettingsModels = function (helpers) {
    ctx = helpers;
    ({ tokenError, getToken, sendAuthenticated, requireSuccess, authRequest, authPut, authPost, failed, readOk, toast, q, el, setSt, showRestartNote, makeSwitch, makeSel, makeTin, makeSuggest, makeRange, makeBtn, makeSt, bRow, restartDaemonFromNote, makeRestartNote, drawDecay, makeRow, makeGrp, P, ID } = ctx);
    return { clearDatalist:clearDatalist, addOption:addOption, stripLatest:stripLatest, computeModelHint:computeModelHint, computeEmbHint:computeEmbHint, updateModelHint:updateModelHint, updateEmbHint:updateEmbHint, fillModelSuggestions:fillModelSuggestions, loadModelCatalog:loadModelCatalog, buildMode:buildMode, loadMode:loadMode, saveMode:saveMode, modelSaved:modelSaved, reindexBusy:reindexBusy, testEndpoint:testEndpoint, testMode:testMode, buildEmb:buildEmb, loadEmb:loadEmb, saveEmb:saveEmb, testEmb:testEmb };
  };
    var _modelCatalog = null;
  function clearDatalist(dl) {
    while (dl.firstChild) dl.removeChild(dl.firstChild);
  }
  function addOption(dl, value, text) {
    var opt = document.createElement('option');
    opt.value = value;
    opt.textContent = text; // never innerHTML with catalog data
    dl.appendChild(opt);
  }
  function stripLatest(tag) { return (tag || '').replace(/:latest$/, ''); }
  function computeModelHint(value, provider, cat) {
    if (!cat) return '';
    if (provider === 'ollama') {
      var rec = (cat.local_recommendations || []).filter(function (r) { return r.model === value; })[0];
      if (rec) return rec.reason;
    }
    var hosted = (cat.hosted_llms || []).filter(function (m) { return m.id === value; })[0];
    if (hosted) return hosted.price + ' per million tokens. ' + hosted.advice;
    if (provider === 'ollama' && value) {
      var installed = (cat.ollama && cat.ollama.installed) || [];
      var found = installed.some(function (n) { return stripLatest(n) === stripLatest(value); });
      if (!found) return 'Not installed. Pull it first: ollama pull ' + value;
    }
    return '';
  }
  function computeEmbHint(value, cat) {
    if (!cat || !value) return '';
    var all = (cat.local_embedders || []).concat(cat.hosted_embedders || []);
    var m = all.filter(function (e) { return e.id === value; })[0];
    if (!m) return '';
    return m.advice + (m.dimension ? ' (dimension ' + m.dimension + ')' : '');
  }
  function updateModelHint() {
    var inp = q('model'), hintEl = q('model-hint');
    if (!inp || !hintEl) return;
    var provider = q('provider') ? q('provider').value : 'none';
    hintEl.textContent = computeModelHint(inp.value, provider, _modelCatalog);
  }
  function updateEmbHint() {
    var inp = q('emb-model'), hintEl = q('emb-model-hint');
    if (!inp || !hintEl) return;
    hintEl.textContent = computeEmbHint(inp.value, _modelCatalog);
    var entries = _modelCatalog ? (_modelCatalog.local_embedders || []).concat(_modelCatalog.hosted_embedders || []) : [];
    var entry = entries.find(function (item) { return item.id === inp.value; });
    if (entry && entry.dimension && q('emb-dim')) q('emb-dim').value = entry.dimension;
  }
  function fillModelSuggestions() {
    var cat = _modelCatalog;
    var modelDl = q('model-list'), embDl = q('emb-model-list');
    if (modelDl) clearDatalist(modelDl);
    if (embDl) clearDatalist(embDl);
    var provider = q('provider') ? q('provider').value : 'none';
    if (cat) {
      if (provider === 'ollama' && modelDl) {
        (cat.local_recommendations || []).forEach(function (r) { addOption(modelDl, r.model, r.reason); });
      } else if (provider === 'openrouter' && modelDl) {
        (cat.hosted_llms || []).forEach(function (m) {
          addOption(modelDl, m.id, m.label + ' — ' + m.price + ' — ' + m.advice);
        });
      }
      if (embDl) {
        (cat.local_embedders || []).concat(cat.hosted_embedders || []).forEach(function (m) {
          addOption(embDl, m.id, m.label + ' — ' + m.advice);
        });
      }
      var modelInp = q('model');
      if (modelInp && provider === 'ollama' && !modelInp.value && cat.best_local_llm) {
        modelInp.value = cat.best_local_llm;
      }
    }
    updateModelHint();
    updateEmbHint();
  }
  function loadModelCatalog() {
    return fetch('/api/v3/models/catalog').then(function (r) {
      if (!r.ok) throw new Error('models catalog unavailable');
      return r.json();
    }).then(function (d) {
      _modelCatalog = d || null;
    }).catch(function () {
      _modelCatalog = null;
    }).then(fillModelSuggestions);
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 1 — Operating Mode & LLM
  ═══════════════════════════════════════════════════════════ */
  function buildMode() {
    var modeSel = makeSel('mode', [
      { v:'a', l:'Mode A — Local Guardian (zero cloud)' },
      { v:'b', l:'Mode B — Smart Local (local model, Ollama default)' },
      { v:'c', l:'Mode C — Full Power (your endpoint or cloud)' }
    ]);
    var provSel = makeSel('provider', [
      { v:'none', l:'None (Mode A)' }, { v:'ollama', l:'Ollama (local)' },
      { v:'openrouter', l:'OpenRouter' }, { v:'openai', l:'OpenAI' },
      { v:'anthropic', l:'Anthropic' }
    ]);
    var modelInp = makeSuggest('model', 'e.g. gemma3:4b');
    var modelHint = el('span', { id: P + '-model-hint' }, { fontSize:'12px', color:'var(--fg-2)' });
    var keyInp   = makeTin('apikey', 'sk-... or your key', 'password');
    var epInp    = makeTin('endpoint', 'http://localhost:11434');
    var saveBtn  = makeBtn('mode-save', 'Save Mode', true);
    var testBtn  = makeBtn('mode-test', 'Test');
    var st = makeSt('mode');
    modeSel.addEventListener('change', function () {
      var a = this.value === 'a';
      [provSel, modelInp, keyInp, epInp].forEach(function (i) { i.disabled = a; });
    });
    provSel.addEventListener('change', fillModelSuggestions);
    modelInp.addEventListener('input', updateModelHint);
    saveBtn.addEventListener('click', saveMode);
    testBtn.addEventListener('click', testMode);
    return makeGrp('Operating Mode & LLM Provider', 'operations', [
      makeRow('Mode', 'Select operating mode', 'mode', modeSel),
      makeRow('Provider', 'LLM backend (Mode B/C)', 'llm.provider', provSel),
      makeRow('Model', '', 'llm.model', modelInp),
      makeRow('', '', '', modelHint),
      makeRow('API Key', 'Stored locally, never sent to Qualixar', 'llm.api_key', keyInp),
      makeRow('Base URL / Endpoint', 'Custom endpoint for Ollama, LM Studio', 'llm.base_url', epInp),
      makeRow('Actions', '', '', bRow(saveBtn, testBtn, st))
    ]);
  }
  function loadMode() {
    fetch('/api/v3/mode').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var m = q('mode');
        if (m) { m.value = d.mode || 'a'; m.dispatchEvent(new Event('change')); }
        var p = q('provider'); if (p) p.value = d.provider || 'none';
        var mi = q('model'); if (mi) mi.value = d.model || '';
        var k = q('apikey');
        if (k) k.placeholder = d.has_key ? '(key saved — enter new to replace)' : 'sk-...';
        // GET /mode deliberately returns only a redacted host.  Showing that
        // host as the editable value would turn a passive reload + save into a
        // destructive replacement of the real URL, so it is display-only.
        var ep = q('endpoint');
        if (ep) {
          ep.value = '';
          ep.placeholder = d.endpoint || 'http://localhost:11434';
        }
      }).catch(function () {}).then(fillModelSuggestions);
  }
  function saveMode() {
    setSt('mode', 'Saving…', null);
    var body = { mode: q('mode') ? q('mode').value : 'a',
                 provider: q('provider') ? q('provider').value : 'none',
                 model: q('model') ? q('model').value : '' };
    var k = q('apikey') ? q('apikey').value : '';
    var ep = q('endpoint') ? q('endpoint').value : '';
    if (k)  { body.api_key = k; }
    if (ep) { body.base_url = ep; body.endpoint = ep; }
    authPost('/api/v3/mode/set', body).then(modelSaved('mode', 'Mode', loadMode))
      .catch(failed('mode', 'Mode save', reindexBusy));
  }
  /** 202: the model switch was queued as a background re-index — started, not done. */
  function modelSaved(id, label, reload) {
    return function (r) {
      return r.json().then(function (d) {
        if (r.status === 202) {
          setSt(id, 'Started: ' + (d.detail || 're-indexing in the background'), null);
          toast(label + ': re-indexing in the background');
          if (window.odReindex) window.odReindex.track(d);
        } else { setSt(id, d.message || 'Saved', d.needs_reindex ? null : true); toast(label + ' saved'); }
        reload();
      });
    };
  }
  function reindexBusy(e) {
    if (e && e.status === 409 && e.body && e.body.error === 'reindex_running' && window.odReindex) {
      window.odReindex.conflict(e.body);
    }
  }
  function testEndpoint(id, url, body) {
    fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) })
      .then(readOk).then(function (d) {
        setSt(id, d.success ? (d.message || 'OK') : 'Failed: ' + (d.error || 'no reason given'), !!d.success);
      }).catch(failed(id, 'Test'));
  }
  function testMode() {
    setSt('mode', 'Testing…', null);
    var body = { provider: q('provider') ? q('provider').value : '',
                 model:    q('model')    ? q('model').value    : '' };
    var k  = q('apikey')   ? q('apikey').value   : '';
    var ep = q('endpoint') ? q('endpoint').value : '';
    if (k)  { body.api_key = k; }
    if (ep) { body.base_url = ep; body.endpoint = ep; }
    testEndpoint('mode', '/api/v3/provider/test', body);
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 2 — Embeddings & Retrieval
  ═══════════════════════════════════════════════════════════ */
  function buildEmb() {
    var provSel = makeSel('emb-prov', [
      { v:'default', l:'Local model (automatic provider)' },
      { v:'openai',  l:'Custom OpenAI-compatible endpoint' }
    ]);
    var modelInp = makeSuggest('emb-model', 'e.g. Qwen3-Embedding');
    var modelHint = el('span', { id: P + '-emb-model-hint' }, { fontSize:'12px', color:'var(--fg-2)' });
    var dimInp   = makeTin('emb-dim', '768', 'number');
    dimInp.style.minWidth = '80px'; dimInp.style.maxWidth = '100px';
    var epInp  = makeTin('emb-endpoint', 'http://localhost:8045/v1/embeddings');
    var keyInp = makeTin('emb-key', 'optional key', 'password');
    var infoEl = el('span', { id: P + '-emb-info' }, { fontSize:'12px', color:'var(--fg-2)' });
    var saveBtn = makeBtn('emb-save', 'Save Embedding', true);
    var testBtn = makeBtn('emb-test', 'Test');
    var st = makeSt('emb');
    provSel.addEventListener('change', function () {
      var c = this.value === 'openai';
      modelInp.disabled = false;
      [epInp, keyInp].forEach(function (i) { i.disabled = !c; });
    });
    modelInp.addEventListener('input', updateEmbHint);
    saveBtn.addEventListener('click', saveEmb);
    testBtn.addEventListener('click', testEmb);
    return makeGrp('Embeddings & Retrieval', 'memories', [
      makeRow('Provider', 'Local model is private and free', 'embedding.provider', provSel),
      makeRow('Model Name', 'Choose a local model or an endpoint model', 'embedding.model_name', modelInp),
      makeRow('', '', '', modelHint),
      makeRow('Dimensions', '768 local · 1024/3072 cloud', 'embedding.dimension', dimInp),
      makeRow('Endpoint', 'OpenAI-compatible URL', 'embedding.api_endpoint', epInp),
      makeRow('API Key', 'optional', 'embedding.api_key', keyInp),
      makeRow('Info', '', '', infoEl),
      makeRow('Actions', 'Save switches the selected model; Test checks the active model.', '', bRow(saveBtn, testBtn, st)),
      makeRow('Re-index', 'A model change re-embeds every memory in the background', '',
              window.odReindex ? window.odReindex.panel(authPost) : el('span')),
      makeRow('Upgrade', 'Move your memories to the newer memory engine', '',
              window.odEngineUpgrade ? window.odEngineUpgrade.panel(authPost) : el('span'))
    ]);
  }
  function loadEmb() {
    fetch('/api/v3/embedding/config').then(function (r) {
      if (!r.ok) throw new Error('embedding config unavailable');
      return r.json();
    })
      .then(function (d) {
        if (!d) throw new Error('embedding config unavailable');
        var p = q('emb-prov');
        if (p) { p.value = d.is_openai_compatible ? 'openai' : 'default'; p.dispatchEvent(new Event('change')); }
        var m = q('emb-model'); if (m) m.value = d.model_name || '';
        var dim = q('emb-dim'); if (dim) dim.value = d.dimension || '';
        var ep = q('emb-endpoint'); if (ep) ep.value = d.api_endpoint || '';
        var inf = q('emb-info');
        if (inf) inf.textContent = 'Current: ' + (d.model_name || 'nomic-embed-text') + ' (' + (d.dimension || 768) + 'd)';
      }).catch(function () {
        var inf = q('emb-info');
        if (inf) inf.textContent = 'Embedding configuration unavailable. Check daemon health and retry.';
      }).then(fillModelSuggestions);
    if (window.odReindex) window.odReindex.refresh();
    if (window.odEngineUpgrade) window.odEngineUpgrade.refresh();
  }
  function saveEmb() {
    setSt('emb', 'Saving…', null);
    var prov = q('emb-prov') ? q('emb-prov').value : 'default';
    var body = prov === 'openai'
      ? { embedding_provider:'openai',
          embedding_endpoint:  q('emb-endpoint') ? q('emb-endpoint').value : '',
          embedding_model:     q('emb-model')    ? q('emb-model').value    : '',
          embedding_dimension: parseInt(q('emb-dim') ? q('emb-dim').value : '768') || 768,
          embedding_key:       q('emb-key')      ? q('emb-key').value      : '' }
      : { embedding_provider:'', embedding_model:q('emb-model').value, embedding_dimension:parseInt(q('emb-dim').value,10) || 0 };
    authPost('/api/v3/mode/set', body).then(modelSaved('emb', 'Embedding config', loadEmb))
      .catch(failed('emb', 'Embedding save', reindexBusy));
  }
  function testEmb() {
    setSt('emb', 'Testing…', null);
    var ep = q('emb-endpoint') ? q('emb-endpoint').value : '';
    if (q('emb-prov').value === 'default') {
      testEndpoint('emb', '/api/dashboard/embedding-test', {});
      return;
    }
    if (!ep) { setSt('emb', 'Enter endpoint first', null); return; }
    testEndpoint('emb', '/api/v3/embedding/test', { api_endpoint: ep,
      model_name: q('emb-model') ? q('emb-model').value : '',
      api_key:    q('emb-key')   ? q('emb-key').value   : '' });
  }

  /* ═══════════════════════════════════════════════════════════
     GROUP 3 — Storage Backends
     GET/PUT /api/v3/storage/config  — PUT returns restart_required
  ═══════════════════════════════════════════════════════════ */

}());
