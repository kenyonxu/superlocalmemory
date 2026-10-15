// Extracted od-operations.js feature scope; initialized once with the pane helpers.
(function () {
  'use strict';
  var ctx;
  var esc, timeAgo, fmtNum, safeIcon, injectCtlStyle;
  window.SLMOperationsLayout = function (helpers) {
    ctx = helpers;
    ({ esc, timeAgo, fmtNum, safeIcon, injectCtlStyle } = ctx);
    return { buildSkeleton:buildSkeleton };
  };
  function buildSkeleton() {
    return (
      // Page header
      '<div class="page-head">' +
        '<h2>Governance</h2>' +
        '<p>Who can access this workspace, how memory ages, trust, and ' +
        'compliance — with the controls to act on them. Nothing here is ' +
        'read-only.</p>' +
      '</div>' +

      // Tab bar
      '<div class="tabs" id="od-ops-tabs">' +
        '<button class="tab active" data-od-tab="lifecycle">Lifecycle</button>' +
        '<button class="tab" data-od-tab="access">Access &amp; Users</button>' +
        '<button class="tab" data-od-tab="trust">Trust</button>' +
        '<button class="tab" data-od-tab="compliance">Compliance</button>' +
        // Ingestion tab — restored: re-mounts #ingestion-overview + #ingestion-adapters
        // so ng-ingestion.js (loaded in index.html) can populate them on call.
        '<button class="tab" data-od-tab="ingestion">Ingestion</button>' +
      '</div>' +

      // ══════ LIFECYCLE TAB ══════════════════════════════════════════════
      '<section class="tabpane active" data-od-pane="lifecycle">' +

        // Every "dim" in this file was .dim (--fg-3, 4.09:1 here — under
        // WCAG AA 4.5:1): loading states, error messages, reasons,
        // timestamps and sublabels are all informational — the user is
        // meant to read them, not just sense a loading flicker — so this
        // file uses .muted-info (--fg-2) throughout instead.
        // State KPI strip: 5 lifecycle buckets
        '<div class="kpi-strip" ' +
          'style="margin-bottom:16px;grid-template-columns:repeat(5,1fr)" ' +
          'id="od-lc-states">' +
          '<div class="muted-info" style="padding:12px">Loading…</div>' +
        '</div>' +

        // Two-column: age table + compaction preview
        '<div class="grid" style="grid-template-columns:1fr 1fr;align-items:start">' +

          '<div class="card">' +
            '<div class="card-head"><h3>Average memory age by state</h3></div>' +
            '<div class="card-pad">' +
              '<table class="tbl" id="od-lc-ages">' +
                '<thead><tr>' +
                  '<th>State</th>' +
                  '<th>Avg age (d)</th>' +
                  '<th>Newest (d)</th>' +
                  '<th>Oldest (d)</th>' +
                '</tr></thead>' +
                '<tbody>' +
                  '<tr><td colspan="4" class="muted-info" style="text-align:center;padding:20px">Loading…</td></tr>' +
                '</tbody>' +
              '</table>' +
            '</div>' +
          '</div>' +

          '<div class="card">' +
            '<div class="card-head">' +
              '<h3>Compaction preview</h3>' +
              '<span class="sub">dry-run · no changes applied</span>' +
            '</div>' +
            '<div class="card-pad">' +
              '<p class="muted" style="font-size:13px" id="od-compact-summary">' +
                'Click "Re-run dry-run" to preview which memories would be transitioned.' +
              '</p>' +
              '<div id="od-compact-detail" style="margin:14px 0"></div>' +
              '<div style="display:flex;gap:8px">' +
                // CRIT(b): wired via addEventListener in wireCompactBtns(), never auto-fired
                '<button class="btn sm" id="od-compact-preview-btn">Re-run dry-run</button>' +
                '<button class="btn sm primary" id="od-compact-apply-btn">Apply compaction</button>' +
              '</div>' +
            '</div>' +
          '</div>' +

        '</div>' + // end two-column grid

        // Recent transitions table
        '<div class="card" style="margin-top:16px">' +
          '<div class="card-head"><h3>Recent transitions</h3></div>' +
          '<div class="card-pad">' +
            '<table class="tbl" id="od-lc-trans">' +
              '<thead><tr>' +
                '<th>Memory</th><th>Transition</th><th>Reason</th><th>When</th>' +
              '</tr></thead>' +
              '<tbody>' +
                '<tr><td colspan="4" class="muted-info" style="text-align:center;padding:20px">Loading…</td></tr>' +
              '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>' +

        // System control — restart the daemon without a terminal
        '<div class="card" style="margin-top:16px;max-width:640px">' +
          '<div class="card-head"><h3>System control</h3></div>' +
          '<div class="card-pad">' +
            '<p class="muted" style="font-size:13px" id="od-restart-summary">' +
              'Restart the memory daemon — applies pending config changes and ' +
              'clears transient state. Memories are never affected.</p>' +
            '<button class="btn sm" id="od-restart-daemon-btn" style="margin-top:10px">' +
              'Restart daemon</button>' +
          '</div>' +
        '</div>' +

      '</section>' +

      // ══════ TRUST TAB ═════════════════════════════════════════════════
      '<section class="tabpane" data-od-pane="trust">' +

        '<div class="card" style="max-width:640px">' +
          '<div class="card-head"><h3>Trust enforcement</h3></div>' +
          '<div class="card-pad" id="od-trust-ctl">' +
            '<div class="muted-info" style="padding:20px;text-align:center">Loading trust data…</div>' +
          '</div>' +
        '</div>' +

      '</section>' +

      // ══════ ACCESS & USERS TAB ════════════════════════════════════════
      // First-class governance surface for non-technical admins: who can use
      // this workspace and at what role. The card is populated by od-team.js
      // (window.odRenderTeam) into #od-team-mount during odRenderOperations().
      '<section class="tabpane" data-od-pane="access">' +
        '<div class="card" id="od-team-mount">' +
          '<div class="card-head"><h3>Team &amp; access</h3></div>' +
          '<div class="card-pad"><p class="muted">Loading…</p></div>' +
        '</div>' +
      '</section>' +

      // ══════ COMPLIANCE TAB ════════════════════════════════════════════
      '<section class="tabpane" data-od-pane="compliance">' +

        // KPI strip: 3 cards with icons
        '<div class="kpi-strip" ' +
          'style="margin-bottom:16px;grid-template-columns:repeat(3,1fr)" ' +
          'id="od-comp-kpi">' +
          '<div class="muted-info" style="padding:12px">Loading…</div>' +
        '</div>' +

        // Retention policies table with New policy button
        '<div class="card">' +
          '<div class="card-head">' +
            '<h3>Active retention policies</h3>' +
            '<div class="spacer"></div>' +
            // New policy button: opens the retention-policy form
            '<button class="btn sm" id="od-new-policy-btn">' +
              safeIcon('plus') + ' New policy' +
            '</button>' +
          '</div>' +
          '<div class="card-pad">' +
            '<table class="tbl" id="od-ret">' +
              '<thead><tr>' +
                '<th>Policy</th>' +
                '<th>Retention (days)</th>' +
                '<th>Category</th>' +
                '<th>Action</th>' +
                '<th></th>' +
              '</tr></thead>' +
              '<tbody>' +
                '<tr><td colspan="5" class="muted-info" style="text-align:center;padding:20px">Loading…</td></tr>' +
              '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>' +

        // Data privacy & GDPR — export, run retention, erase (dashboard-operable)
        '<div class="card" style="margin-top:16px">' +
          '<div class="card-head"><h3>Data privacy &amp; GDPR</h3></div>' +
          '<div class="card-pad">' +
            '<p class="muted" style="font-size:13px" id="od-gdpr-summary">' +
              'Manage this profile\'s data under GDPR — export a full copy, run ' +
              'retention now, or erase everything for this profile.</p>' +
            '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">' +
              '<button class="btn sm" id="od-gdpr-export-btn">' + safeIcon('download') + ' Export my data</button>' +
              '<button class="btn sm" id="od-retention-run-btn">Run retention now</button>' +
              '<button class="btn sm" id="od-gdpr-erase-btn" style="border-color:#ff6b6b;color:#ff6b6b">Erase this profile…</button>' +
            '</div>' +
          '</div>' +
        '</div>' +

        // Audit trail with All/Denied segment filter + event-type API filter
        '<div class="card" style="margin-top:16px">' +
          '<div class="card-head">' +
            '<h3>Audit trail</h3>' +
            // Task C: chain-integrity badge
            '<span id="od-audit-chain-status"></span>' +
            '<div class="spacer"></div>' +
            // Task C: event-type filter select (calls API)
            '<select id="od-audit-evt-filter" style="padding:4px 8px;border-radius:6px;' +
              'border:1px solid var(--border);background:var(--card);color:var(--fg);font-size:12px">' +
              '<option value="">All types</option>' +
              '<option value="recall">recall</option>' +
              '<option value="store">remember (store)</option>' +
              '<option value="delete">delete</option>' +
              '<option value="lifecycle_transition">lifecycle</option>' +
              '<option value="access_denied">access_denied</option>' +
              '<option value="retention_enforced">retention</option>' +
            '</select>' +
            '<div class="seg" id="od-audit-filter">' +
              '<button class="active" data-filter="all">All</button>' +
              '<button data-filter="denied">Denied</button>' +
            '</div>' +
          '</div>' +
          '<div class="card-pad">' +
            '<table class="tbl" id="od-audit">' +
              '<thead><tr>' +
                '<th>Timestamp</th>' +
                '<th>Event</th>' +
                '<th>Actor</th>' +
                '<th>Action</th>' +
                '<th>Target</th>' +
                '<th>Result</th><th>Profile</th>' +
              '</tr></thead>' +
              '<tbody id="od-audit-body">' +
                '<tr><td colspan="7" class="muted-info" style="text-align:center;padding:20px">Loading…</td></tr>' +
              '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>' +

      '</section>' +

      // ══════ INGESTION TAB ═════════════════════════════════════════════
      // Restoration: mounts #ingestion-overview and #ingestion-adapters so that
      // window.loadIngestionStatus() (ng-ingestion.js) can populate them.
      // ng-ingestion.js is loaded in index.html and defines loadIngestionStatus().
      // Adapter action buttons use data-act-click="adapter-action" which is handled
      // by event-delegation.js → adapterAction() defined in ng-ingestion.js.
      // Backend: GET /api/adapters · POST /api/adapters/{enable|disable|start|stop}
      '<section class="tabpane" data-od-pane="ingestion">' +

        '<div class="page-head" style="margin-bottom:16px">' +
          '<h2 style="font-size:20px;margin-bottom:6px">Connectors &amp; Ingestion</h2>' +
          '<p style="font-size:13.5px">' +
            'Gmail, Calendar, and Transcript adapters — enable to start building memory ' +
            'from external sources. All processing is local.' +
          '</p>' +
        '</div>' +

        // #ingestion-overview — ng-ingestion.js: 4 stat cards (Available/Enabled/Running/Privacy)
        '<div id="ingestion-overview"></div>' +

        // #ingestion-adapters — ng-ingestion.js: adapter cards (Gmail/Calendar/Transcript)
        '<div id="ingestion-adapters" style="margin-top:16px"></div>' +

      '</section>'
    );
  }

  // ─── Tab switching ────────────────────────────────────────────────────────
  /**
   * Wire the internal tab bar.
   * Uses data-od-tab on buttons and data-od-pane on sections.
   * No onclick attributes — CSP-safe addEventListener only.
   */

}());
