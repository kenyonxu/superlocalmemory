// od-memories.js — Memories · Timeline · Knowledge Clusters (OD design port)
// Render: #memories-pane  |  Public: window.odRenderMemories(container)
// CSP-safe: data-od-act event delegation. XSS-safe: _esc() on all API values.
// nosemgrep: innerHTML — all dynamic values escaped via _esc()
//
// Endpoints (live daemon 127.0.0.1:8765):
//   GET /api/memories?limit=N&offset=N[&kind=X][&category=X]
//       → { memories[{id,memory_id,content,category,memory_kind,
//                      memory_kind_label,memory_kind_state,importance(0-1),
//                      access_count,created_at,project_name}], total }
//       `kind` filters on the DISPLAYED kind (nine memory_kind values,
//       e.g. "decision"), the same precedence `slm list`/recall use
//       (core/kind_query.py). `category` still filters on the raw legacy
//       fact_type for callers that want that instead.
//   GET /api/memories/kind-counts[?scope=X] → { counts:{<kind>:n,...}, truncated }
//   GET /api/memories/{id}/facts → { facts[{fact_type,content}] }
//   GET /api/v3/timeline/?range=Xd&group_by=category&limit=N
//       → { events[{id,timestamp,category}] }
//   GET /api/clusters → { clusters[{cluster_id,member_count,categories,summary}] }
//   GET /api/clusters/{id}?limit=N → { members[], summary }
// TODO: /api/memories/{id}/score-breakdown — Semantic/BM25/Entity/Temporal not yet exposed.
(function () {
  'use strict';

  // ── Utilities ───────────────────────────────────────────────────────────────

  function _esc(s) {
    // Quote-safe: the result is used inside attribute values too.
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _fmt(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString();
  }

  function _ago(iso) {
    if (!iso) return '';
    var s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 0) s = 0;
    if (s < 60)    return Math.floor(s) + 's ago';
    if (s < 3600)  return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  // importance arrives as 0–1 float; display as X/10 integer
  function _imp(raw) { return Math.round((parseFloat(raw) || 0) * 10); }

  // importance (0–1) → badge class for Score column
  function _scoreCls(raw) {
    var v = parseFloat(raw) || 0;
    return v >= 0.7 ? 'ok' : v >= 0.4 ? 'warn' : 'danger';
  }

  // memory_kind → badge class. Only 6 .badge modifier colors exist
  // (design-system.css .badge.ok/.warn/.danger/.violet/.cyan/.neutral) for
  // the nine kinds, so two pairs share a color; the label text (not the
  // color) is what distinguishes them.
  function _catCls(kind) {
    var MAP = { semantic: 'violet', episodic: 'cyan', status: 'neutral',
                opinion: 'warn', rule: 'violet', decision: 'ok',
                procedure: 'cyan', prospective: 'ok', correction: 'danger' };
    return MAP[String(kind).toLowerCase()] || 'neutral';
  }

  // Normalize /api/search result → memory shape (fact_id→id, score→importance)
  // UX-12: one memory shape for browse and search. Kind, kind label/state,
  // scope, sharing and project are kept, never defaulted: a shared decision
  // found by search must not open as "personal" with the Scope control preset.
  function _normSearch(r) {
    return {
      id: r.fact_id || r.memory_id || '', memory_id: r.memory_id || '',
      // 4.1.25: a memory that does not answer the search exactly says so.
      content: (r.tier === 'related' ? '[related] ' : (r.weak ? '[weak] ' : '')) +
        (r.content || ''), category: r.category || r.fact_type || 'semantic',
      memory_kind: r.memory_kind || '', memory_kind_label: r.memory_kind_label || '',
      memory_kind_state: r.memory_kind_state || '',
      project_name: r.project_name || '', scope: r.scope || '',
      shared_with: r.shared_with || [],
      importance: r.score || r.confidence || 0, created_at: r.created_at || '',
      access_count: r.access_count || 0
    };
  }
  // shared_with is stored as a JSON array string; show it comma-separated.
  function _sharedToStr(v) {
    if (!v) return '';
    if (Array.isArray(v)) return v.join(', ');
    try { var a = JSON.parse(v); return Array.isArray(a) ? a.join(', ') : ''; }
    catch (e) { return ''; }
  }

  // Write-auth token: reuses dashboard.js closure when available, else fails gracefully
  function _getMutToken() { return typeof window.dashboardInstallToken==='function'?window.dashboardInstallToken():Promise.resolve(''); }

  // ── CSS (injected once) ─────────────────────────────────────────────────────

  var _cssInjected = false;
  function _injectCSS() {
    if (_cssInjected) return;
    _cssInjected = true;
    var s = document.createElement('style');
    s.dataset.odModule = 'memories';
    s.textContent =
      '.od-drawer{position:fixed;top:0;right:0;height:100vh;width:420px;max-width:92vw;' +
      'background:var(--card);border-left:1px solid var(--border);box-shadow:var(--sh-lg);' +
      'transform:translateX(100%);transition:transform .28s cubic-bezier(.22,1,.36,1);' +
      'z-index:60;overflow-y:auto;padding:24px;}' +
      '.od-drawer.open{transform:none;}' +
      '.od-drawer-scrim{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:59;' +
      'opacity:0;pointer-events:none;transition:opacity .25s;}' +
      '.od-drawer-scrim.on{opacity:1;pointer-events:auto;}' +
      '.od-prov{padding:14px;border-radius:var(--r-md);background:var(--card-2);' +
      'border:1px solid var(--border);margin-top:12px;}';
    document.head.appendChild(s);
  }

  // ── Module state ────────────────────────────────────────────────────────────

  var _st = {
    rootId:    null,
    // Must match the pane carrying `active` in _scaffold(). Recall Lab leads.
    activeTab: 'recall',
    category:  null,   // null = all; string = filtered category
    sort:      'created',
    page:      0,
    pageSize:  50,
    total:     0,
    memories:  [],
    catCounts: {},     // pre-fetched per-category totals
    tlRange:   '30d',
    cluLoaded: false,  // reset in render() to avoid stale state on re-entry
    searchQ:   null,
    requestSeq: 0,
  };

  // The nine memory kinds (storage/memory_kinds.py MemoryKind) and their
  // human-facing labels (storage/memory_kinds.py LABELS). Kept in this
  // fixed order (same as the Python enum) rather than sorted by count, so
  // the chip bar does not reshuffle every time counts change.
  var KNOWN_KINDS = [
    { kind: 'semantic',    label: 'Fact' },
    { kind: 'episodic',    label: 'Event' },
    { kind: 'status',      label: 'Current state' },
    { kind: 'opinion',     label: 'Preference or view' },
    { kind: 'rule',        label: 'Standing rule' },
    { kind: 'decision',    label: 'Decision' },
    { kind: 'procedure',   label: 'How-to' },
    { kind: 'prospective', label: 'Plan or to-do' },
    { kind: 'correction',  label: 'Correction' },
  ];

  // ── Main entry ──────────────────────────────────────────────────────────────

  function render(container) {
    if (!container) return;
    _injectCSS();
    var id = 'od-mem-' + Math.random().toString(36).slice(2, 8);
    // activeTab is reset explicitly: _scaffold always emits the Recall pane as the
    // active one, so leaving a stale value here (from a tab click before the user
    // navigated away and back) would desync state from what is actually on screen.
    _st = Object.assign({}, _st, {
      rootId: id, page: 0, category: null, sort: 'created', activeTab: 'recall',
      memories: [], catCounts: {}, cluLoaded: false, searchQ: null, requestSeq: 0,
    });
    // Clear per-id timeline cache for this render instance
    _tlLoaded = {};
    container.innerHTML = _scaffold(id);
    _wire(container, id);
    // Fire initial loads in parallel
    _loadMem(id);
    _loadCatCounts(id);
  }

  // ── Scaffold HTML ────────────────────────────────────────────────────────────

  function _scaffold(id) {
    return (
      '<div id="' + id + '">' +
        '<div class="page-head">' +
          '<h2>Everything you\'ve remembered</h2>' +
          '<p id="' + id + '-sub" style="color:var(--fg-2);margin-top:5px">Loading…</p>' +
        '</div>' +
        '<div class="tabs" id="' + id + '-tabs">' +
          '<button class="tab active" data-od-act="tab" data-tab="recall">Recall Lab</button>' +
          '<button class="tab" data-od-act="tab" data-tab="summary">Summaries</button>' +
          '<button class="tab" data-od-act="tab" data-tab="views">Saved views</button>' +
          '<button class="tab" data-od-act="tab" data-tab="all">' +
            'All memories <span class="cnt" id="' + id + '-cnt-all">…</span></button>' +
          '<button class="tab" data-od-act="tab" data-tab="timeline">Creation timeline</button>' +
          '<button class="tab" data-od-act="tab" data-tab="clusters">' +
            'Knowledge clusters <span class="cnt" id="' + id + '-cnt-clusters">…</span></button>' +
        '</div>' +
        '<div class="tabpane" id="' + id + '-pane-all">' + _allScaffold(id) + '</div>' +
        '<div class="tabpane" id="' + id + '-pane-timeline">' + _tlScaffold(id) + '</div>' +
        '<div class="tabpane" id="' + id + '-pane-clusters">' +
          '<div class="launch-grid" id="' + id + '-clu-grid">' +
            _loading('Loading clusters…') +
          '</div>' +
        '</div>' +
        '<div class="tabpane" id="' + id + '-pane-views">' +
          (window.ODViews ? window.ODViews.pane(id) : '') +
        '</div>' +
        _summaryScaffold(id) + _recallScaffold(id) +
      '</div>' +
          '</div>' +
          '<div id="recall-lab-meta" ' +
            'style="font-size:12px;color:var(--fg-2);margin-bottom:8px"></div>' +
          '<div id="recall-lab-results">' + _recallStarter() + '</div>' +
        '</div>' +
      '</div>'
    );
  }


  function _summaryScaffold(id) {
    return (
      '<div class="tabpane" id="' + id + '-pane-summary" style="padding-top:12px">' +
          '<p style="font-size:13px;color:var(--fg-2);margin-bottom:12px">'+
            'A readable view of what you recorded. Every summary states how much '+
            'of the underlying data it could actually cover.' +
          '</p>' +
          (window.ODSummaries ? window.ODSummaries.controls(id) : '') +
          '<div id="' + id + '-kover" style="margin-top:26px;' +
            'border-top:1px solid var(--border);padding-top:20px">' +
            _loading('Loading what your memory knows…') +
          '</div>' +
        '</div>'
    );
  }

  function _recallScaffold(id) {
    return (
      '<div class="tabpane active" id="' + id + '-pane-recall" style="padding-top:12px">' +
          '<div style="margin-bottom:14px">' +
            '<p style="font-size:13px;color:var(--fg-2);margin-bottom:10px">' +
              'Ask your memory a question and watch it answer. This shows you the ' +
              'same result an AI assistant gets — plus <em>why</em> each memory was ' +
              'chosen, how strongly it matched, and how long the lookup took.' +
            '</p>' +
            '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
              '<input id="recall-lab-query" placeholder="Enter recall query…" autocomplete="off" ' +
                'style="flex:1;min-width:200px;padding:8px 12px;' +
                  'border:1px solid var(--border);border-radius:6px;font-size:13px;' +
                  'background:var(--bg-2);color:var(--fg)">' +
              '<select id="recall-lab-per-page" aria-label="Results per page" ' +
                'style="padding:7px 10px;border:1px solid var(--border);border-radius:6px;' +
                  'font-size:13px;background:var(--bg-2);color:var(--fg)">' +
                '<option value="5">5</option>' +
                '<option value="10" selected>10</option>' +
                '<option value="20">20</option>' +
                '<option value="50">50</option>' +
              '</select>' +
              '<button id="recall-lab-search" class="btn primary" ' +
                'style="white-space:nowrap">Run Trace</button>'
    );
  }

  function _allScaffold(id) {
    return (
      '<div style="margin-bottom:16px;display:flex;gap:8px;align-items:center">' +
        '<input data-od-act="search" placeholder="Search all memories…" autocomplete="off" ' +
          'style="flex:1;padding:8px 12px;border:1px solid var(--border);' +
          'border-radius:var(--r-md);background:var(--card-2);color:var(--fg);' +
          'font-size:13.5px;outline:none">' +
        '<label title="When nothing answers the search exactly, list the closest matches anyway" ' +
          'style="display:flex;gap:4px;align-items:center;font-size:12.5px;color:var(--fg-2);' +
          'white-space:nowrap"><input type="checkbox" data-od-act="showweak">' +
          'Show closest matches</label>' +
        '<select data-od-act="window" title="Limit search to a time window" ' +
          'style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--r-md);' +
          'background:var(--card-2);color:var(--fg);font-size:13px;outline:none">' +
          '<option value="">Any time</option>' +
          '<option value="24h">Last 24h</option>' +
          '<option value="7d">Last 7 days</option>' +
          '<option value="30d">Last 30 days</option>' +
          '<option value="90d">Last 90 days</option>' +
          '<option value="1y">Last year</option>' +
        '</select>' +
      '</div>' +
      // Filter bar: category chips (populated after cat-count fetch) + sort seg
      '<div id="' + id + '-cats" ' +
        'style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center">' +
        '<div class="chip on" data-od-act="cat" data-cat="">All kinds</div>' +
        '<div style="flex:1"></div>' +
        // Scope view: Mine (this profile) / Shared with me / Global / Everything
        '<div class="seg" title="Which memories to show">' +
          '<button class="active" data-od-act="scope-view" data-scope="mine">Mine</button>' +
          '<button data-od-act="scope-view" data-scope="shared">Shared</button>' +
          '<button data-od-act="scope-view" data-scope="global">Global</button>' +
          '<button data-od-act="scope-view" data-scope="all">All</button>' +
        '</div>' +
        '<div class="seg">' +
          '<button class="active" data-od-act="sort" data-sort="created">Newest</button>' +
          '<button data-od-act="sort" data-sort="score">Score</button>' +
          '<button data-od-act="sort" data-sort="importance">Importance</button>' +
        '</div>' +
      '</div>' +
      '<div class="card" id="' + id + '-tbl-wrap">' + _loading('Loading memories…') + '</div>' +
      '<div id="' + id + '-pg" style="margin-top:10px"></div>'
    );
  }

  function _tlScaffold(id) {
    return (
      '<div class="card">' +
        '<div class="card-head">' +
          '<h3>Memory creation timeline</h3>' +
          '<span class="sub" id="' + id + '-tl-sub">facts stored per day</span>' +
          '<div style="flex:1"></div>' +
          '<div class="seg">' +
            '<button data-od-act="tl-range" data-range="7d">7d</button>' +
            '<button class="active" data-od-act="tl-range" data-range="30d">30d</button>' +
          '</div>' +
        '</div>' +
        '<div class="card-pad">' +
          '<div class="bars" id="' + id + '-bars" style="height:120px">' +
            _loading('Loading timeline…') +
          '</div>' +
          '<div style="display:flex;justify-content:space-between;margin-top:10px;' +
            'font-size:11.5px;color:var(--fg-2)">' +
            '<span id="' + id + '-tl-start"></span><span>today</span>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="card" style="margin-top:16px">' +
        '<div class="card-head"><h3>By fact type</h3></div>' +
        '<div class="card-pad" id="' + id + '-ftypes">' + _loading('Loading breakdown…') + '</div>' +
      '</div>'
    );
  }

  function _loading(msg) {
    return '<div style="padding:40px;text-align:center;color:var(--fg-2);font-size:13px">' +
      _esc(msg) + '</div>';
  }

  /* Starter state for Recall Lab.
   *
   * Recall Lab is the landing pane, so this is the first thing most people see in
   * the dashboard. It has to answer "what is this and what do I type" without
   * assuming the reader knows what a recall trace is.
   *
   * The examples are deliberately static. Seeding them from real memories was the
   * first idea and it is worse: stored content is things like
   * "[claude][SLM 4.0.8 CHECKPOINT] RELEASE STATE: local", which teaches exactly
   * the wrong query shape. These teach the shape; the store answers.
   */
  var RECALL_EGS = [
    'what did I decide about the database',
    'what am I working on',
    'what problems have I hit before',
  ];

  function _recallStarter() {
    var chips = RECALL_EGS.map(function (q) {
      return '<button class="tab" data-od-act="recall-eg" data-q="' + _esc(q) + '">' +
        _esc(q) + '</button>';
    }).join('');
    return (
      '<div id="od-recall-starter" style="border:1px dashed var(--border);' +
        'border-radius:var(--r-md);padding:22px;color:var(--fg-2);font-size:13px">' +
        '<div style="font-weight:600;color:var(--fg);margin-bottom:6px">' +
          'Ask a question above to search your memory</div>' +
        '<p style="margin:0 0 14px;line-height:1.55">' +
          'It searches by meaning, not keywords — asking “what database are we using” ' +
          'can surface a memory that says “switched to Postgres”, even though the two ' +
          'share no words. Try one:' +
        '</p>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap">' + chips + '</div>' +
      '</div>'
    );
  }

  // ── Tab switching ────────────────────────────────────────────────────────────

  function _switchTab(id, tab) {
    _st = Object.assign({}, _st, { activeTab: tab });
    var root = document.getElementById(id);
    if (!root) return;
    root.querySelectorAll('#' + id + '-tabs .tab').forEach(function (b) {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    root.querySelectorAll('.tabpane').forEach(function (p) { p.classList.remove('active'); });
    var pane = document.getElementById(id + '-pane-' + tab);
    if (pane) pane.classList.add('active');
    if (tab === 'timeline') _loadTimeline(id);
    if (tab === 'clusters')  _loadClusters(id);
    // The Summaries and Saved views panes live in od-summaries.js / od-views.js.
    if (tab === 'summary') {
      if (window.ODSummaries) window.ODSummaries.onShow(id);
      _loadKnowledgeOverview(id);
    }
    if (tab === 'views' && window.ODViews) window.ODViews.onShow(id);
  }

  // ── Kind counts (pre-fetch real totals) ──────────────────────────────────────

  function _loadCatCounts(id) {
    // One request classifies every visible row by its DISPLAYED kind
    // (server-side kind_fields, same precedence as `slm list`/recall) —
    // not nine (or, before 4.1.21, five) separate ?category=X round trips,
    // which could only ever count legacy fact_type buckets.
    fetch('/api/memories/kind-counts')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        _st = Object.assign({}, _st, { catCounts: d.counts || {} });
        _rebuildCatBar(id);
      })
      .catch(function () {
        _st = Object.assign({}, _st, { catCounts: {} });
        _rebuildCatBar(id);
      });
  }

  // Rebuild the full filter bar (chips + sort) from current _st
  function _rebuildCatBar(id) {
    var bar = document.getElementById(id + '-cats');
    if (!bar) return;
    var active = _st.category || '';
    var html = '<div class="chip' + (!active ? ' on' : '') +
      '" data-od-act="cat" data-cat="">All kinds</div>';
    KNOWN_KINDS.forEach(function (k) {
      var count = _st.catCounts[k.kind] || 0;
      if (count <= 0) return;
      html += '<div class="chip' + (_st.category === k.kind ? ' on' : '') +
        '" data-od-act="cat" data-cat="' + _esc(k.kind) + '">' +
        _esc(k.label) + ' <span class="cnt">' +
        count.toLocaleString() + '</span></div>';
    });
    var sv = _st.scopeView || 'mine';
    html += '<div style="flex:1"></div>' +
      '<div class="seg" title="Which memories to show">' +
        ['mine', 'shared', 'global', 'all'].map(function (s) {
          var label = { mine: 'Mine', shared: 'Shared', global: 'Global', all: 'All' }[s];
          return '<button class="' + (sv === s ? 'active' : '') +
            '" data-od-act="scope-view" data-scope="' + s + '">' + label + '</button>';
        }).join('') +
      '</div>' +
      '<div class="seg">' +
        '<button class="' + (_st.sort === 'created' ? 'active' : '') +
          '" data-od-act="sort" data-sort="created">Newest</button>' +
        '<button class="' + (_st.sort === 'score' ? 'active' : '') +
          '" data-od-act="sort" data-sort="score">Score</button>' +
        '<button class="' + (_st.sort === 'importance' ? 'active' : '') +
          '" data-od-act="sort" data-sort="importance">Importance</button>' +
      '</div>';
    bar.innerHTML = html;
  }

  // ── Memories fetch & render ──────────────────────────────────────────────────

  function _loadMem(id) {
    var url = '/api/memories?limit=' + _st.pageSize +
      '&offset=' + (_st.page * _st.pageSize);
    if (_st.category) url += '&kind=' + encodeURIComponent(_st.category);
    if (_st.scopeView && _st.scopeView !== 'mine') {
      url += '&scope=' + encodeURIComponent(_st.scopeView);
    }
    var requestSeq = _st.requestSeq + 1;
    _st = Object.assign({}, _st, { requestSeq: requestSeq });
    var wrap = document.getElementById(id + '-tbl-wrap');
    if (wrap) wrap.innerHTML = _loading('Loading memories…');

    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (_st.rootId !== id || _st.requestSeq !== requestSeq) return;
        _st = Object.assign({}, _st, { memories: d.memories || [], total: d.total || 0 });
        _renderTable(id, d);
      })
      .catch(function (err) {
        if (_st.rootId !== id || _st.requestSeq !== requestSeq) return;
        var w = document.getElementById(id + '-tbl-wrap');
        if (w) w.innerHTML = '<div class="card-pad" style="color:var(--danger);text-align:center">' +
          'Failed to load memories: ' + _esc(err.message) + '</div>';
      });
  }

  function _renderTable(id, d) {
    var mems   = d.memories || [];
    var total  = d.total || 0;
    var wrap   = document.getElementById(id + '-tbl-wrap');
    var cntEl  = document.getElementById(id + '-cnt-all');
    var subEl  = document.getElementById(id + '-sub');

    if (cntEl) cntEl.textContent = total.toLocaleString();
    if (subEl) {
      var _projs = mems.reduce(function (s, m) {
        if (m.project_name) s.add(m.project_name);
        return s;
      }, new Set()).size;
      var _pStr = _projs > 0
        ? ' across ' + _projs + ' project' + (_projs === 1 ? '' : 's')
        : '';
      subEl.textContent = total.toLocaleString() + ' memories' + _pStr +
        '. Every memory shows its provenance — why it was remembered and how strongly it scored.';
    }
    if (!wrap) return;

    if (mems.length === 0) {
      wrap.innerHTML = '<div class="card-pad" style="text-align:center;padding:40px;' +
        'color:var(--fg-2)">No memories found. Try a different filter.</div>';
      _renderPag(id, d);
      return;
    }

    var rows = mems.map(function (m, idx) {
      var imp      = _imp(m.importance);
      var impCls   = imp >= 8 ? 'ok' : imp >= 5 ? 'warn' : 'neutral';
      var scorePct = Math.round((parseFloat(m.importance) || 0) * 100);
      var scoreCls = _scoreCls(m.importance);
      // memory_kind_label (server: storage/memory_kinds.py kind_fields(),
      // the same confirmed/suggested/legacy-fallback precedence `slm list`
      // and recall use) over the raw legacy category — a v2 store with no
      // kind support at all still falls back to m.category.
      var kind     = m.memory_kind || m.category || 'semantic';
      var catLabel = m.memory_kind_label || m.category || 'semantic';
      var preview  = (m.content || '').substring(0, 120);
      if ((m.content || '').length > 120) preview += '…';

      return '<tr class="row" data-od-act="drawer" data-idx="' + idx + '" style="cursor:pointer">' +
        '<td style="max-width:420px">' +
          '<b class="mono muted-info" style="font-size:11px">#' +
            _esc((m.id || '').substring(0, 8)) +
          '</b>' +
          '<button type="button" class="btn ghost" data-od-act="drawer" data-idx="' + idx + '" aria-label="Open memory ' + _esc(m.id) + '" style="text-align:left;white-space:normal">' + _esc(preview) + '</button>' +
        '</td>' +
        '<td><span class="badge ' + _esc(_catCls(kind)) + '">' + _esc(catLabel) + '</span>' +
          // A stored stub (receipt, session boilerplate): listed, never an answer.
          (m.stub ? ' <span class="badge neutral" title="Kept, but never used as an answer">stub</span>' : '') +
        '</td>' +
        '<td class="mono muted-info" style="font-size:12px">' + _esc(m.project_name || '—') + '</td>' +
        '<td><b class="num">' + imp + '</b>/10</td>' +
        '<td><span class="badge ' + _esc(scoreCls) + '">' + scorePct + '%</span></td>' +
        '<td class="mono muted-info" style="font-size:12px">' + _esc(_fmt(m.created_at)) + '</td>' +
      '</tr>';
    }).join('');

    wrap.innerHTML =
      '<table class="tbl">' +
        '<thead><tr>' +
          '<th>Memory</th><th>Kind</th><th>Project</th>' +
          '<th>Importance</th><th>Score</th><th>Created</th>' +
        '</tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>';

    _renderPag(id, d);
  }

  function _renderPag(id, d) {
    var el   = document.getElementById(id + '-pg');
    if (!el) return;
    var tot  = d.total || 0;
    var lim  = d.limit || _st.pageSize;
    var off  = d.offset || (_st.page * _st.pageSize);
    var pg   = Math.floor(off / lim);
    var last = Math.max(0, Math.ceil(tot / lim) - 1);
    var show = Math.min(off + lim, tot);
    var from = tot === 0 ? 0 : off + 1;

    el.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;' +
        'font-size:12.5px;color:var(--fg-2)">' +
        '<span>Showing ' + from + '–' + show + ' of ' + tot.toLocaleString() + '</span>' +
        '<div class="seg">' +
          '<button ' + (pg <= 0 ? 'disabled style="opacity:.4"' :
            'data-od-act="pg" data-page="' + (pg - 1) + '"') + '>← Prev</button>' +
          '<button disabled style="opacity:.7;cursor:default">Page ' +
            (pg + 1) + ' / ' + (last + 1) + '</button>' +
          '<button ' + (pg >= last ? 'disabled style="opacity:.4"' :
            'data-od-act="pg" data-page="' + (pg + 1) + '"') + '>Next →</button>' +
        '</div>' +
      '</div>';
  }

  // ── Sort (client-side on current page) ──────────────────────────────────────

  function _doSort(id, by) {
    _st = Object.assign({}, _st, { sort: by });
    var mems = _st.memories.slice();
    // 'score' and 'importance' both rank by the same underlying importance float
    if (by === 'importance' || by === 'score') {
      mems.sort(function (a, b) {
        return (parseFloat(b.importance) || 0) - (parseFloat(a.importance) || 0);
      });
    } else {
      mems.sort(function (a, b) {
        return String(b.created_at || '').localeCompare(String(a.created_at || ''));
      });
    }
    _st = Object.assign({}, _st, { memories: mems });
    _renderTable(id, {
      memories: mems, total: _st.total,
      limit: _st.pageSize, offset: _st.page * _st.pageSize,
    });
  }

  // ── Search — POST /api/search (full corpus, 0.5–1.4 s warm) ─────────────────

  var _searchTimer = null;
  function _doSearch(id, q) {
    clearTimeout(_searchTimer);
    var requestSeq = _st.requestSeq + 1;
    // /api/search is a full-corpus semantic query. It cannot truthfully retain
    // the list endpoint's category/scope selection, so clear those controls
    // before the request rather than leaving the table and chips disagreeing.
    _st = Object.assign({}, _st, {
      searchQ: q || null,
      category: q ? null : _st.category,
      scopeView: q ? 'mine' : _st.scopeView,
      page: 0,
      requestSeq: requestSeq,
    });
    if (q) _rebuildCatBar(id);
    _searchTimer = setTimeout(function () {
      if (_st.rootId !== id || _st.requestSeq !== requestSeq) return;
      if (!q) { _loadMem(id); return; }
      var wrap = document.getElementById(id + '-tbl-wrap');
      if (wrap) wrap.innerHTML = _loading('Searching…');
      fetch('/api/search', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        slmInvalidatesCache: false,
        slmRequiresWriteAuth: false,
        body: JSON.stringify({ query: q, limit: 50, window: _st.window || '',
                               show_weak: !!_st.showWeak })
      }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }).then(function (d) {
        if (_st.rootId !== id || _st.requestSeq !== requestSeq) return;
        var mems = (d.results || []).map(_normSearch);
        _st = Object.assign({}, _st, { memories: mems, total: mems.length });
        _renderTable(id, { memories: mems, total: mems.length, limit: mems.length, offset: 0 });
        // 4.1.25: why nothing (or only close matches) came back, in plain words.
        // An empty result says the reason instead of "No results found.".
        var note = d.status_note || d.abstention_note || '';
        var nw = document.getElementById(id + '-tbl-wrap');
        if (note && nw) {
          var noteHtml = '<div class="card-pad" style="color:var(--fg-2);' +
            'font-size:13px;padding:10px 12px">' + _esc(note) + '</div>';
          if (!mems.length) nw.innerHTML = noteHtml;
          else nw.insertAdjacentHTML('afterbegin', noteHtml);
        }
      }).catch(function (err) {
        if (_st.rootId !== id || _st.requestSeq !== requestSeq) return;
        var w = document.getElementById(id + '-tbl-wrap');
        if (w) w.innerHTML = '<div class="card-pad" style="color:var(--danger);text-align:center;' +
          'padding:24px">Search failed: ' + _esc(err.message) + '</div>';
      });
    }, 350);
  }

  // ── Timeline fetch & render ──────────────────────────────────────────────────

  var moduleContext = { _esc:_esc, _fmt:_fmt, _ago:_ago, _imp:_imp, _scoreCls:_scoreCls, _catCls:_catCls, _normSearch:_normSearch, _sharedToStr:_sharedToStr, _getMutToken:_getMutToken, _injectCSS:_injectCSS, render:render, _scaffold:_scaffold, _allScaffold:_allScaffold, _tlScaffold:_tlScaffold, _loading:_loading, _recallStarter:_recallStarter, _switchTab:_switchTab, _loadCatCounts:_loadCatCounts, _rebuildCatBar:_rebuildCatBar, _loadMem:_loadMem, _renderTable:_renderTable, _renderPag:_renderPag, _doSort:_doSort, _doSearch:_doSearch };
  Object.defineProperty(moduleContext, 'searchTimer', {get:function(){return _searchTimer;},set:function(v){_searchTimer=v;}});
  Object.defineProperty(moduleContext, 'state', {get:function(){return _st;},set:function(v){_st=v;}});
  var SLMMemoryKnowledge = window.SLMMemoryKnowledge(moduleContext);
  Object.assign(moduleContext, SLMMemoryKnowledge);
  var { _loadTimeline, _renderBars, _renderFtypes, _loadKnowledgeOverview, _koverHtml, _koverCard, _koverPlain, _koverChip, _koverSpan, _koverHealth, _loadClusters, _renderClusters, _expandCluster , _tlLoaded } = SLMMemoryKnowledge;
  var SLMMemoryDrawer = window.SLMMemoryDrawer(moduleContext);
  Object.assign(moduleContext, SLMMemoryDrawer);
  var { _ensureDrawer, _openDrawer, _closeDrawer, _readWriteResponse, _showWriteFailure, _loadFacts, _loadCodeLinks, _startEdit, _startDel } = SLMMemoryDrawer;
  var SLMMemoryEvents = window.SLMMemoryEvents(moduleContext);
  Object.assign(moduleContext, SLMMemoryEvents);
  var { _wire } = SLMMemoryEvents;
  // ── Public API + auto-init ───────────────────────────────────────────────────

  window.odRenderMemories = render;

  // Open one memory in the Memories tab, by fact id: a source opened from a
  // recall elsewhere on the dashboard lands on the real memory, in full, with
  // its scope, project and kind read the same way the table reads them.
  window.odOpenMemory = function (factId) {
    if (!factId) return;
    var link = document.querySelector('.nav-link[data-tab="memories-pane"]');
    if (link) link.click();
    fetch('/api/memories?limit=1&fact_id=' + encodeURIComponent(factId), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        var row = (d.memories || [])[0];
        if (!row) throw new Error('not found');
        var list = (_st.memories || []).filter(function (m) { return m.id !== row.id; });
        list.unshift(row);
        _st = Object.assign({}, _st, { memories: list });
        _openDrawer(0);
      })
      .catch(function () {
        _ensureDrawer();
        var drawer = document.getElementById('od-mem-drawer');
        if (!drawer) return;
        drawer.textContent = '';
        var p = document.createElement('p');
        p.className = 'muted';
        p.textContent = 'That memory could not be opened. It may have been deleted, or it belongs to a workspace you cannot read.';
        drawer.appendChild(p);
        drawer.classList.add('open');
        var scrim = document.getElementById('od-mem-drawer-scrim');
        if (scrim) scrim.classList.add('open');
      });
  };
  document.addEventListener('keydown', function (e) {
    var drawer = document.getElementById('od-mem-drawer');
    if (e.key === 'Escape' && drawer && drawer.classList.contains('open')) _closeDrawer();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      var p = document.getElementById('memories-pane');
      if (p) render(p);
    });
  } else {
    var _p = document.getElementById('memories-pane');
    if (_p) render(_p);
  }

}());
