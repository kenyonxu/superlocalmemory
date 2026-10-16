// od-operations.js — OD Operations pane renderer v2 (design-faithful)
// Renders into window.odRenderOperations(container).
//
// Endpoints (confirmed with curl against daemon :8765):
//   GET  /api/lifecycle/status → {available, active_profile, total_memories,
//                                  states: {archive, cold, warm},
//                                  recent_transitions: [],
//                                  age_stats: {warm: {avg_days, min_days, max_days}, ...}}
//   POST /api/lifecycle/compact → {status: "not_implemented"} — wired to button only
//   GET  /api/trust/stats       → {avg_trust_score, total_signals, enforcement, by_signal_type}
//   GET  /api/compliance/status → {available, audit_events_count, recent_audit_events,
//                                   retention_policies, abac_policies_count}
//
// NOTE on lifecycle states: API returns keys 'archive', 'cold', 'warm' (not 'active'/'tombstoned').
// Display maps: archive → Archived. Missing keys default to 0.
//
// CRIT fixes applied:
//   (a) Lifecycle counts always from /api/lifecycle/status — no hardcoded state numbers.
//   (b) Compact buttons wired ONLY to click via addEventListener — never auto-fired.
//   (c) All API strings (policy names, actors, actions, targets) pass through esc() for XSS safety.
//
// Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar — AGPL-3.0

