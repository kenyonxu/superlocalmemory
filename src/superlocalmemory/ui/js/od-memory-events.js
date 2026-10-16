// Extracted od-memories.js feature scope; initialized once with the pane helpers.
(function () {
  'use strict';
  var ctx;
  var _esc, _fmt, _ago, _imp, _scoreCls, _catCls, _normSearch, _sharedToStr, _getMutToken, _injectCSS, render, _scaffold, _allScaffold, _tlScaffold, _loading, _recallStarter, _switchTab, _loadCatCounts, _rebuildCatBar, _loadMem, _renderTable, _renderPag, _doSort, _doSearch;
  window.SLMMemoryEvents = function (helpers) {
    ctx = helpers;
    ({ _esc, _fmt, _ago, _imp, _scoreCls, _catCls, _normSearch, _sharedToStr, _getMutToken, _injectCSS, render, _scaffold, _allScaffold, _tlScaffold, _loading, _recallStarter, _switchTab, _loadCatCounts, _rebuildCatBar, _loadMem, _renderTable, _renderPag, _doSort, _doSearch } = ctx);
    return { _wire:_wire };
  };
  function _wire(container, id) {
    container.addEventListener('click', function (e) {
      var el  = e.target.closest('[data-od-act]');
      if (!el) return;
      var act = el.dataset.odAct;

      // Example query chip in the Recall Lab starter state. recall-lab.js owns the
      // search itself and listens on #recall-lab-search, so fill the box and let it
      // run — duplicating its fetch here would be a second, divergent code path.
      if (act === 'recall-eg') {
        var qBox = document.getElementById('recall-lab-query');
        var qBtn = document.getElementById('recall-lab-search');
        if (qBox) qBox.value = el.dataset.q || '';
        if (qBtn) qBtn.click();
        return;
      }
      if (act === 'tab') {
        _switchTab(id, el.dataset.tab);
        return;
      }
      if (act === 'cat') {
        // Toggle on/off chip states without a full re-render
        var bar = document.getElementById(id + '-cats');
        if (bar) bar.querySelectorAll('[data-od-act="cat"]').forEach(function (c) {
          c.classList.toggle('on', c.dataset.cat === el.dataset.cat);
        });
        clearTimeout(ctx.searchTimer);
        ctx.state = Object.assign({}, ctx.state, {
          category: el.dataset.cat || null, page: 0, searchQ: null,
          requestSeq: ctx.state.requestSeq + 1,
        });
        var searchInput = document.querySelector('#' + id + ' [data-od-act="search"]');
        if (searchInput) searchInput.value = '';
        _loadMem(id);
        return;
      }
      if (act === 'sort') {
        // Update active state on sort buttons in-place
        var root = document.getElementById(id);
        if (root) root.querySelectorAll('[data-od-act="sort"]').forEach(function (b) {
          b.classList.toggle('active', b.dataset.sort === el.dataset.sort);
        });
        _doSort(id, el.dataset.sort);
        return;
      }
      if (act === 'scope-view') {
        var sroot = document.getElementById(id);
        if (sroot) sroot.querySelectorAll('[data-od-act="scope-view"]').forEach(function (b) {
          b.classList.toggle('active', b.dataset.scope === el.dataset.scope);
        });
        clearTimeout(ctx.searchTimer);
        ctx.state = Object.assign({}, ctx.state, {
          scopeView: el.dataset.scope, page: 0, searchQ: null,
          requestSeq: ctx.state.requestSeq + 1,
        });
        var scopedSearchInput = document.querySelector('#' + id + ' [data-od-act="search"]');
        if (scopedSearchInput) scopedSearchInput.value = '';
        _loadMem(id);
        return;
      }
      if (act === 'drawer') {
        var row = el.closest('tr[data-od-act]');
        var idx = parseInt((row || el).dataset.idx, 10);
        if (!isNaN(idx)) ctx._openDrawer(idx);
        return;
      }
      if (act === 'close-drawer') { ctx._closeDrawer(); return; }
      if (act === 'pg') {
        var pg = parseInt(el.dataset.page, 10);
        if (!isNaN(pg)) {
          clearTimeout(ctx.searchTimer);
          ctx.state = Object.assign({}, ctx.state, {
            page: pg, searchQ: null, requestSeq: ctx.state.requestSeq + 1,
          });
          var pagedSearchInput = document.querySelector('#' + id + ' [data-od-act="search"]');
          if (pagedSearchInput) pagedSearchInput.value = '';
          _loadMem(id);
        }
        return;
      }
      if (act === 'expand-cluster') { ctx._expandCluster(id, el.dataset.cid); return; }
      if (act === 'tl-range') {
        var root2 = document.getElementById(id);
        if (root2) root2.querySelectorAll('[data-od-act="tl-range"]').forEach(function (b) {
          b.classList.toggle('active', b.dataset.range === el.dataset.range);
        });
        ctx._tlLoaded[id + '-' + ctx.state.tlRange] = false; // invalidate cache
        ctx.state = Object.assign({}, ctx.state, { tlRange: el.dataset.range });
        ctx._loadTimeline(id);
        return;
      }
    });

    // Search — input event
    container.addEventListener('change', function (e) {
      if (e.target.dataset.odAct === 'showweak') {
        // 4.1.25: list the closest matches when nothing answers exactly.
        ctx.state = Object.assign({}, ctx.state, { showWeak: !!e.target.checked });
        var sq = (ctx.state.searchQ || '').trim();
        if (sq) _doSearch(id, sq);
      }
      if (e.target.dataset.odAct === 'window') {
        ctx.state = Object.assign({}, ctx.state, { window: e.target.value || '' });
        var wq = (ctx.state.searchQ || '').trim();
        if (wq) _doSearch(id, wq);   // re-run active search with the new window
      }
    });
    container.addEventListener('input', function (e) {
      if (e.target.dataset.odAct === 'search') _doSearch(id, e.target.value.trim());
    });

    // Drawer actions — document-level (drawer is outside container), guarded once
    if (!window._slmMemDrawerWired) {
      window._slmMemDrawerWired = true;
      document.addEventListener('click', function (e) {
        var el = e.target.closest('[data-od-act]');
        if (!el) return;
        var act = el.dataset.odAct;
        if (act === 'close-drawer') { ctx._closeDrawer(); return; }
        if (act === 'edit-mem') { ctx._startEdit(el.dataset.mid); return; }
        if (act === 'del-mem')  { ctx._startDel(el.dataset.mid); return; }
        if (act === 'cancel-act') { var ac = document.getElementById('od-mem-act'); if (ac) ac.innerHTML = ''; return; }
        if (act === 'set-scope') {
          var smid = el.dataset.mid;
          var scopeVal = (document.getElementById('od-scope-sel') || {}).value || 'personal';
          var sharedVal = ((document.getElementById('od-scope-shared') || {}).value || '').trim();
          if (scopeVal === 'shared' && !sharedVal) {
            showToast('Enter at least one profile to share with'); return;
          }
          _getMutToken().then(function (tok) {
            if (!tok) { showToast('Auth unavailable — reload the page'); return; }
            fetch('/api/memories/' + encodeURIComponent(smid) + '/scope', {
              method: 'PATCH', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json', 'X-Install-Token': tok },
              body: JSON.stringify({ scope: scopeVal, shared_with: sharedVal }),
            })
              .then(ctx._readWriteResponse)
              .then(function () { showToast('Scope updated → ' + scopeVal); ctx._closeDrawer(); _loadMem(ctx.state.rootId); })
              .catch(function (e) { ctx._showWriteFailure(e, el); });
          });
          return;
        }
        if (act === 'save-edit' || act === 'confirm-del') {
          var rid = ctx.state.rootId, isDel = act === 'confirm-del', mid = el.dataset.mid;
          var content = isDel ? '' : ((document.getElementById('od-ea') || {}).value || '').trim();
          if (!isDel && !content) { showToast('Content cannot be empty'); return; }
          _getMutToken().then(function (tok) {
            if (!tok) { showToast('Auth unavailable — reload the page'); return; }
            var opts = isDel
              ? {method:'DELETE',credentials:'same-origin',headers:{'X-Install-Token':tok}}
              : {method:'PATCH',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Install-Token':tok},body:JSON.stringify({content:content})};
            fetch('/api/memories/' + encodeURIComponent(mid), opts)
              .then(ctx._readWriteResponse)
              .then(function () { showToast(isDel ? 'Memory deleted' : 'Memory updated'); ctx._closeDrawer(); _loadMem(rid); })
              .catch(function (e) { ctx._showWriteFailure(e, el); });
          });
          return;
        }
      });
    }
  }


}());
