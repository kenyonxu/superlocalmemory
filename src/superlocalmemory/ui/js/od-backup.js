/* od-backup.js — SuperLocalMemory Backup & Cloud Dashboard v1.0
 * Exposes:
 *   window.odRenderBackup(container)    — render into a supplied pane
 *   window.odOpenBackupDashboard()      — open a full-screen overlay (called from od-settings.js)
 * Wired endpoints (all confirmed against live daemon):
 *   GET  /api/backup/status
 *   POST /api/backup/create
 *   POST /api/backup/configure          body: {interval_hours, max_backups, enabled}
 *   GET  /api/backup/list
 *   GET  /api/backup/destinations
 *   POST /api/backup/connect/github     body: {pat, repo_name}
 *   DELETE /api/backup/disconnect/{id}
 *   POST /api/backup/sync
 *   POST /api/backup/export             → FileResponse (.db.gz download)
 *   GET  /api/backup/oauth/github/start → OAuth redirect or PAT form
 *   GET  /api/backup/oauth/google/start → Google OAuth redirect
 *   GET/POST /api/backup/encryption, /api/backup/recovery-key (od-backup-encryption.js)
 * No mock / seed data — every render goes to the live daemon.
 * Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar — AGPL-3.0
 */
(function () {
  'use strict';

  var OVERLAY_ID = 'od-backup-overlay';
  var P = 'od-bk';
  var tokenPromise = null;

  function getToken(forceRefresh, retriedEmpty) {
    if (forceRefresh) tokenPromise = null;
    if (!tokenPromise) {
      tokenPromise = fetch('/internal/token', { credentials:'same-origin' })
        .then(function (response) {
          if (!response.ok) throw new Error('Dashboard authorization failed');
          return response.json();
        })
        .then(function (data) {
          if (!data.token && !retriedEmpty) {
            tokenPromise = null;
            return getToken(true, true);
          }
          if (!data.token) throw new Error('Dashboard authorization token is unavailable');
          return data.token;
        })
        .catch(function (error) {
          tokenPromise = null;
          throw error;
        });
    }
    return tokenPromise;
  }
  function authMutation(url, method, body) {
    function send(forceRefresh) {
      return getToken(forceRefresh).then(function (token) {
        return fetch(url, {
          method: method,
          credentials: 'same-origin',
          headers: { 'Content-Type':'application/json', 'X-Install-Token': token },
          body: body === undefined ? undefined : JSON.stringify(body)
        });
      }).then(function (response) {
        if (!forceRefresh && (response.status === 401 || response.status === 403)) {
          return send(true);
        }
        if (!response.ok) {
          return response.text().then(function (message) {
            // FastAPI answers {"detail": "..."}: show the sentence, not the JSON.
            try { message = JSON.parse(message).detail || message; } catch (e) { /* plain text */ }
            throw new Error(message || ('Request failed (' + response.status + ')'));
          });
        }
        return response;
      });
    }
    return send(false);
  }

  /* ── Tiny utilities ──────────────────────────────────────── */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }
  function toast(msg, err) {
    if (typeof window.showToast === 'function') { window.showToast(msg); return; }
    var d = document.createElement('div');
    d.textContent = msg;
    Object.assign(d.style, { position:'fixed', bottom:'20px', right:'20px', zIndex:'99999',
      background: err ? 'var(--danger)' : 'var(--violet)', color:'#fff',
      padding:'10px 18px', borderRadius:'10px', fontSize:'13px',
      boxShadow:'0 4px 16px rgba(0,0,0,.4)', maxWidth:'340px' });
    document.body.appendChild(d);
    setTimeout(function () { d.remove(); }, 3200);
  }
  function fmt(bytes) {
    if (!bytes) return '0 MB';
    var mb = bytes / 1048576;
    return mb > 1024 ? (mb/1024).toFixed(1) + ' GB' : mb.toFixed(0) + ' MB';
  }
  function fmtDate(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
  }
  function el(tag, attrs, css) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'text') e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    if (css) Object.assign(e.style, css);
    return e;
  }
  function q(root, id) {
    return (root || document).querySelector('#' + P + '-' + id);
  }
  function set(root, id, txt) {
    var e = q(root, id); if (e) e.textContent = txt;
  }

  /* ── CSS injection (design-system classes from backup.html inline styles) ── */
  function injectStyles() {
    if (document.getElementById('od-bk-styles')) return;
    var s = document.createElement('style');
    s.id = 'od-bk-styles';
    s.textContent = [
      '.cloud-orb{width:52px;height:52px;border-radius:16px;display:grid;place-items:center;',
      'flex-shrink:0;background:linear-gradient(150deg,var(--violet),var(--cyan));',
      'box-shadow:0 6px 20px -6px var(--violet)}',
      '.cloud-orb svg{width:26px;height:26px;color:#fff}',
      '.cloud-orb.off{background:var(--card-2);box-shadow:none}',
      '.cloud-orb.off svg{color:var(--fg-3)}',
      '.bk-conn{display:flex;align-items:center;gap:13px;padding:14px 0;',
      'border-bottom:1px solid var(--border)}',
      '.bk-conn:last-child{border-bottom:0}',
      '.bk-conn-ic{width:38px;height:38px;border-radius:10px;flex-shrink:0;',
      'display:grid;place-items:center;color:#fff}',
      '.bk-conn-ic.gh{background:#24292f}',
      '.bk-conn-ic.goog{background:#fff;border:1px solid var(--border)}',
      '.bk-conn-ic.s3{background:var(--warn)}',
      '.bk-conn.off .bk-conn-ic{filter:grayscale(1);opacity:.6}',
      '.bk-ctl{display:flex;align-items:center;justify-content:space-between;',
      'gap:16px;padding:13px 0;border-bottom:1px solid var(--border)}',
      '.bk-ctl:last-child{border-bottom:0}'
    ].join('');
    document.head.appendChild(s);
  }

  /* ── Card factory ────────────────────────────────────────── */
  // Returns {wrap, head, body} matching .card structure from design-system.css
  function card(title, sub, ic) {
    var wrap = el('div');
    wrap.className = 'card';
    Object.assign(wrap.style, { marginBottom:'16px' });
    // card-head
    var head = el('div');
    head.className = 'card-head';
    if (ic && typeof window.slmIcon === 'function') {
      var icEl = el('span', null,
        { color:'var(--violet)', width:'18px', height:'18px', display:'flex', flexShrink:'0' });
      icEl.innerHTML = window.slmIcon(ic);
      head.appendChild(icEl);
    }
    head.appendChild(el('h3', { text: title }));
    if (sub) head.appendChild(el('span', { text: sub }, { className:'sub' }));
    wrap.appendChild(head);
    var body = el('div');
    body.className = 'card-pad';
    wrap.appendChild(body);
    return { wrap: wrap, head: head, body: body };
  }

  /* ══════════════════════════════════════════════════════════
     HERO SECTION  (matches design: card.glass + cloud-orb + badge + stats)
  ══════════════════════════════════════════════════════════ */
  var moduleContext = { getToken:getToken, authMutation:authMutation, esc:esc, toast:toast, fmt:fmt, fmtDate:fmtDate, el:el, q:q, set:set, injectStyles:injectStyles, card:card, P:P, doBackupNow:doBackupNow, doExport:doExport, doSync:doSync, doDisconnect:doDisconnect, openOAuth:openOAuth, loadAll:loadAll, loadStatus:loadStatus, loadDestinations:loadDestinations, loadList:loadList };
  var SLMBackupLayout = window.SLMBackupLayout(moduleContext);
  Object.assign(moduleContext, SLMBackupLayout);
  var { buildHero, buildKPIs, injectConnIcons, buildConnections, renderConnections, buildConnRow, buildScope, fmtScope, fmtDest, buildHistory, renderHistory } = SLMBackupLayout;
  /* ══════════════════════════════════════════════════════════
     ACTIONS: backup now, sync, disconnect, oauth
  ══════════════════════════════════════════════════════════ */
  function doBackupNow(root) {
    var btn = q(root, 'btn-now'); if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }
    authMutation('/api/backup/recovery/create', 'POST')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        toast(d.success ? 'Backup created: ' + esc(d.filename || '') : 'Backup failed', !d.success);
        if (btn) { btn.disabled = false; btn.textContent = 'Back up now'; }
        loadAll(root);
      })
      .catch(function () {
        toast('Backup failed', true);
        if (btn) { btn.disabled = false; btn.textContent = 'Back up now'; }
      });
  }

  function doSync(root) {
    authMutation('/api/backup/sync', 'POST')
      .then(function (r) { return r.json(); })
      .then(function (d) { toast(d.success ? 'Sync started' : 'Sync failed: ' + esc(d.error || ''), !d.success); })
      .catch(function () { toast('Sync error', true); });
  }

  function doExport(button) {
    if (button) button.disabled = true;
    authMutation('/api/backup/recovery/export', 'POST')
      .then(function (response) {
        return response.blob().then(function (blob) {
          return { blob: blob, disposition: response.headers.get('Content-Disposition') || '' };
        });
      })
      .then(function (download) {
        var match = download.disposition.match(/filename="?([^";]+)"?/i);
        var link = document.createElement('a');
        var objectUrl = URL.createObjectURL(download.blob);
        link.href = objectUrl;
        link.download = match ? match[1] : 'superlocalmemory-recovery.zip';
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(objectUrl);
        toast('Backup export downloaded');
      })
      .catch(function (err) { toast((err && err.message) || 'Backup export failed', true); })
      .finally(function () { if (button) button.disabled = false; });
  }

  function doDisconnect(root, destId) {
    window.confirmDestructive({
      title: 'Disconnect cloud destination',
      target: destId,
      consequence: 'Existing backups are not deleted.',
      confirmLabel: 'Disconnect',
    }).then(function(confirmed) {
      if (!confirmed) return;
      authMutation('/api/backup/disconnect/' + encodeURIComponent(destId), 'DELETE')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        toast(d.success ? 'Disconnected' : 'Disconnect failed: ' + esc(d.error || ''), !d.success);
        loadDestinations(root);
      })
      .catch(function () { toast('Disconnect failed', true); });
    });
  }

  function openOAuth(root, oauthPath, providerLabel) {
    var popup = window.open(oauthPath, providerLabel + '-oauth', 'width=560,height=700');
    if (!popup) { toast('Popup blocked — allow popups for this page', true); return; }
    var timer = setInterval(function () {
      if (!popup || popup.closed) {
        clearInterval(timer);
        // A closed popup is not proof of success; refresh runtime truth only.
        loadDestinations(root, true);
      }
    }, 800);
  }

  /* ══════════════════════════════════════════════════════════
     DATA LOADING
  ══════════════════════════════════════════════════════════ */
  function loadStatus(root) {
    if (window.SLMBackupRecovery) window.SLMBackupRecovery.outcome(root);
    fetch('/api/backup/status').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;

        // Hero stats
        var lastStr = d.last_backup ? fmtDate(d.last_backup) : 'Never';
        set(root, 'hero-last',    lastStr);
        var next = !d.enabled
          ? 'Manual only'
          : (d.last_backup && d.interval_hours
            ? new Date(new Date(d.last_backup).getTime() + d.interval_hours * 3600000).toLocaleString()
            : 'Scheduled');
        set(root, 'hero-next',    next);
        set(root, 'hero-encrypt', 'Plain SQLite here · encrypted in cloud');

        // Hero title + badge: reflect configured and witnessed sync state.
        var hasCloud = d.cloud_destinations && d.cloud_destinations.length > 0;
        var allSuccessful = hasCloud && d.cloud_destinations.every(function (dest) {
          return dest.last_sync_status === 'success' && Boolean(dest.last_sync_at);
        });
        var failed = hasCloud && d.cloud_destinations.some(function (dest) {
          return dest.last_sync_status === 'failed';
        });
        var titleEl = q(root, 'cloud-title');
        if (titleEl) titleEl.textContent = hasCloud ? 'Cloud destination configured' : 'No cloud destination';
        var badgeEl = q(root, 'cloud-badge');
        if (badgeEl) {
          badgeEl.className = 'badge ' + (failed ? 'danger' : allSuccessful ? 'ok' : 'warn');
          badgeEl.replaceChildren();
          var dot = el('span'); dot.className = 'dot';
          badgeEl.appendChild(dot);
          badgeEl.appendChild(document.createTextNode(
            failed
              ? ' One or more syncs failed'
              : allSuccessful
                ? ' Latest reported syncs succeeded'
                : hasCloud ? ' Sync incomplete or pending' : ' Not connected'
          ));
        }
        var subEl = q(root, 'cloud-sub');
        if (subEl) {
          if (hasCloud) {
            var dest = d.cloud_destinations[0];
            var destStr = dest.display_name || dest.destination_type || 'cloud';
            subEl.textContent = 'Copies are encrypted on this computer before upload to your private ' +
              destStr + '. The provider stores only ciphertext; keep your recovery key safe.';
          } else {
            subEl.textContent = 'Local snapshots are plaintext SQLite. Cloud copies are encrypted on this computer before upload.';
          }
        }
        var orb = q(root, 'orb');
        if (orb) orb.classList.toggle('off', !hasCloud);

        // KPI values (matched to new KPI ids)
        var cnt = d.backup_count || d.learning_backup_count || 0;
        set(root, 'kpi-total',  String(cnt));
        var totalMb = d.total_size_mb || 0;
        set(root, 'kpi-size',   totalMb > 1024
          ? (totalMb / 1024).toFixed(1) + ' GB'
          : totalMb.toFixed(0) + ' MB');
        set(root, 'kpi-retain', (d.max_backups || cnt) + ' snapshots');
        set(root, 'kpi-count',  String(cnt));

        if (d.backups) renderHistory(root, d.backups);

        // Sync schedule segment buttons
        var hours = d.interval_hours || 168;
        var active = !d.enabled ? 'manual' : hours <= 24 ? 'daily' : 'weekly';
        ['daily','weekly','manual'].forEach(function (s) {
          var b = document.getElementById(P + '-seg-' + s);
          if (b) b.className = (s === active ? 'active' : '');
        });
      }).catch(function () {});
  }

  function loadDestinations(root, forceReload) {
    fetch('/api/backup/destinations').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var dests = d && d.destinations ? d.destinations : [];
        renderConnections(root, dests);
        if (forceReload) {
          toast('Destination status refreshed');
          // Re-load status without inferring whether the popup succeeded.
          loadStatus(root);
        }
      }).catch(function () {});
  }

  function loadList(root) {
    fetch('/api/backup/recovery/list').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.backups) renderHistory(root, d.backups);
      }).catch(function () {});
  }

  function loadAll(root) {
    loadStatus(root);
    loadDestinations(root);
    loadList(root);
  }

  /* ══════════════════════════════════════════════════════════
     MAIN RENDER
  ══════════════════════════════════════════════════════════ */
  function odRenderBackup(container) {
    if (!container) return;
    injectStyles();
    // Clear skeleton / prior render
    Array.from(container.children).forEach(function (c) { c.style.display = 'none'; });

    var hub = el('div', null, { padding:'26px', maxWidth:'960px' });

    // Page head — matches design's <div class="page-head"> block
    var pageHead = el('div');
    pageHead.className = 'page-head';
    pageHead.appendChild(el('h2', { text:'Backup & cloud sync' }));
    var desc = el('p');
    desc.textContent = 'Your memory lives on this machine as plaintext SQLite snapshots. ' +
      'Copies sent to a private GitHub repository or your Google Drive are encrypted on this ' +
      'computer before upload, so the provider stores only ciphertext. Keep your recovery key: ' +
      'without it those copies cannot be restored on another computer.';
    pageHead.appendChild(desc);
    hub.appendChild(pageHead);

    hub.appendChild(buildHero(hub));
    if (typeof window.odRenderBackupEncryption === 'function') {
      hub.appendChild(window.odRenderBackupEncryption(authMutation, toast));
    }
    hub.appendChild(buildKPIs());

    // Two-column area: Connections | Scope
    var twoCol = el('div');
    twoCol.className = 'grid';
    twoCol.style.gridTemplateColumns = '1fr 1fr';
    twoCol.style.alignItems = 'start';
    twoCol.appendChild(buildConnections(hub));
    twoCol.appendChild(buildScope());
    hub.appendChild(twoCol);

    hub.appendChild(buildHistory());
    container.insertBefore(hub, container.firstChild);
    loadAll(hub);
  }

  /* ══════════════════════════════════════════════════════════
     OVERLAY — called from od-settings.js Backup group
  ══════════════════════════════════════════════════════════ */
  function odOpenBackupDashboard() {
    var existing = document.getElementById(OVERLAY_ID);
    if (existing) { existing.style.display = 'flex'; loadAll(existing); return; }

    var overlay = el('div', { id: OVERLAY_ID }, {
      position:'fixed', inset:'0', zIndex:'9000',
      background:'var(--page)', overflowY:'auto',
      display:'flex', flexDirection:'column'
    });

    // Top bar with close button
    var topbar = el('div', null, { position:'sticky', top:'0', zIndex:'9001',
      background:'var(--card)', borderBottom:'1px solid var(--border)',
      display:'flex', alignItems:'center', gap:'12px', padding:'0 22px', height:'52px' });

    var backBtn = el('button', { type:'button' }); backBtn.className = 'btn ghost sm';
    backBtn.textContent = '← Settings';
    backBtn.addEventListener('click', function () {
      overlay.style.display = 'none';
    });
    topbar.appendChild(backBtn);
    topbar.appendChild(el('span', { text:'Backup & Cloud' },
      { fontWeight:'640', fontSize:'15px' }));

    var escHint = el('span', { text:'Esc to close' },
      { marginLeft:'auto', fontSize:'11.5px', color:'var(--fg-3)' });
    topbar.appendChild(escHint);

    overlay.appendChild(topbar);

    // Content area
    var content = el('div', null, { flex:'1', padding:'22px' });
    overlay.appendChild(content);
    document.body.appendChild(overlay);

    // ESC key close
    document.addEventListener('keydown', function onEsc(e) {
      if (e.key === 'Escape' && overlay.style.display !== 'none') {
        overlay.style.display = 'none';
      }
    });

    odRenderBackup(content);
  }

  /* ══════════════════════════════════════════════════════════
     BOOT
  ══════════════════════════════════════════════════════════ */
  window.odRenderBackup      = odRenderBackup;
  window.odOpenBackupDashboard = odOpenBackupDashboard;

  document.addEventListener('DOMContentLoaded', function () {
    // Wire into a backup-pane if the shell gains one in future
    var pane = document.getElementById('backup-pane');
    if (pane) odRenderBackup(pane);
  });

}());
