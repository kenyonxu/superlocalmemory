// Extracted od-operations.js feature scope; initialized once with the pane helpers.
(function () {
  'use strict';
  var ctx;
  var esc, timeAgo, fmtNum, safeIcon, injectCtlStyle;
  window.SLMOperationsCompliance = function (helpers) {
    ctx = helpers;
    ({ esc, timeAgo, fmtNum, safeIcon, injectCtlStyle } = ctx);
    return { resetAudit:function(){_allAuditRows=[]},populateComplianceKpi:populateComplianceKpi, populateRetentionPolicies:populateRetentionPolicies, normalizeAuditEvent:normalizeAuditEvent, populateAuditTrail:populateAuditTrail, showAuditChainStatus:showAuditChainStatus, fetchAndRenderAudit:fetchAndRenderAudit, renderAuditRows:renderAuditRows, wireAuditFilter:wireAuditFilter, wireRetentionDeleteBtns:wireRetentionDeleteBtns, wireNewPolicyBtn:wireNewPolicyBtn, wireGdprBtns:wireGdprBtns, openRetentionPolicyModal:openRetentionPolicyModal };
  };
  function populateComplianceKpi(comp) {
    var kpiEl = document.getElementById('od-comp-kpi');
    if (!kpiEl) return;

    var auditCount = comp ? Number(comp.audit_events_count || 0)        : 0;
    var retCount   = comp && comp.retention_policies ? comp.retention_policies.length : 0;
    var abacCount  = comp ? Number(comp.abac_policies_count || 0)       : 0;

    kpiEl.innerHTML = (
      '<div class="card kpi">' +
        // Design uses data-ic="shield". Shield is not in the icon registry (od-shell.js).
        // safeIcon('health') (pulse/waveform) is the closest semantic match for audit monitoring.
        '<div class="label">' + safeIcon('health') + ' Audit events</div>' +
        '<div class="value num">' + fmtNum(auditCount) + '</div>' +
      '</div>' +
      '<div class="card kpi">' +
        '<div class="label">' + safeIcon('clock') + ' Retention policies</div>' +
        '<div class="value num">' + fmtNum(retCount) + '</div>' +
      '</div>' +
      '<div class="card kpi">' +
        '<div class="label">' + safeIcon('lock') + ' ABAC rules</div>' +
        '<div class="value num">' + fmtNum(abacCount) + '</div>' +
      '</div>'
    );
  }
  /**
   * Populate the retention policies table.
   * CRIT(c): policy name, category, action go through esc().
   * Empty state explains how to add policies via MCP tool.
   */
  function populateRetentionPolicies(comp) {
    var retTbody = document.querySelector('#od-ret tbody');
    if (!retTbody) return;

    var policies = (comp && comp.retention_policies) || [];

    if (policies.length === 0) {
      retTbody.innerHTML =
        '<tr><td colspan="5" class="muted-info" style="text-align:center;padding:20px">' +
        'No retention policies yet. Use New policy to add one.' +
        '</td></tr>';
      return;
    }

    var ACTION_CLS = { archive: 'neutral', tombstone: 'danger', notify: 'warn' };

    // Build rows then wire delete buttons post-inject (avoids inline onclick — CSP safe)
    retTbody.innerHTML = policies.map(function (p) {
      var ac = ACTION_CLS[p.action] || 'neutral';
      return (
        '<tr>' +
          '<td><b>' + esc(p.name || '') + '</b></td>' +
          '<td class="num">' + esc(String(p.retention_days || '—')) + '</td>' +
          '<td><span class="badge neutral">' + esc(p.category || 'all') + '</span></td>' +
          '<td><span class="badge ' + ac + '">' + esc(p.action || '') + '</span></td>' +
          // Task E: per-row delete — data-pol-name used by wireRetentionDeleteBtns()
          '<td style="text-align:right">' +
            '<button class="btn sm danger" data-pol-name="' + esc(p.name || '') + '">' +
              'Delete' +
            '</button>' +
          '</td>' +
        '</tr>'
      );
    }).join('');

    // Task E: wire delete buttons (CSP-safe addEventListener after innerHTML set)
    wireRetentionDeleteBtns(retTbody);
  }

  // Cache of all audit rows (for client-side All/Denied filter)
  var _allAuditRows = [];
  function normalizeAuditEvent(ev) {
    var md = ev.metadata || {};
    if (typeof md === 'string') { try { md = JSON.parse(md); } catch (_) { md = {}; } }
    return Object.assign({}, ev, {
      event_type: ev.event_type || ev.operation || 'Not recorded',
      actor: ev.actor || ev.agent_id || 'Not recorded',
      action: ev.action || ev.operation || 'Not recorded',
      target: ev.target || md.target || md.fact_id || md.memory_id || md.target_id ||
        (Array.isArray(md.fact_ids) ? md.fact_ids.join(', ') : typeof md.fact_ids === 'string' ? md.fact_ids : '') || 'Not recorded',
      result: ev.result || md.outcome || md.result || 'Not recorded',
      profile_id: ev.profile_id || 'Not recorded'
    });
  }
  /**
   * Task C: Populate the audit trail table.
   * auditFull: response from /api/compliance/audit (preferred, up to 100 events).
   * comp: response from /api/compliance/status (fallback, recent_audit_events).
   * CRIT(c): actor, action, target, result all through esc().
   */
  function populateAuditTrail(comp, auditFull) {
    var auditTbody = document.getElementById('od-audit-body');
    if (!auditTbody) return;

    // Prefer full audit endpoint data; fall back to status data
    var events = (auditFull && auditFull.events) || (comp && comp.recent_audit_events) || [];
    events = events.map(normalizeAuditEvent);
    _allAuditRows = events;

    renderAuditRows(auditTbody, events);
  }

  /**
   * Task C: Render the chain-integrity badge inside the audit trail card header.
   * Appended to #od-audit-chain-status if it exists.
   */
  function showAuditChainStatus(auditFull) {
    var el = document.getElementById('od-audit-chain-status');
    if (!el || !auditFull) return;
    if (typeof auditFull.chain_verified !== 'boolean' && !auditFull.integrity_label) {
      el.textContent = 'Chain verification unavailable';
      return;
    }
    // Say what the check covered (server label); never "verified" by default.
    var ok = auditFull.chain_verified === true;
    var badge = document.createElement('span');
    badge.className = 'badge ' + (ok ? 'ok' : 'danger');
    badge.style.marginLeft = '8px';
    badge.textContent = auditFull.integrity_label || (ok ? 'Order verified' : 'Integrity not checked');
    el.textContent = '';
    el.appendChild(badge);
  }

  /** Task C: re-fetch audit trail from API with a specific event_type filter. */
  function fetchAndRenderAudit(eventType) {
    var auditTbody = document.getElementById('od-audit-body');
    if (!auditTbody) return;
    auditTbody.innerHTML = '<tr><td colspan="7" class="muted-info" style="text-align:center;padding:16px">Loading…</td></tr>';
    var url = '/api/compliance/audit?limit=100';
    if (eventType && eventType !== 'all') url += '&event_type=' + encodeURIComponent(eventType);
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        _allAuditRows = (d.events || []).map(normalizeAuditEvent);
        renderAuditRows(auditTbody, _allAuditRows);
        showAuditChainStatus(d);
      })
      .catch(function () {
        if (auditTbody) auditTbody.innerHTML = '<tr><td colspan="7" class="muted-info" style="text-align:center;padding:16px">Failed to load audit trail.</td></tr>';
      });
  }

  /** Render audit table rows into tbody, filtering by mode ('all' or 'denied'). */
  function renderAuditRows(tbody, events) {
    if (events.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="7" class="muted-info" style="text-align:center;padding:20px">' +
        'No audit events recorded yet.</td></tr>';
      return;
    }

    var EV_CLS = {
      recall:               'cyan',
      remember:             'ok',
      delete:               'danger',
      lifecycle_transition: 'warn',
      access_denied:        'danger',
      retention_enforced:   'warn',
    };

    tbody.innerHTML = events.slice(0, 50).map(function (ev) {
      var evc      = EV_CLS[ev.event_type] || 'neutral';
      var isOk     = ev.result === 'success' || ev.result === 'allowed';
      var isDenied = ev.result === 'denied'  || ev.result === 'error';
      var resultCls = isOk ? 'ok' : isDenied ? 'danger' : 'neutral';
      // CRIT(c): all user-sourced strings through esc()
      return (
        '<tr>' +
          '<td class="mono muted-info" style="font-size:12px">' + esc(ev.timestamp || '') + '</td>' +
          '<td><span class="badge ' + evc + '">' + esc(ev.event_type || '') + '</span></td>' +
          '<td class="mono" style="font-size:12px">' + esc(ev.actor || '') + '</td>' +
          '<td>' + esc(ev.action || '') + '</td>' +
          '<td class="muted-info">' + esc(ev.target || '') + '</td>' +
          '<td><span class="badge ' + resultCls + '">' + esc(ev.result || '') + '</span></td>' +
          '<td>' + esc(ev.profile_id || 'Not recorded') + '</td>' +
        '</tr>'
      );
    }).join('');
  }
  /**
   * Task C: Wire audit filters.
   * - All/Denied segment: client-side filter (fast).
   * - Event-type select (#od-audit-evt-filter): calls API to get filtered results.
   */
  function wireAuditFilter(auditFull) {
    var filterEl = document.getElementById('od-audit-filter');
    var tbody    = document.getElementById('od-audit-body');
    if (!filterEl || !tbody) return;

    // All / Denied segment buttons — client-side
    filterEl.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var mode = btn.getAttribute('data-filter');
        filterEl.querySelectorAll('button').forEach(function (b) {
          b.classList.remove('active');
        });
        btn.classList.add('active');

        var rows = (mode === 'denied')
          ? _allAuditRows.filter(function (ev) {
              return ev.result === 'denied' || ev.result === 'error' ||
                     (ev.event_type && ev.event_type.indexOf('denied') >= 0);
            })
          : _allAuditRows;

        renderAuditRows(tbody, rows);
      });
    });

    // Task C: Event-type select — re-fetches from API
    var evtSelect = document.getElementById('od-audit-evt-filter');
    if (evtSelect) {
      evtSelect.addEventListener('change', function () {
        fetchAndRenderAudit(evtSelect.value);
      });
    }
  }

  /**
   * Task E: Wire delete buttons injected by populateRetentionPolicies.
   * Data attribute data-pol-name carries the policy name (already esc'd in innerHTML).
   * Uses confirmDestructive before calling DELETE /api/compliance/retention-policy?name=.
   */
  function wireRetentionDeleteBtns(tbody) {
    tbody.querySelectorAll('[data-pol-name]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var policyName = btn.getAttribute('data-pol-name');
        window.confirmDestructive({
          title: 'Delete retention policy',
          target: policyName,
          consequence: 'This policy will no longer be enforced on any memory.',
          confirmLabel: 'Delete',
        }).then(function (confirmed) {
          if (!confirmed) return;
          btn.disabled = true;
          fetch('/api/compliance/retention-policy?name=' + encodeURIComponent(policyName), {
            method: 'DELETE',
          })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d && d.success !== false) {
                var pane = document.getElementById('operations-pane');
                if (pane && typeof window.odRenderOperations === 'function') {
                  window.odRenderOperations(pane);
                }
              } else {
                btn.disabled = false;
                if (typeof console !== 'undefined') {
                  console.error('[od-ops] retention delete failed:', d && d.error);
                }
              }
            })
            .catch(function () { btn.disabled = false; });
        });
      });
    });
  }

  /**
   * Wire the "New policy" button to the retention-policy form.
   */
  function wireNewPolicyBtn() {
    var btn = document.getElementById('od-new-policy-btn');
    if (!btn) return;
    btn.addEventListener('click', openRetentionPolicyModal);
  }

  /**
   * Wire the GDPR data-privacy controls: export (download), run retention now,
   * and erase-this-profile (typed confirmation). All dashboard-operable.
   */
  function wireGdprBtns() {
    var summary = document.getElementById('od-gdpr-summary');
    var exportBtn = document.getElementById('od-gdpr-export-btn');
    var runBtn = document.getElementById('od-retention-run-btn');
    var eraseBtn = document.getElementById('od-gdpr-erase-btn');

    if (exportBtn) {
      exportBtn.addEventListener('click', function () {
        // Fetch the file so the page can say what really happened: a failed
        // export used to print "Export started" and deliver nothing.
        if (typeof window.slmDownloadExport !== 'function') {
          // core.js is served from cache and predates this file.
          if (summary) summary.textContent = 'Export failed: reload this page (hard refresh) to finish updating the dashboard.';
          return;
        }
        exportBtn.disabled = true;
        if (summary) summary.textContent = 'Preparing your export… a large store takes a while.';
        window.slmDownloadExport('/api/compliance/gdpr/export').then(function (result) {
          if (summary) summary.textContent = window.slmExportMessage(result);
          if (!result.ok && typeof showToast === 'function') showToast(window.slmExportMessage(result));
        }).catch(function (error) {
          if (summary) summary.textContent = window.slmExportMessage(
            { ok: false, error: (error && error.message) || 'unexpected error' });
        }).then(function () { exportBtn.disabled = false; });
      });
    }

    if (runBtn) {
      runBtn.addEventListener('click', function () {
        runBtn.disabled = true; runBtn.textContent = 'Running…';
        fetch('/api/compliance/retention/enforce', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!summary) return;
            // Task E: surface all returned counts from the enforce response
            if (d && d.success) {
              var parts = [];
              if (d.archived != null)    parts.push('archived ' + fmtNum(d.archived));
              if (d.tombstoned != null)  parts.push('tombstoned ' + fmtNum(d.tombstoned));
              if (d.notified != null)    parts.push('flagged ' + fmtNum(d.notified));
              if (d.evaluated != null)   parts.push('evaluated ' + fmtNum(d.evaluated));
              if (d.policies_run != null) parts.push('policies run: ' + fmtNum(d.policies_run));
              summary.textContent = 'Retention applied — ' + (parts.join(', ') || 'no changes needed') + '.';
            } else {
              summary.textContent = 'Retention failed: ' + ((d && d.error) || 'unknown');
            }
          })
          .catch(function () { if (summary) summary.textContent = 'Retention run failed. Check console.'; })
          .finally(function () { runBtn.disabled = false; runBtn.textContent = 'Run retention now'; });
      });
    }

    if (eraseBtn) {
      eraseBtn.addEventListener('click', async function () {
        // Resolve the active profile so the confirmation is unambiguous.
        var profile = 'default';
        try {
          var s = await fetch('/status', { cache: 'no-store' }).then(function (r) { return r.json(); });
          profile = s.profile || s.active_profile || 'default';
        } catch (e) {}
        if (profile === 'default') {
          window.alert('The default profile cannot be erased.');
          return;
        }
        var typed = window.prompt(
          'This PERMANENTLY erases ALL data for profile "' + profile + '".\n' +
          'This cannot be undone.\n\nType the profile name to confirm:');
        if (typed !== profile) {
          if (typed !== null && summary) summary.textContent = 'Erase cancelled — name did not match.';
          return;
        }
        eraseBtn.disabled = true; eraseBtn.textContent = 'Erasing…';
        fetch('/api/compliance/gdpr/erase', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: profile }),
        })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (summary) renderEraseOutcome(summary, profile, d);
            if (d && d.success) setTimeout(function () { window.location.reload(); }, 900);
          })
          .catch(function () { if (summary) summary.textContent = 'Erase failed. Check console.'; })
          .finally(function () { eraseBtn.disabled = false; eraseBtn.textContent = 'Erase this profile…'; });
      });
    }
  }

  /**
   * OD-styled "New retention policy" modal → POST /api/compliance/retention-policy.
   * Non-technical users define GDPR/retention rules without a CLI. On success
   * the Compliance tab reloads so the new policy appears immediately.
   */
  function openRetentionPolicyModal() {
    var existing = document.getElementById('od-retention-overlay');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.id = 'od-retention-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:11000;display:flex;align-items:center;' +
      'justify-content:center;background:rgba(0,0,0,0.45);backdrop-filter:blur(2px)';

    var card = document.createElement('div');
    card.style.cssText =
      'width:min(460px,93vw);background:var(--card,#1a1f2e);color:var(--fg,#e8ecf3);' +
      'border:1px solid var(--border,rgba(255,255,255,0.1));border-radius:14px;' +
      'box-shadow:0 20px 60px rgba(0,0,0,0.4);padding:22px 22px 18px';

    var fieldStyle =
      'width:100%;box-sizing:border-box;padding:9px 11px;font-size:0.9rem;margin-top:4px;' +
      'background:var(--page,rgba(255,255,255,0.04));color:var(--fg,#e8ecf3);' +
      'border:1px solid var(--border,rgba(255,255,255,0.14));border-radius:8px;outline:none';
    var labelStyle = 'display:block;margin-top:12px;font-size:0.8rem;color:var(--fg-3,#8b93a7)';

    card.innerHTML =
      '<h3 style="margin:0 0 4px;font-size:1.05rem;font-weight:600">New retention policy</h3>' +
      '<p style="margin:0 0 8px;font-size:0.8125rem;color:var(--fg-3,#8b93a7)">' +
        'Automatically age out memories older than a set period — GDPR-style retention.</p>' +
      '<label style="' + labelStyle + '">Policy name' +
        '<input id="od-rp-name" type="text" maxlength="60" placeholder="e.g. GDPR 90-day" style="' + fieldStyle + '"></label>' +
      '<label style="' + labelStyle + '">Retain for (days)' +
        '<input id="od-rp-days" type="number" min="1" value="90" style="' + fieldStyle + '"></label>' +
      '<label style="' + labelStyle + '">Framework' +
        '<select id="od-rp-cat" style="' + fieldStyle + '">' +
          '<option value="gdpr">GDPR</option><option value="hipaa">HIPAA</option>' +
          '<option value="sox">SOX</option><option value="custom">Custom</option>' +
        '</select></label>' +
      '<label style="' + labelStyle + '">When a memory expires' +
        '<select id="od-rp-action" style="' + fieldStyle + '">' +
          '<option value="archive">Archive (keep, hide from recall)</option>' +
          '<option value="tombstone">Tombstone (mark deleted)</option>' +
          '<option value="notify">Notify only (no change)</option>' +
        '</select></label>' +
      '<div id="od-rp-err" style="min-height:18px;margin:8px 2px 0;font-size:0.75rem;color:#ff6b6b"></div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px">' +
        '<button type="button" id="od-rp-cancel" style="padding:8px 14px;font-size:0.85rem;border-radius:8px;cursor:pointer;background:transparent;color:var(--fg-3,#8b93a7);border:1px solid var(--border,rgba(255,255,255,0.14))">Cancel</button>' +
        '<button type="button" id="od-rp-create" style="padding:8px 16px;font-size:0.85rem;border-radius:8px;cursor:pointer;background:var(--violet,#7c5cff);color:#fff;border:1px solid transparent;font-weight:600">Create policy</button>' +
      '</div>';

    overlay.appendChild(card);
    document.body.appendChild(overlay);
    setTimeout(function () { var n = document.getElementById('od-rp-name'); if (n) n.focus(); }, 30);

    function close() { document.removeEventListener('keydown', onKey); overlay.remove(); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    var errEl = document.getElementById('od-rp-err');
    var createBtn = document.getElementById('od-rp-create');

    async function submit() {
      var name = (document.getElementById('od-rp-name').value || '').trim();
      var days = parseInt(document.getElementById('od-rp-days').value, 10);
      var cat = document.getElementById('od-rp-cat').value;
      var action = document.getElementById('od-rp-action').value;
      if (!name) { errEl.textContent = 'Please enter a policy name.'; return; }
      if (!(days >= 1)) { errEl.textContent = 'Retention days must be a positive number.'; return; }
      createBtn.disabled = true; createBtn.textContent = 'Creating…'; errEl.textContent = '';
      try {
        var r = await fetch('/api/compliance/retention-policy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, retention_days: days, category: cat, action: action }),
        });
        var data = await r.json().catch(function () { return {}; });
        if (!r.ok || data.success === false) {
          errEl.textContent = (data && data.error) || 'Failed to create policy.';
          createBtn.disabled = false; createBtn.textContent = 'Create policy';
          return;
        }
        close();
        if (typeof window.odRenderOperations === 'function') {
          // Reload the Operations pane so the new policy shows in the table.
          var pane = document.getElementById('operations-pane');
          if (pane) window.odRenderOperations(pane);
        }
      } catch (e) {
        errEl.textContent = 'Network error creating policy.';
        createBtn.disabled = false; createBtn.textContent = 'Create policy';
      }
    }

    document.getElementById('od-rp-cancel').addEventListener('click', close);
    createBtn.addEventListener('click', submit);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    document.addEventListener('keydown', onKey);
  }


  function renderEraseOutcome(summary, profile, d) {
    summary.textContent = '';
    var report = (d && d.erasure_report) || {};
    var remains = report.remains || [];
    if (d && d.success) {
      summary.textContent = 'Profile "' + profile + '" erased completely. Reloading…';
      return;
    }
    var head = document.createElement('div');
    head.textContent = d && d.erasure_report
      ? 'Erase INCOMPLETE for profile "' + profile + '". What is listed below is still on this computer.'
      : 'Erase failed: ' + ((d && d.error) || 'unknown');
    summary.appendChild(head);
    if (remains.length) {
      var list = document.createElement('ul');
      remains.forEach(function (item) {
        var li = document.createElement('li');
        li.textContent = item.what + ' ' + item.retry;
        list.appendChild(li);
      });
      summary.appendChild(list);
    }
    var removed = report.removed || {};
    var parts = Object.keys(removed).slice(0, 12).map(function (k) { return k + ': ' + removed[k]; });
    if (parts.length) {
      var gone = document.createElement('div');
      gone.textContent = 'Removed so far: ' + parts.join(', ');
      summary.appendChild(gone);
    }
  }

  window.slmRenderEraseOutcome = renderEraseOutcome;
}());
