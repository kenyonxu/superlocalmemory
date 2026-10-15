// Extracted od-backup.js feature scope; initialized once with the pane helpers.
(function () {
  'use strict';
  var ctx;
  var getToken, authMutation, esc, toast, fmt, fmtDate, el, q, set, injectStyles, card, P, doBackupNow, doExport, doSync, doDisconnect, openOAuth, loadAll, loadStatus, loadDestinations, loadList;
  window.SLMBackupLayout = function (helpers) {
    ctx = helpers;
    ({ getToken, authMutation, esc, toast, fmt, fmtDate, el, q, set, injectStyles, card, P, doBackupNow, doExport, doSync, doDisconnect, openOAuth, loadAll, loadStatus, loadDestinations, loadList } = ctx);
    return { buildHero:buildHero, buildKPIs:buildKPIs, injectConnIcons:injectConnIcons, buildConnections:buildConnections, renderConnections:renderConnections, buildConnRow:buildConnRow, buildScope:buildScope, fmtScope:fmtScope, fmtDest:fmtDest, buildHistory:buildHistory, renderHistory:renderHistory };
  };
  function buildHero(root) {
    var hero = el('section');
    hero.className = 'card glass';
    Object.assign(hero.style, { padding:'24px 26px', marginBottom:'16px' });

    var inner = el('div', null,
      { display:'flex', alignItems:'center', gap:'18px', flexWrap:'wrap' });

    // Cloud orb icon (gradient, matches design)
    var orb = el('span', { id: P + '-orb' });
    orb.className = 'cloud-orb';
    if (typeof window.slmIcon === 'function') {
      orb.innerHTML = window.slmIcon('cloud');
    } else {
      orb.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor"' +
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg>';
    }
    inner.appendChild(orb);

    // Title + badge + sub
    var titleArea = el('div', null, { flex:'1', minWidth:'200px' });
    var titleRow = el('div', null, { display:'flex', alignItems:'center', gap:'10px' });
    var titleEl = el('h3', { id: P + '-cloud-title', text:'Checking cloud backup' },
      { fontSize:'17px', margin:'0' });
    titleRow.appendChild(titleEl);
    var syncBadge = el('span', { id: P + '-cloud-badge' });
    syncBadge.className = 'badge neutral';
    syncBadge.innerHTML = '<span class="dot"></span> Checking';
    titleRow.appendChild(syncBadge);
    titleArea.appendChild(titleRow);
    var subEl = el('p', { id: P + '-cloud-sub', text:'Connecting to your cloud…' },
      { fontSize:'13px', marginTop:'4px', color:'var(--fg-2)' });
    titleArea.appendChild(subEl);
    inner.appendChild(titleArea);

    // Stats: Last backup / Next scheduled / on-disk representation
    var statsRow = el('div', null, { display:'flex', gap:'26px', flexWrap:'wrap' });
    [
      { id:'hero-last',    lbl:'Last backup' },
      { id:'hero-next',    lbl:'Next scheduled' },
      { id:'hero-encrypt', lbl:'Backup format' }
    ].forEach(function (s) {
      var item = el('div');
      // Stat label ("Last backup", "Next scheduled", "Backup format") is
      // informational — --fg-3 (2.91:1/4.02:1) is under WCAG AA (4.5:1).
      item.appendChild(el('div', { text: s.lbl },
        { fontSize:'11px', color:'var(--fg-2)' }));
      item.appendChild(el('div', { id: P + '-' + s.id, text:'…' },
        { fontSize:'16px', fontWeight:'650', marginTop:'2px' }));
      statsRow.appendChild(item);
    });
    inner.appendChild(statsRow);

    // Buttons
    var btnGroup = el('div', null,
      { display:'flex', flexDirection:'column', gap:'8px', alignItems:'flex-end' });
    var nowBtn = el('button', { type:'button', id: P + '-btn-now' });
    nowBtn.className = 'btn primary';
    if (typeof window.slmIcon === 'function') {
      nowBtn.innerHTML = window.slmIcon('cloud') + ' Back up now';
    } else {
      nowBtn.textContent = 'Back up now';
    }
    nowBtn.addEventListener('click', doBackupNow.bind(null, root));
    btnGroup.appendChild(nowBtn);

    var exportBtn = el('button', { type:'button' });
    exportBtn.className = 'btn ghost';
    exportBtn.textContent = 'Export recovery archive';
    exportBtn.addEventListener('click', function () {
      doExport(exportBtn);
    });
    btnGroup.appendChild(exportBtn);
    inner.appendChild(btnGroup);

    hero.appendChild(inner);
    return hero;
  }

  /* ══════════════════════════════════════════════════════════
     KPI STRIP  (design uses .kpi-strip + .card.kpi structure)
  ══════════════════════════════════════════════════════════ */
  function buildKPIs() {
    var kpis = [
      { id:'kpi-total', lbl:'Total backups',    icon:'cloud' },
      { id:'kpi-size',  lbl:'Local storage used', icon:'memories' },
      { id:'kpi-retain',lbl:'Retention',         icon:'clock' },
      { id:'kpi-count', lbl:'Local snapshots',   icon:'shield' }
    ];
    var strip = el('div', null, { marginBottom:'16px' });
    strip.className = 'kpi-strip';
    kpis.forEach(function (k) {
      var tile = el('div');
      tile.className = 'card kpi';
      var lbl = el('div');
      lbl.className = 'label';
      if (typeof window.slmIcon === 'function') {
        var icEl = el('span', null, { display:'contents' });
        icEl.innerHTML = window.slmIcon(k.icon);
        lbl.appendChild(icEl);
      }
      lbl.appendChild(document.createTextNode(' ' + k.lbl));
      tile.appendChild(lbl);
      var val = el('div', { id: P + '-' + k.id, text:'—' });
      val.className = 'value num';
      // Retention value ("10 snapshots") wraps at 30px — design overrides to 22px for this tile
      if (k.id === 'kpi-retain') val.style.fontSize = '22px';
      tile.appendChild(val);
      strip.appendChild(tile);
    });
    return strip;
  }

  /* ══════════════════════════════════════════════════════════
     CONNECTIONS — GitHub + Google OAuth + S3
     Design: .bk-conn rows with .bk-conn-ic.gh/.goog/.s3 brand icons
  ══════════════════════════════════════════════════════════ */
  var PROVIDERS = [
    { id:'github', label:'GitHub',
      icClass:'gh',   oauthPath:'/api/backup/oauth/github/start',
      hint:'Connect as your GitHub account to mirror backups to a private repo.' },
    { id:'google', label:'Google (Gmail)',
      icClass:'goog', oauthPath:'/api/backup/oauth/google/start',
      hint:'Back up to Google Drive via your Gmail account.' },
    { id:'s3',     label:'Custom S3 / WebDAV',
      icClass:'s3',   oauthPath:'/api/backup/oauth/s3/start',
      hint:'Bring your own S3-compatible bucket (Wasabi, MinIO, Cloudflare R2).' }
  ];

  // Inject brand SVGs after DOM settles — same pattern as design's backup.html
  function injectConnIcons(container) {
    var gh = container.querySelector('.bk-conn-ic.gh');
    if (gh && typeof window.slmIcon === 'function') gh.innerHTML = window.slmIcon('github');
    var goog = container.querySelector('.bk-conn-ic.goog');
    if (goog) goog.innerHTML =
      '<svg viewBox="0 0 24 24" width="20" height="20">' +
      '<path fill="#4285F4" d="M21.6 12.2c0-.6-.1-1.2-.2-1.8H12v3.4h5.4a4.6 4.6 0 0 1-2 3v2.5h3.2c1.9-1.7 3-4.3 3-7.1z"/>' +
      '<path fill="#34A853" d="M12 22c2.7 0 5-.9 6.6-2.4l-3.2-2.5c-.9.6-2 .9-3.4.9-2.6 0-4.8-1.7-5.6-4.1H3.1v2.6A10 10 0 0 0 12 22z"/>' +
      '<path fill="#FBBC05" d="M6.4 13.9a6 6 0 0 1 0-3.8V7.5H3.1a10 10 0 0 0 0 9z"/>' +
      '<path fill="#EA4335" d="M12 6.6c1.5 0 2.8.5 3.8 1.5l2.8-2.8A10 10 0 0 0 3.1 7.5l3.3 2.6C7.2 8.3 9.4 6.6 12 6.6z"/>' +
      '</svg>';
    var s3 = container.querySelector('.bk-conn-ic.s3');
    if (s3 && typeof window.slmIcon === 'function') s3.innerHTML = window.slmIcon('cloud');
  }

  function buildConnections(root) {
    var c = card('Connections', 'connect your own accounts', 'mesh');
    c.body.style.padding = '0 20px';
    var connList = el('div', { id: P + '-conn-list' });
    c.body.appendChild(connList);
    return c.wrap;
  }

  function renderConnections(root, destinations) {
    var connList = q(root, 'conn-list'); if (!connList) return;
    connList.innerHTML = '';
    var frag = document.createDocumentFragment();
    PROVIDERS.forEach(function (prov) {
      var dest = destinations.filter(function (d) {
        var destinationType = d.destination_type || d.type || d.provider;
        return destinationType === prov.id ||
          (prov.id === 'google' && destinationType === 'google_drive');
      })[0];
      frag.appendChild(buildConnRow(root, prov, dest || null));
    });
    connList.appendChild(frag);
    // Inject brand icons after DOM insertion
    injectConnIcons(connList);
  }

  function buildConnRow(root, prov, dest) {
    var row = el('div');
    row.className = 'bk-conn' + (dest ? '' : ' off');
    row.setAttribute('data-conn', prov.id);

    // Brand icon
    var iconWrap = el('span');
    iconWrap.className = 'bk-conn-ic ' + prov.icClass;
    row.appendChild(iconWrap);

    // Info area
    var info = el('div', null, { flex:'1' });
    info.appendChild(el('b', { text: prov.label }));
    var detailEl = el('div');
    // Sync status / provider hint text is informational, not decorative
    // (2.91:1/4.09:1 under --fg-3, under WCAG AA 4.5:1).
    detailEl.className = 'muted-info';
    Object.assign(detailEl.style, { fontSize:'12.5px' });
    if (dest) {
      var config = {};
      try { config = typeof dest.config === 'string' ? JSON.parse(dest.config) : (dest.config || {}); }
      catch (e) { config = {}; }
      var repoOrBucket = dest.repo || dest.bucket || dest.folder_id ||
        config.full_repo || config.repo || config.folder || '';
      var syncStatus = dest.last_sync_status || 'never';
      var statusText = syncStatus === 'success' && dest.last_sync_at
        ? 'Last sync succeeded ' + fmtDate(dest.last_sync_at)
        : syncStatus === 'failed'
          ? 'Last sync failed'
          : 'Connected · never synced';
      detailEl.textContent = statusText + (repoOrBucket ? ' · ' + repoOrBucket : '');
    } else {
      detailEl.textContent = prov.hint;
    }
    info.appendChild(detailEl);
    row.appendChild(info);

    // Action button
    var btnWrap = el('div', null, { display:'flex', gap:'7px', flexShrink:'0' });
    if (dest) {
      var syncBtn = el('button', { type:'button' });
      syncBtn.className = 'btn sm ghost';
      syncBtn.textContent = 'Sync all';
      syncBtn.addEventListener('click', function () {
        doSync(root);
      });
      btnWrap.appendChild(syncBtn);

      var discBtn = el('button', { type:'button' });
      discBtn.className = 'btn sm';
      discBtn.textContent = 'Disconnect';
      discBtn.addEventListener('click', function () {
        doDisconnect(root, dest.id || dest.dest_id || prov.id);
      });
      btnWrap.appendChild(discBtn);
    } else {
      var connBtn = el('button', { type:'button' });
      connBtn.className = 'btn sm primary';
      connBtn.textContent = prov.id === 's3' ? 'Unavailable' : 'Connect';
      if (prov.id === 's3') { connBtn.disabled = true; detailEl.textContent = 'S3 / WebDAV backup is not available in this release.'; }
      connBtn.addEventListener('click', function () {
        openOAuth(root, prov.oauthPath, prov.label);
      });
      btnWrap.appendChild(connBtn);
    }
    row.appendChild(btnWrap);
    return row;
  }

  /* ══════════════════════════════════════════════════════════
     BACKUP SCOPE + SCHEDULE
     Design: .bk-ctl rows with .switch toggles; Schedule at bottom
  ══════════════════════════════════════════════════════════ */
  function buildScope() {
    var c = card('Backup scope', 'current managed database set', 'operations');

    // Scope items — matching design's 4 items with toggle switches
    var scopeItems = [
      { id:'scope-mem',   lbl:'Memories',
        sub:'<span class="mono">memory.db</span> · facts & conversations', on: true },
      { id:'scope-learn', lbl:'Learning data',
        sub:'<span class="mono">learning.db</span> · ranking model', on: true },
      { id:'scope-audit', lbl:'Audit & code data',
        sub:'<span class="mono">audit_chain.db · code_graph.db</span> · when present', on: true },
      { id:'scope-media', lbl:'Pictures & documents', sub:'media.db and original files, when present', on:true },
      { id:'scope-settings', lbl:'Settings', sub:'Configuration with credentials excluded', on:true },
      { id:'scope-pending', lbl:'Pending operations',
        sub:'<span class="mono">pending.db</span> · when present', on: true }
    ];

    scopeItems.forEach(function (item) {
      var row = el('div');
      row.className = 'bk-ctl';

      var labelGroup = el('div');
      labelGroup.appendChild(el('b', { text: item.lbl }));
      var subEl = el('div');
      // Also informational (the file names and what each db holds), same
      // reason as detailEl above — includes the nested .mono filenames,
      // which have no color of their own and inherit this.
      subEl.className = 'muted-info';
      subEl.style.fontSize = '12.5px';
      subEl.innerHTML = item.sub;
      labelGroup.appendChild(subEl);
      row.appendChild(labelGroup);

      var sw = el('button', { type:'button', id: P + '-' + item.id });
      sw.className = 'switch' + (item.on ? ' on' : '');
      sw.setAttribute('role', 'switch');
      sw.setAttribute('aria-checked', String(item.on));
      sw.disabled = true;
      sw.title = 'Managed database scope is fixed in this release';
      row.appendChild(sw);
      c.body.appendChild(row);
    });

    // Schedule row — matching design
    var schedRow = el('div');
    schedRow.className = 'bk-ctl';
    schedRow.appendChild(el('b', { text: 'Schedule' }));

    var segs = ['Manual', 'Daily', 'Weekly'];
    var segWrap = el('div');
    segWrap.className = 'seg';
    segs.forEach(function (s) {
      var b = el('button', { type:'button', id: P + '-seg-' + s.toLowerCase() });
      b.textContent = s;
      if (s === 'Daily') b.className = 'active';
      b.addEventListener('click', function () {
        segs.forEach(function (t) {
          var tb = document.getElementById(P + '-seg-' + t.toLowerCase());
          if (tb) tb.className = (t === s ? 'active' : '');
        });
        var schedule = s === 'Manual'
          ? { enabled: false }
          : { enabled: true, interval_hours: s === 'Daily' ? 24 : 168 };
        authMutation('/api/backup/configure', 'POST', schedule)
          .then(function (r) { return r.json(); })
          .then(function () { toast(s + ' schedule saved'); })
          .catch(function () { toast('Schedule save failed', true); });
      });
      segWrap.appendChild(b);
    });
    schedRow.appendChild(segWrap);
    c.body.appendChild(schedRow);

    return c.wrap;
  }

  /* ══════════════════════════════════════════════════════════
     BACKUP HISTORY TABLE
     Design: .tbl class, columns When/Scope/Size/Destination/Status
  ══════════════════════════════════════════════════════════ */
  // Map backup type from API to a human-readable scope string
  function fmtScope(bk) {
    var t = (bk.type || 'memory').toLowerCase();
    if (t === 'full')     return 'Memories · Learning · Config';
    if (t === 'memory')   return 'Memories';
    if (t === 'learning') return 'Learning data';
    if (t === 'config')   return 'Config & profiles';
    return esc(t);
  }

  function fmtDest(bk) {
    var d = bk.destination || bk.dest || '';
    if (!d) return 'local · ~/.slm/backups';
    return esc(d);
  }

  var _histFilter = 'all'; // all | cloud | local

  function buildHistory() {
    var c = card('Backup history', 'local snapshots and upload evidence', 'health');

    // Add filter segment to card-head (All / Cloud / Local)
    var spacer = el('div');
    spacer.className = 'spacer';
    c.head.appendChild(spacer);

    var seg = el('div');
    seg.className = 'seg';
    ['All', 'Cloud', 'Local'].forEach(function (f) {
      var b = el('button', { type:'button', id: P + '-hist-' + f.toLowerCase() });
      b.textContent = f;
      if (f === 'All') b.className = 'active';
      b.addEventListener('click', function () {
        ['all','cloud','local'].forEach(function (k) {
          var btn = document.getElementById(P + '-hist-' + k);
          if (btn) btn.className = (k === f.toLowerCase() ? 'active' : '');
        });
        _histFilter = f.toLowerCase();
      });
      seg.appendChild(b);
    });
    c.head.appendChild(seg);

    c.body.style.padding = '0';
    var wrap = el('div', { id: P + '-history-wrap' });
    c.body.appendChild(wrap);
    return c.wrap;
  }

  function renderHistory(root, backups) {
    var wrap = q(root, 'history-wrap'); if (!wrap) return;
    if (!backups || backups.length === 0) {
      wrap.innerHTML = '<div style="padding:28px;text-align:center;color:var(--fg-2);' +
        'font-size:13px">No backups yet. Click "Back up now" to create your first snapshot.</div>';
      return;
    }
    var table = el('table');
    table.className = 'tbl';
    table.id = P + '-hist-tbl';

    var thead = el('thead');
    var headTr = el('tr');
    ['When', 'Scope', 'Size', 'Destination', 'Status', ''].forEach(function (h) {
      var th = el('th', { text: h });
      headTr.appendChild(th);
    });
    thead.appendChild(headTr);
    table.appendChild(thead);

    var tbody = el('tbody');
    backups.slice(0, 20).forEach(function (bk) {
      var tr = el('tr');
      // When
      var tdWhen = el('td'); tdWhen.className = 'muted-info';
      tdWhen.textContent = fmtDate(bk.created);
      tr.appendChild(tdWhen);
      // Scope
      var tdScope = el('td');
      tdScope.textContent = fmtScope(bk);
      tr.appendChild(tdScope);
      // Size
      var tdSize = el('td'); tdSize.className = 'num';
      tdSize.textContent = bk.size_mb ? bk.size_mb.toFixed(0) + ' MB' : '—';
      tr.appendChild(tdSize);
      // Destination
      var tdDest = el('td'); tdDest.className = 'mono';
      tdDest.style.fontSize = '12px';
      tdDest.textContent = fmtDest(bk);
      tr.appendChild(tdDest);
      // Status badge
      var tdStatus = el('td');
      var statusBadge = el('span');
      var statusTxt = bk.status || 'Complete';
      statusBadge.className = 'badge ' +
        (statusTxt === 'Complete' || statusTxt === 'ok' ? 'ok' :
         statusTxt === 'Partial' ? 'warn' : 'danger');
      statusBadge.innerHTML = '<span class="dot"></span>' + esc(statusTxt);
      tdStatus.appendChild(statusBadge);
      tr.appendChild(tdStatus);
      // Preview is verified by the server before a typed offline restore.
      var tdAct = el('td', null, { textAlign:'right' });
      if (window.SLMBackupRecovery) window.SLMBackupRecovery.actions(tdAct, bk, authMutation);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.innerHTML = '';
    wrap.appendChild(table);
  }


}());