/* global window, document, fetch, Promise */
(function () {
  'use strict';

  // ─── Helpers ──────────────────────────────────────────────────────────────

  /** HTML-escape a value before inserting via innerHTML. */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** Format a Unix-seconds float or ISO string as "Xm ago". */
  function timeAgo(ts) {
    if (!ts) return '—';
    var ms = (typeof ts === 'number') ? ts * 1000 : new Date(ts).getTime();
    var d = (Date.now() - ms) / 1000;
    if (d < 0) d = 0;
    if (d < 60)    return Math.floor(d) + 's ago';
    if (d < 3600)  return Math.floor(d / 60) + 'm ago';
    if (d < 86400) return Math.floor(d / 3600) + 'h ago';
    return Math.floor(d / 86400) + 'd ago';
  }

  /** Format a number with locale separators. */
  function fmtNum(n) { return Number(n || 0).toLocaleString(); }

  /** Get an inline SVG icon from the shell registry; fail-safe if shell absent. */
  function safeIcon(name) {
    return (typeof window.slmIcon === 'function') ? window.slmIcon(name) : '';
  }

  // ── .ctl style injection ────────────────────────────────────────────────────
  // Design defines .ctl via <style> block. Inject once so :last-child border rule works.
  var _ctlStyleInjected = false;
  function injectCtlStyle() {
    if (_ctlStyleInjected || document.getElementById('od-ops-ctl-style')) return;
    var s = document.createElement('style');
    s.id = 'od-ops-ctl-style';
    s.textContent =
      '.od-ops-ctl{display:flex;align-items:center;justify-content:space-between;' +
        'gap:16px;padding:14px 0;border-bottom:1px solid var(--border)}' +
      '.od-ops-ctl:last-child{border-bottom:0}';
    document.head.appendChild(s);
    _ctlStyleInjected = true;
  }

  // ─── Skeleton HTML ────────────────────────────────────────────────────────

  /**
   * Full static HTML for the operations pane — zero API data inside.
   * IDs use od-* prefix to avoid colliding with legacy Bootstrap IDs.
   * Tab switching uses data-od-tab / data-od-pane to isolate from shell nav.
   */
  var moduleContext = { esc:esc, timeAgo:timeAgo, fmtNum:fmtNum, safeIcon:safeIcon, injectCtlStyle:injectCtlStyle };
  var SLMOperationsLayout = window.SLMOperationsLayout(moduleContext);
  Object.assign(moduleContext, SLMOperationsLayout);
  var { buildSkeleton } = SLMOperationsLayout;
  function initTabs(root) {
    var buttons = root.querySelectorAll('[data-od-tab]');
    var panes   = root.querySelectorAll('[data-od-pane]');

    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = btn.getAttribute('data-od-tab');
        buttons.forEach(function (b) { b.classList.remove('active'); });
        panes.forEach(function (p) {
          p.classList.toggle('active', p.getAttribute('data-od-pane') === target);
        });
        btn.classList.add('active');
      });
    });
  }


  // ─── Lifecycle ────────────────────────────────────────────────────────────
  /**
   * Populate the lifecycle KPI strip.
   * API returns states: {archive, cold, warm}. 'active' and 'tombstoned' not in API.
   * Missing keys default to 0.
   */
  function populateLifecycleStates(lc) {
    var statesEl = document.getElementById('od-lc-states');
    if (!statesEl) return;

    var rawStates = (lc && lc.states) || {};

    // Labels are lowercase to match the approved design (design uses 'active' not 'Active').
    var STATE_MAP = [
      { key: 'active',     label: 'active',     cls: 'ok' },
      { key: 'warm',       label: 'warm',        cls: 'warn' },
      { key: 'cold',       label: 'cold',        cls: 'cyan' },
      { key: 'archive',    label: 'archived',    cls: 'neutral' },
      { key: 'tombstoned', label: 'tombstoned',  cls: 'danger' },
    ];

    statesEl.innerHTML = STATE_MAP.map(function (s) {
      var count = rawStates[s.key] != null ? rawStates[s.key] : 0;
      return (
        '<div class="card kpi">' +
          '<div class="label">' +
            '<span class="badge ' + s.cls + '" style="padding:1px 8px">' +
              '<span class="dot"></span>' + esc(s.label) +
            '</span>' +
          '</div>' +
          '<div class="value num" style="font-size:24px">' + fmtNum(count) + '</div>' +
        '</div>'
      );
    }).join('');
  }

  /**
   * Populate the age-by-state table from lifecycle age_stats.
   * Maps API key 'archive' to display label 'Archived'.
   */
  function populateLifecycleAges(lc) {
    var ageTbody = document.querySelector('#od-lc-ages tbody');
    if (!ageTbody) return;

    var ageStats = (lc && lc.age_stats) || {};
    var ageKeys  = Object.keys(ageStats);

    if (ageKeys.length === 0) {
      ageTbody.innerHTML =
        '<tr><td colspan="4" class="muted-info" style="text-align:center;padding:16px">' +
        'No age data available yet.</td></tr>';
      return;
    }

    ageTbody.innerHTML = ageKeys.map(function (k) {
      var ag = ageStats[k] || {};
      // Lowercase to match design badge style (design shows 'archived' not 'Archived')
      var displayLabel = (k === 'archive') ? 'archived' : esc(k);
      return (
        '<tr>' +
          '<td><span class="badge neutral">' + displayLabel + '</span></td>' +
          '<td class="num">' + esc(String(Number(ag.avg_days || 0).toFixed(1))) + '</td>' +
          '<td class="num dim">' + esc(String(Number(ag.min_days || 0).toFixed(1))) + '</td>' +
          '<td class="num dim">' + esc(String(Number(ag.max_days || 0).toFixed(1))) + '</td>' +
        '</tr>'
      );
    }).join('');
  }

  /**
   * Populate the recent transitions table.
   * API returns recent_transitions: [] in current live daemon.
   * Shows honest empty state; displays up to 20 rows when present.
   */
  function populateLifecycleTransitions(lc) {
    var transTbody = document.querySelector('#od-lc-trans tbody');
    if (!transTbody) return;

    var trans = (lc && lc.recent_transitions) || [];

    if (trans.length === 0) {
      transTbody.innerHTML =
        '<tr><td colspan="4" class="muted-info" style="text-align:center;padding:16px">' +
        'No recent transitions. Transitions appear here when memories change lifecycle state.' +
        '</td></tr>';
      return;
    }

    transTbody.innerHTML = trans.slice(0, 20).map(function (t) {
      var idLabel    = t.memory_id ? '#' + esc(String(t.memory_id)) : '—';
      var transition = t.transition ? esc(String(t.transition)) : '—';
      var reason     = t.reason ? esc(String(t.reason)) : '—';
      var when       = t.timestamp ? esc(timeAgo(t.timestamp)) : '—';
      return (
        '<tr>' +
          '<td class="mono muted-info">' + idLabel + '</td>' +
          '<td><span class="badge neutral">' + transition + '</span></td>' +
          '<td class="muted-info">' + reason + '</td>' +
          '<td class="muted-info">' + when + '</td>' +
        '</tr>'
      );
    }).join('');
  }

  /**
   * Populate all lifecycle sections.
   * Calls populateLifecycleStates, populateLifecycleAges, populateLifecycleTransitions.
   */
  function populateLifecycle(lc) {
    if (!lc) {
      var stEl = document.getElementById('od-lc-states');
      if (stEl) stEl.innerHTML = '<p class="muted">Lifecycle data unavailable.</p>';
      return;
    }
    populateLifecycleStates(lc);
    populateLifecycleAges(lc);
    populateLifecycleTransitions(lc);
  }

  // ─── Compact buttons ──────────────────────────────────────────────────────
  /**
   * Wire the compaction buttons via addEventListener.
   * CRIT(b): preview-btn and apply-btn only fire on explicit user click.
   * Current API returns {status:"not_implemented"} for POST /api/lifecycle/compact.
   */
  function wireCompactBtns() {
    var previewBtn = document.getElementById('od-compact-preview-btn');
    var applyBtn   = document.getElementById('od-compact-apply-btn');
    var summaryEl  = document.getElementById('od-compact-summary');
    var detailEl   = document.getElementById('od-compact-detail');

    if (previewBtn) {
      previewBtn.addEventListener('click', function () {
        previewBtn.disabled = true;
        previewBtn.textContent = 'Running…';

        fetch('/api/lifecycle/compact', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dry_run: true }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data && data.success === false) {
              if (summaryEl) summaryEl.innerHTML =
                '<span class="muted-info">' + esc(data.error || 'Compaction unavailable.') + '</span>';
              return;
            }
            var recs = Number(data.candidates || 0);
            if (summaryEl) {
              summaryEl.innerHTML =
                'Compaction preview: <b style="color:var(--fg)">' + fmtNum(recs) +
                '</b> of ' + fmtNum(data.total_facts || 0) +
                ' memories would be transitioned.';
            }
            if (detailEl) {
              var details = (data.transitions || []).slice(0, 10);
              if (details.length === 0) {
                detailEl.innerHTML =
                  '<p class="muted" style="font-size:13px">' +
                  'No compaction needed — all memories in optimal states.</p>';
              } else {
                detailEl.innerHTML = details.map(function (d) {
                  return (
                    '<div style="display:flex;justify-content:space-between;' +
                      'align-items:center;padding:8px 0;' +
                      'border-bottom:1px solid var(--border)">' +
                      '<span class="mono" style="font-size:12.5px">' +
                        esc(String(d.fact_id || '')) +
                      '</span>' +
                      '<span class="badge neutral">' +
                        esc(d.current || '') + ' → ' + esc(d.proposed || '') +
                      '</span>' +
                    '</div>'
                  );
                }).join('');
              }
            }
          })
          .catch(function (err) {
            if (summaryEl) summaryEl.textContent = 'Preview failed. Check console.';
            if (typeof console !== 'undefined') {
              console.error('[od-ops] compact preview error:', err);
            }
          })
          .finally(function () {
            previewBtn.disabled = false;
            previewBtn.textContent = 'Re-run dry-run';
          });
      });
    }

    if (applyBtn) {
      applyBtn.addEventListener('click', function () {
        window.confirmDestructive({
          title: 'Apply compaction',
          target: 'All eligible memories',
          consequence: 'Transitions memories to lower lifecycle states.',
          confirmLabel: 'Apply',
        }).then(function(confirmed) {
          if (!confirmed) return;
          applyBtn.disabled = true;
        applyBtn.textContent = 'Running…';

        fetch('/api/lifecycle/compact', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dry_run: false }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data && data.success === false) {
              if (summaryEl) summaryEl.innerHTML =
                '<span class="muted-info">' + esc(data.error || 'Compaction failed.') + '</span>';
              return;
            }
            var n = Number(data.applied || 0);
            if (summaryEl) {
              summaryEl.innerHTML =
                'Compaction complete: <b style="color:var(--ok)">' +
                fmtNum(n) + '</b> memories transitioned.';
            }
          })
          .catch(function (err) {
            if (summaryEl) summaryEl.textContent = 'Compaction failed. Check console.';
            if (typeof console !== 'undefined') {
              console.error('[od-ops] compact apply error:', err);
            }
          })
          .finally(function () {
            applyBtn.disabled = false;
            applyBtn.textContent = 'Apply compaction';
          });
        });
      });
    }
  }

  /**
   * Wire the "Restart daemon" button. POSTs /api/daemon/restart (which spawns a
   * detached `slm restart`), then polls /health until the fresh daemon is back
   * and reloads the dashboard. Wired via addEventListener — never auto-fired.
   */
  function restartDaemon(btn, summary) {
    var label = btn.textContent;
    window.confirmDestructive({
      title: 'Restart memory daemon',
      target: 'Memory daemon',
      consequence: 'Will be unavailable for a few seconds. Your memories are not affected.',
      confirmLabel: 'Restart',
    }).then(function(confirmed) {
      if (!confirmed) return;
      btn.disabled = true;
      btn.textContent = 'Restarting…';
      if (summary) summary.textContent = 'Restart requested — waiting for the daemon to come back…';

      fetch('/api/daemon/restart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (data) {
          if (data && data.success === false) {
            if (summary) summary.textContent = 'Restart failed: ' + (data.error || 'unknown error');
            btn.disabled = false; btn.textContent = label;
            return;
          }
          // Poll /health until the fresh daemon reports ready, then reload.
          var tries = 0;
          var poll = setInterval(function () {
            tries += 1;
            fetch('/health', { cache: 'no-store' })
              .then(function (r) { return r.ok ? r.json() : null; })
              .then(function (h) {
                if (h && (h.ready === true || h.engine === 'initialized')) {
                  clearInterval(poll);
                  if (summary) summary.textContent = 'Daemon back online — reloading…';
                  setTimeout(function () { window.location.reload(); }, 600);
                }
              })
              .catch(function () { /* daemon still down mid-restart — keep polling */ });
            if (tries > 30) {  // ~30s ceiling
              clearInterval(poll);
              if (summary) summary.textContent =
                'Daemon is taking longer than expected. Refresh the page in a moment.';
              btn.disabled = false; btn.textContent = label;
            }
          }, 1000);
        })
        .catch(function () {
          // The connection often drops as the daemon stops — that's expected;
          // start polling for it to come back.
          if (summary) summary.textContent = 'Daemon restarting — waiting for it to come back…';
          var tries = 0;
          var poll = setInterval(function () {
            tries += 1;
            fetch('/health', { cache: 'no-store' })
              .then(function (r) { return r.ok ? r.json() : null; })
              .then(function (h) {
                if (h && (h.ready === true || h.engine === 'initialized')) {
                  clearInterval(poll);
                  window.location.reload();
                }
              })
              .catch(function () {});
            if (tries > 30) { clearInterval(poll); btn.disabled = false; btn.textContent = label; }
          }, 1000);
        });
    });
  }

  function wireRestartBtn() {
    var btn = document.getElementById('od-restart-daemon-btn');
    var summary = document.getElementById('od-restart-summary');
    if (!btn) return;
    btn.addEventListener('click', function () { restartDaemon(btn, summary); });
  }

  // Shared with the images-and-documents card (od-features.js).
  window.odRestartDaemon = restartDaemon;

  // ─── Trust ────────────────────────────────────────────────────────────────
  /**
   * Populate trust enforcement section from /api/trust/stats.
   * Toggle controls are read-only display — no write endpoint exists yet.
   * TODO: wire toggles when /api/trust/settings PATCH is exposed.
   */
  function populateTrust(trustStats) {
    var ctlEl = document.getElementById('od-trust-ctl');
    if (!ctlEl) return;

    if (!trustStats) {
      ctlEl.innerHTML = '<p class="muted">Trust data unavailable.</p>';
      return;
    }

    var enforcement = String(trustStats.enforcement || 'unknown');
    var isEnforced  = enforcement.toLowerCase() !== 'silent collection' &&
                      enforcement.toLowerCase() !== 'off' &&
                      enforcement.toLowerCase() !== 'disabled';

    // Helper: a control row using .od-ops-ctl class (injectCtlStyle injects .ctl rules).
    // :last-child removes border-bottom on the final row — matches design's <style> block.
    function ctlRow(label, sublabel, control) {
      return (
        '<div class="od-ops-ctl">' +
          '<div>' +
            '<b>' + esc(label) + '</b>' +
            '<div class="muted-info" style="font-size:12.5px">' + esc(sublabel) + '</div>' +
          '</div>' +
          control +
        '</div>'
      );
    }

    // Switch is display-only (no write endpoint) — disabled + lower opacity.
    // enforcement: if mode is "silent collection" / off / disabled → switch is OFF.
    var enfSwitch = '<button class="switch' + (isEnforced ? ' on' : '') +
      '" disabled aria-label="read-only" style="cursor:not-allowed;opacity:0.7"></button>';

    // Design shows only 4 ctl rows — no stats summary block above them.
    ctlEl.innerHTML = (
      ctlRow(
        'Enforcement mode',
        'Block writes from agents below the trust floor',
        enfSwitch
      ) +
      // TODO: trust floor — no read or write endpoint in current API
      ctlRow(
        'Trust floor',
        'Minimum trust score to write',
        '<span class="mono">0.30</span>' // TODO: read from /api/trust/config when available
      ) +
      // TODO: quick-delete penalty — no read/write endpoint yet
      ctlRow(
        'Quick-delete penalty',
        'Lower trust when writes are deleted < 60s',
        '<button class="switch on" disabled style="cursor:not-allowed;opacity:0.7"></button>'
      ) +
      // TODO: recall reward — no read/write endpoint yet
      ctlRow(
        'Recall reward',
        'Raise trust when a memory is recalled',
        '<button class="switch on" disabled style="cursor:not-allowed;opacity:0.7"></button>'
      )
    );
  }

  // ─── Compliance ───────────────────────────────────────────────────────────
  /**
   * Populate the compliance KPI strip — 3 cards with icons.
   * Icons via window.slmIcon() so they work in dynamically-injected content.
   */
  var SLMOperationsCompliance = window.SLMOperationsCompliance(moduleContext);
  Object.assign(moduleContext, SLMOperationsCompliance);
  var { populateComplianceKpi, populateRetentionPolicies, normalizeAuditEvent, populateAuditTrail, showAuditChainStatus, fetchAndRenderAudit, renderAuditRows, wireAuditFilter, wireRetentionDeleteBtns, wireNewPolicyBtn, wireGdprBtns, openRetentionPolicyModal } = SLMOperationsCompliance;
  // ─── Main entry point ─────────────────────────────────────────────────────
  /**
   * Fetch all operations endpoints, inject the OD skeleton, and populate with real data.
   * Idempotent: each call clears and re-renders the container.
   *
   * @param {HTMLElement} container  Usually document.getElementById("operations-pane").
   */
  function odRenderOperations(container) {
    if (!container) return;
    var selected = container.querySelector('[data-od-tab].active');
    var selectedTab = selected && selected.dataset.odTab;

    container.innerHTML =
      '<div style="color:var(--fg-2);text-align:center;padding:48px 0">' +
      'Loading operations data…</div>';

    // Reset audit row cache on each render
    SLMOperationsCompliance.resetAudit();

    Promise.all([
      fetch('/api/lifecycle/status').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
      fetch('/api/trust/stats').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
      fetch('/api/compliance/status').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
      // Task C: wire the full audit trail endpoint (up to 100 events, chain_verified)
      fetch('/api/compliance/audit?limit=100').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
    ]).then(function (results) {
      var lc         = results[0];
      var trustStats = results[1];
      var comp       = results[2];
      var auditFull  = results[3]; // {available, events, chain_verified, ...}

      // Inject .ctl class styles (used by Trust section control rows)
      injectCtlStyle();

      // Inject static skeleton — no API data inside
      container.innerHTML = buildSkeleton();

      // Wire tab switching (CSP-safe)
      initTabs(container);
      if (selectedTab) {
        var selectedButton = container.querySelector('[data-od-tab="' + selectedTab + '"]');
        if (selectedButton) { selectedButton.click(); selectedButton.focus(); }
      }

      // Populate lifecycle
      populateLifecycle(lc);
      // CRIT(b): compact buttons wired here — never auto-fired on render
      wireCompactBtns();
      // Restart-daemon button — wired via addEventListener, never auto-fired
      wireRestartBtn();

      // Populate trust
      populateTrust(trustStats);

      // Populate compliance
      populateComplianceKpi(comp);
      populateRetentionPolicies(comp);
      // Task C: prefer the full audit trail from the dedicated endpoint;
      // fall back to the status endpoint's recent_audit_events on failure.
      populateAuditTrail(comp, auditFull);

      // Task C: show chain-integrity indicator
      showAuditChainStatus(auditFull);

      // Wire audit filter and new-policy button (listeners on injected DOM)
      wireAuditFilter(auditFull);
      wireNewPolicyBtn();
      wireGdprBtns();

      // Team & access (RBAC / C3) — rendered by od-team.js
      if (typeof window.odRenderTeam === 'function') {
        window.odRenderTeam(document.getElementById('od-team-mount'));
      }

      // Ingestion (Gmail / Calendar / Transcript) — re-linked to ng-ingestion.js.
      // #ingestion-overview and #ingestion-adapters are now in the skeleton DOM
      // (inside the Ingestion tab section); calling loadIngestionStatus() renders them.
      // The function is null-guarded: no-op if ng-ingestion.js failed to load.
      if (typeof window.loadIngestionStatus === 'function') {
        window.loadIngestionStatus();
      }

    }).catch(function (err) {
      container.innerHTML =
        '<div style="color:var(--danger);text-align:center;padding:48px 0">' +
        'Failed to load operations data. Check the daemon is running on port 8765.</div>';
      if (typeof console !== 'undefined') {
        console.error('[od-ops] render error:', err);
      }
    });
  }

  // ─── Public API ───────────────────────────────────────────────────────────

  window.odRenderOperations = odRenderOperations;

  // Auto-run: if the operations pane is already in the DOM at script load, render it.
  document.addEventListener('DOMContentLoaded', function () {
    var pane = document.getElementById('operations-pane');
    if (pane) odRenderOperations(pane);
  });

}());
