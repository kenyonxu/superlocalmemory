// Extracted od-memories.js feature scope; initialized once with the pane helpers.
(function () {
  'use strict';
  var ctx;
  var _esc, _fmt, _ago, _imp, _scoreCls, _catCls, _normSearch, _sharedToStr, _getMutToken, _injectCSS, render, _scaffold, _allScaffold, _tlScaffold, _loading, _recallStarter, _switchTab, _loadCatCounts, _rebuildCatBar, _loadMem, _renderTable, _renderPag, _doSort, _doSearch;
  window.SLMMemoryKnowledge = function (helpers) {
    ctx = helpers;
    ({ _esc, _fmt, _ago, _imp, _scoreCls, _catCls, _normSearch, _sharedToStr, _getMutToken, _injectCSS, render, _scaffold, _allScaffold, _tlScaffold, _loading, _recallStarter, _switchTab, _loadCatCounts, _rebuildCatBar, _loadMem, _renderTable, _renderPag, _doSort, _doSearch } = ctx);
    return { _tlLoaded:_tlLoaded,_loadTimeline:_loadTimeline, _renderBars:_renderBars, _renderFtypes:_renderFtypes, _loadKnowledgeOverview:_loadKnowledgeOverview, _koverHtml:_koverHtml, _koverCard:_koverCard, _koverPlain:_koverPlain, _koverChip:_koverChip, _koverSpan:_koverSpan, _koverHealth:_koverHealth, _loadClusters:_loadClusters, _renderClusters:_renderClusters, _expandCluster:_expandCluster };
  };
  var _tlLoaded = {};
  function _loadTimeline(id) {
    var range = ctx.state.tlRange;
    if (_tlLoaded[id + '-' + range]) return;
    var barsEl  = document.getElementById(id + '-bars');
    var ftEl    = document.getElementById(id + '-ftypes');
    var startEl = document.getElementById(id + '-tl-start');

    fetch('/api/v3/timeline/?range=' + range + '&group_by=category&limit=1000')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var events = d.events || [];
        _tlLoaded[id + '-' + range] = true;
        if (events.length === 0) {
          if (barsEl) barsEl.innerHTML =
            '<div style="display:grid;place-items:center;width:100%;height:100%;' +
            'color:var(--fg-2);font-size:13px">No memory events in this range.</div>';
          if (ftEl) ftEl.innerHTML =
            '<div style="color:var(--fg-2);font-size:13px">No category data available.</div>';
          return;
        }
        _renderBars(id, events, range, barsEl, startEl);
        _renderFtypes(id, events, ftEl);
      })
      .catch(function (err) {
        if (barsEl) barsEl.innerHTML =
          '<div style="color:var(--fg-2);font-size:13px;text-align:center;padding:16px">' +
          'Timeline unavailable: ' + _esc(err.message) + '</div>';
        // TODO: endpoint returns 404 when timeline feature is disabled
      });
  }

  function _renderBars(id, events, range, barsEl, startEl) {
    if (!barsEl) return;
    var days   = range === '30d' ? 30 : 7;
    var today  = new Date();
    var counts = new Array(days).fill(0);
    events.forEach(function (ev) {
      var diff = Math.floor((today - new Date(ev.timestamp)) / 86400000);
      if (diff >= 0 && diff < days) counts[days - 1 - diff]++;
    });
    if (typeof slmBars === 'function') {
      slmBars(barsEl, counts);
    } else {
      var maxVal = Math.max.apply(null, counts) || 1;
      barsEl.innerHTML = counts.map(function (v) {
        return '<i style="height:' + Math.max(4, (v / maxVal) * 100) + '%' +
          '" title="' + v + ' events"></i>';
      }).join('');
    }
    if (startEl) startEl.textContent = days + 'd ago';
    var subEl = document.getElementById(id + '-tl-sub');
    if (subEl) subEl.textContent = events.length.toLocaleString() + ' events · last ' + days + ' days';
  }

  function _renderFtypes(id, events, ftEl) {
    if (!ftEl) return;
    var catMap = {};
    events.forEach(function (ev) {
      catMap[ev.category || 'unknown'] = (catMap[ev.category || 'unknown'] || 0) + 1;
    });
    var total  = events.length;
    var colors = { temporal:'var(--ok)', semantic:'var(--violet)', episodic:'var(--cyan)', opinion:'var(--warn)' };
    var sorted = Object.keys(catMap).sort(function (a, b) { return catMap[b] - catMap[a]; });
    ftEl.innerHTML = sorted.map(function (cat) {
      var pct   = Math.round(catMap[cat] / total * 100);
      var color = colors[cat] || 'var(--fg-2)';
      return '<div style="margin-bottom:13px">' +
        '<div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px">' +
          '<span class="mono">' + _esc(cat) + '</span><b>' + pct + '%</b>' +
        '</div>' +
        '<div class="meter"><i style="width:' + pct + '%;background:' + color + '"></i></div>' +
      '</div>';
    }).join('');
  }

  // ── Clusters fetch & render ──────────────────────────────────────────────────

  /* Knowledge Overview — cluster summaries, plus an honest health line.
   *
   * Two rules this card exists to obey.
   *
   * IT READS ONLY THE DISPLAY TABLE. These summaries were in the retrieval
   * corpus until 4.0.10, where they out-ranked the user's own words. Moving
   * them out is only worth anything if exactly one surface shows them, and
   * this is that surface.
   *
   * IT DOES NOT DRESS UP JUNK AS INSIGHT. Some stored summaries are a model's
   * non-answer -- "Unfortunately, there is no information available about
   * 'State'..." -- and rendering those as a heading called Knowledge Overview
   * makes the product look broken. The endpoint labels each row's quality; the
   * usable ones are shown and the rest are counted in one plain sentence. Not
   * hidden: hiding them would hide the very problem the release fixes, and a
   * reader who is told "3 could not be generated" trusts the other 47 more.
   */
  function _loadKnowledgeOverview(id) {
    var box = document.getElementById(id + '-kover');
    if (!box) return;
    // Guarded on the element, not a module flag — same reason as the
    // summary pickers: a stale "already loaded" boolean outlives the DOM
    // it described and leaves a permanent spinner after a re-render.
    if (box.dataset.loaded === '1') return;

    Promise.all([
      fetch('/api/v3/abstraction/consolidated?limit=24')
        .then(function (r) { return r.ok ? r.json() : { summaries: [], unusable: 0 }; })
        .catch(function () { return { summaries: [], unusable: 0 }; }),
      fetch('/api/v3/abstraction/health')
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
    ]).then(function (res) {
      box.dataset.loaded = '1';
      box.innerHTML = _koverHtml(res[0] || {}, res[1]);
    });
  }

  function _koverHtml(data, health) {
    var all = data.summaries || [];
    var usable = all.filter(function (s) { return s.quality === 'ok'; });
    var unusable = typeof data.unusable === 'number' ? data.unusable
                 : (all.length - usable.length);

    var html = '<h3 style="font-size:15px;margin:0 0 4px">What your memory knows</h3>' +
      '<p style="font-size:12px;color:var(--fg-2);margin:0 0 14px">' +
        'Written by the summarizer from groups of your memories. These are a ' +
        'view of your memory, not memories themselves — they are never returned ' +
        'as answers.' +
      '</p>';

    if (health) html += _koverHealth(health);

    if (usable.length === 0) {
      html += '<div style="padding:20px;color:var(--fg-2);font-size:13px">' +
        (all.length === 0
          ? 'No summaries yet. They are written in the background as related ' +
            'memories accumulate.'
          : 'None of the ' + all.length + ' stored summaries came back usable. ' +
            'That usually means the summarizer had nothing in common to merge.') +
        '</div>';
      return html;
    }

    html += '<div style="display:grid;gap:12px;' +
      'grid-template-columns:repeat(auto-fill,minmax(280px,1fr))">';
    usable.forEach(function (s) { html += _koverCard(s); });
    html += '</div>';

    var notes = [];
    if (unusable > 0) {
      notes.push(unusable + (unusable === 1 ? ' summary' : ' summaries') +
        ' could not be generated and are not shown. The memories they were ' +
        'built from are unaffected.');
    }
    if (data.near_duplicates > 0) {
      notes.push(data.near_duplicates + ' near-identical ' +
        (data.near_duplicates === 1 ? 'summary was' : 'summaries were') +
        ' collapsed into the ones above.');
    }
    if (notes.length) {
      html += '<p style="font-size:12px;color:var(--fg-2);margin-top:12px">' +
        notes.map(_esc).join(' ') + '</p>';
    }
    return html;
  }

  function _koverCard(s) {
    // Every field is escaped. Anything in a memory can reach this screen: the
    // summarizer merges fact content verbatim, so a memory containing markup
    // would otherwise render as markup.
    var body = _koverPlain(s.content);
    var clipped = body.length > 320;
    var shown = clipped ? body.slice(0, 320).replace(/\s+\S*$/, '') + '…' : body;

    var meta = [];
    if (s.source_count) {
      meta.push(s.source_count + (s.source_count === 1 ? ' memory' : ' memories'));
    }
    var span = _koverSpan(s.source_earliest, s.source_latest);
    if (span) meta.push(span);

    return '<div style="background:var(--card-2);border:1px solid var(--border);' +
        'border-radius:var(--r-md);padding:14px">' +
      (s.entity_name
        ? '<div style="font-size:13px;font-weight:600;margin-bottom:6px">' +
            _esc(s.entity_name) + '</div>'
        : '') +
      '<div style="font-size:13px;line-height:1.5">' + _esc(shown) + '</div>' +
      (meta.length
        ? '<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">' +
            meta.map(_koverChip).join('') +
          '</div>'
        : '') +
    '</div>';
  }

  /* Flatten a summary to plain prose for display.
   *
   * Mode B/C summaries come back with markdown in them — "**Audit Round**",
   * "**Findings**:" — because a chat-tuned model formats for a chat window.
   * Rendered as text those asterisks read as broken output to exactly the
   * reader this card is for. Escaping is still what protects the page; this
   * only removes the emphasis marks that carry no meaning here.
   */
  function _koverPlain(text) {
    return String(text || '')
      .replace(/```[\s\S]*?```/g, ' ')     // fenced blocks: no room for them
      .replace(/`([^`]*)`/g, '$1')          // inline code marks
      .replace(/\*\*([^*]+)\*\*/g, '$1')   // bold
      .replace(/(^|\s)\*([^*\s][^*]*)\*/g, '$1$2')  // italic, not bullets
      .replace(/^#{1,6}\s+/gm, '')          // headings
      .replace(/^\s*[-*+]\s+/gm, '')        // list bullets
      .replace(/\s+/g, ' ')                 // collapse whitespace + newlines
      .trim();
  }

  /* A chip, not a keyword dump.
   *
   * The neighbouring community-summary API returns its `summary` field as
   * "Topics: CCQ, Resume, P07…", which printed verbatim reads as a broken
   * page. Counts and date spans are rendered as discrete labels instead.
   */
  function _koverChip(text) {
    return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;' +
      'background:var(--card);border:1px solid var(--border);color:var(--fg-2)">' +
      _esc(text) + '</span>';
  }

  function _koverSpan(earliest, latest) {
    var a = String(earliest || '').slice(0, 10);
    var b = String(latest || '').slice(0, 10);
    if (!a && !b) return '';
    if (!a || !b || a === b) return a || b;
    return a + ' to ' + b;
  }

  /* Whether the memories behind all this can actually be found.
   *
   * The one question no screen answered before. A machine ran for months with
   * 43.7% of its store unreachable by meaning while every status line it showed
   * said healthy, because nothing counted the reachable share.
   */
  function _koverHealth(h) {
    var lines = h.summary || [];
    if (!lines.length) return '';
    var bad = h.healthy === false;
    return '<div style="margin:0 0 16px;padding:12px 14px;border-radius:var(--r-md);' +
        'background:var(--card-2);border-left:3px solid ' +
        (bad ? 'var(--warn)' : 'var(--ok)') + '">' +
      lines.map(function (l) {
        return '<div style="font-size:12px;color:var(--fg-2);line-height:1.5">' +
          _esc(l) + '</div>';
      }).join('') +
    '</div>';
  }

  function _loadClusters(id, offset) {
    if (ctx.state.cluLoaded && offset === undefined) return;
    offset = offset === undefined ? (ctx.state.clusterOffset || 0) : offset;
    ctx.state = Object.assign({}, ctx.state, {clusterOffset:offset});
    var grid = document.getElementById(id + '-clu-grid');
    if (!grid) return;
    fetch('/api/dashboard/clusters?limit=25&offset=' + offset)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        ctx.state = Object.assign({}, ctx.state, { cluLoaded: true });
        var clus  = d.clusters || [];
        var cntEl = document.getElementById(id + '-cnt-clusters');
        if (cntEl) cntEl.textContent = (d.total_clusters || 0).toLocaleString();
        if (clus.length === 0) {
          grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;' +
            'color:var(--fg-2)">No clusters yet. Clusters form as memories accumulate.</div>';
          return;
        }
        _renderClusters(id, clus, grid);
        var pager = document.createElement('div');
        pager.style.gridColumn = '1/-1';
        [['Previous',Math.max(0,offset-25),offset===0],['Next',offset+25,!d.has_more]].forEach(function (item) {
          var b = document.createElement('button'); b.type = 'button'; b.className='btn'; b.textContent=item[0]; b.disabled=item[2];
          b.addEventListener('click', function(){_loadClusters(id,item[1]);}); pager.appendChild(b);
        });
        grid.appendChild(pager);
      })
      .catch(function (err) {
        if (grid) grid.innerHTML =
          '<div style="grid-column:1/-1;text-align:center;padding:24px;' +
          'color:var(--danger);font-size:13px">Failed to load clusters: ' +
          _esc(err.message) + '</div>';
      });
  }

  var _PAL = ['var(--violet)', 'var(--cyan)', 'var(--warn)', 'var(--ok)', 'var(--danger)'];

  function _renderClusters(id, clus, grid) {
    grid.innerHTML = clus.map(function (c, i) {
      var color   = _PAL[i % _PAL.length];
      var summary = c.summary || c.categories || '';
      var preview = summary.length > 80 ? summary.substring(0, 80) + '…' : summary;
      var cid     = String(c.cluster_id || '');
      return '<div class="launch-card card" style="cursor:pointer"' +
        ' data-od-act="expand-cluster" data-cid="' + _esc(cid) + '">' +
        '<div class="ic" style="background:color-mix(in srgb,' + color + ' 15%,transparent);color:' + color + '">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="21" height="21">' +
            '<path d="M12 3l1.9 4.6L18 9l-3.5 3 1 5-3.5-2.5L8.5 17l1-5L6 9l4.1-1.4z"/>' +
          '</svg>' +
        '</div>' +
        '<h3>' + _esc(preview) + '</h3>' +
        '<p><b class="num">' + _esc(String(c.member_count)) + '</b> memories · expand →</p>' +
        '<div style="display:none;margin-top:14px;border-top:1px solid var(--border);' +
          'padding-top:12px;font-size:13px" id="' + id + '-clu-' + _esc(cid) + '"></div>' +
      '</div>';
    }).join('');
  }

  function _expandCluster(id, cid) {
    var detEl = document.getElementById(id + '-clu-' + cid);
    if (!detEl) return;
    if (detEl.style.display !== 'none') { detEl.style.display = 'none'; return; }
    detEl.style.display = 'block';
    if (detEl.dataset.loaded) return;
    detEl.dataset.loaded = '1';
    detEl.textContent = 'Loading members…';
    fetch('/api/clusters/' + encodeURIComponent(cid) + '?limit=5')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var members = d.members || [];
        if (members.length === 0) { detEl.textContent = 'No members found.'; return; }
        detEl.innerHTML = members.map(function (m, i) {
          var txt = m.content || m.summary || '';
          return '<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid var(--border)">' +
            '<div style="font-size:12.5px">' + (i + 1) + '. ' +
              _esc(txt.substring(0, 120)) + '</div></div>';
        }).join('');
      })
      .catch(function () { detEl.textContent = 'Failed to load members.'; });
  }

  // ── Provenance drawer ────────────────────────────────────────────────────────


}());
