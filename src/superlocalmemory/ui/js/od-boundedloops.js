// Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
// Licensed under AGPL-3.0-or-later - see LICENSE file
// Part of SuperLocalMemory | https://qualixar.com
//
// Bounded Loops pane.
//
// Moved out of Governance in 4.0.8. Every other Governance tab governs SLM's
// OWN data — lifecycle, access, trust, compliance, ingestion. Bounded Loops
// governs none of it: it is a SEPARATE product that SLM optionally observes
// over the published contract bounded-loops.dev/slm-bridge/v1. Neither product
// depends on the other and installing either alone is complete, which makes
// this an integration, not a governance function.
//
// Reads GET /api/v3/bounded-loops/evidence for live bridge status and observed
// terminal runs. The guarantees are rendered FROM THE DATA rather than asserted
// in prose: every document carries eligible_for_learning=false, so the pane can
// show that SLM is not permitted to learn from these runs instead of merely
// promising that it does not.

(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function scaffold() {
    return (

      // ══════ BOUNDED LOOPS TAB ════════════════════════════════════════
      // Static info card — no backend calls.
      // A bounded loop advances only when an INDEPENDENT gate passes, not
      // when the agent claims success. Laps are persisted as SLM memory
      // (tag loop:<name>) so the full run history is queryable.
      '<div>' +

        // Live bridge status first: whether Bounded Loops is installed, the
        // negotiated contract, and which runs were observed. The explanation
        // below it is the same for everyone; this part is about YOUR install.
        '<div class="card" style="margin-bottom:16px">' +
          '<div class="card-head"><h3>Bridge status</h3>' +
            '<span class="sub">observation only \u2014 never learns from these runs</span>' +
          '</div>' +
          '<div class="card-pad" id="od-bl-evidence">' +
            '<div style="font-size:13px;color:var(--fg-2)">Checking bridge\u2026</div>' +
          '</div>' +
        '</div>' +

        '<div class="page-head" style="margin-bottom:16px">' +
          '<h2 style="font-size:20px;margin-bottom:6px">Bounded Loops</h2>' +
          '<p style="font-size:13.5px">An iteration control pattern for agentic ' +
            'frameworks &mdash; the loop advances only when an <strong>independent ' +
            'gate</strong> passes, not when the agent claims success. Prevents ' +
            'rationalisation: the model cannot self-certify completion.</p>' +
        '</div>' +

        // Two-column: concept + CLI reference
        '<div class="grid" style="grid-template-columns:1fr 1fr;align-items:start;margin-bottom:16px">' +

          '<div class="card">' +
            '<div class="card-head"><h3>How it works</h3></div>' +
            '<div class="card-pad">' +
              '<p style="font-size:13px;line-height:1.6;margin-bottom:12px">' +
                'Standard agentic loops let the model declare itself done &mdash; ' +
                'a known failure mode when the model rationalises instead of verifying. ' +
                'A bounded loop separates <em>execution</em> (the agent) from ' +
                '<em>verification</em> (an independent gate such as a test suite, ' +
                'linter, or judge LLM).' +
              '</p>' +
              '<p style="font-size:13px;line-height:1.6;margin-bottom:12px">' +
                'The loop terminates only when the gate returns <code>DONE</code>, ' +
                'or when a hard lap cap is reached (<code>HALT</code>). Each lap is ' +
                'persisted as a queryable SLM memory tagged ' +
                '<code>loop:&lt;name&gt;</code>.' +
              '</p>' +
              '<div style="background:var(--card-2);border-radius:var(--r-md);' +
                'padding:10px 14px;font-size:12.5px;line-height:1.7">' +
                '<div><span class="badge ok" style="margin-right:8px">DONE</span>' +
                  'Gate passed &mdash; loop succeeded cleanly</div>' +
                '<div style="margin-top:6px"><span class="badge warn" style="margin-right:8px">HALT</span>' +
                  'Lap cap reached &mdash; hard stop applied</div>' +
                '<div style="margin-top:6px"><span class="badge cyan" style="margin-right:8px">PAUSE</span>' +
                  'Awaiting external input or approval</div>' +
                '<div style="margin-top:6px"><span class="badge danger" style="margin-right:8px">KILLED</span>' +
                  'Manually stopped by the operator</div>' +
                '<div style="margin-top:6px"><span class="badge neutral" style="margin-right:8px">ERROR</span>' +
                  'Unrecoverable failure during a lap</div>' +
              '</div>' +
            '</div>' +
          '</div>' +

          '<div class="card">' +
            '<div class="card-head"><h3>Run it: CLI &middot; command &middot; MCP</h3></div>' +
            '<div class="card-pad">' +
              '<p style="font-size:13px;margin-bottom:14px">' +
                'Bounded loops ship on three surfaces &mdash; the same engine and ' +
                'the same queryable ledger behind each.' +
              '</p>' +

              '<div style="font-size:12px;font-weight:600;color:var(--fg-2);margin-bottom:6px">CLI</div>' +
              '<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px">' +
                '<div class="list-row">' +
                  '<span class="mono" style="min-width:190px;font-size:13px">slm loop demo</span>' +
                  '<span style="font-size:12.5px;color:var(--fg-2)">Run a live demo bounded loop</span>' +
                '</div>' +
                '<div class="list-row">' +
                  '<span class="mono" style="min-width:190px;font-size:13px">slm loop history</span>' +
                  '<span style="font-size:12.5px;color:var(--fg-2)">List loop runs for this profile</span>' +
                '</div>' +
                '<div class="list-row">' +
                  '<span class="mono" style="min-width:190px;font-size:13px">slm loop show &lt;run_id&gt;</span>' +
                  '<span style="font-size:12.5px;color:var(--fg-2)">Inspect a run lap-by-lap</span>' +
                '</div>' +
              '</div>' +

              '<div style="font-size:12px;font-weight:600;color:var(--fg-2);margin-bottom:6px">Command (Claude Code / Codex)</div>' +
              '<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px">' +
                '<div class="list-row">' +
                  '<span class="mono" style="min-width:190px;font-size:13px">/slm-loop</span>' +
                  '<span style="font-size:12.5px;color:var(--fg-2)">Slash command bound to the slm-loop skill + runner agent</span>' +
                '</div>' +
              '</div>' +

              '<div style="font-size:12px;font-weight:600;color:var(--fg-2);margin-bottom:6px">MCP tools (code / full / power profiles)</div>' +
              '<div style="display:flex;flex-direction:column;gap:8px">' +
                '<div class="list-row">' +
                  '<span class="mono" style="min-width:190px;font-size:13px">slm_loop_run</span>' +
                  '<span style="font-size:12.5px;color:var(--fg-2)">Run a gated loop &mdash; the gate is an independent SLM recall</span>' +
                '</div>' +
                '<div class="list-row">' +
                  '<span class="mono" style="min-width:190px;font-size:13px">slm_loop_history</span>' +
                  '<span style="font-size:12.5px;color:var(--fg-2)">List runs (read-only)</span>' +
                '</div>' +
                '<div class="list-row">' +
                  '<span class="mono" style="min-width:190px;font-size:13px">slm_loop_show</span>' +
                  '<span style="font-size:12.5px;color:var(--fg-2)">Show a run lap-by-lap (read-only)</span>' +
                '</div>' +
              '</div>' +
              '<div style="margin-top:18px;padding:10px 14px;background:var(--card-2);' +
                'border-radius:var(--r-md);font-size:12.5px;line-height:1.6">' +
                '<b>Memory tagging:</b> each lap is stored with tag ' +
                '<code>loop:&lt;name&gt;</code>. Recall the full history with ' +
                '<br><code>slm recall --tag loop:my-loop-name</code>' +
              '</div>' +
            '</div>' +
          '</div>' +

        '</div>' + // end two-column grid

        // Framework adapters note
        '<div class="card">' +
          '<div class="card-head"><h3>Framework adapters</h3></div>' +
          '<div class="card-pad">' +
            '<p style="font-size:13px;line-height:1.6;margin-bottom:14px">' +
              'Bounded loops integrate with any agentic framework that supports ' +
              'tool-call round-trips. The gate is an ordinary SLM memory check &mdash; ' +
              'no special framework wiring required. Each framework stamps ' +
              '<code>SLM_AGENT_ID</code> so lap history is attribution-aware.' +
            '</p>' +
            '<div style="display:flex;flex-wrap:wrap;gap:8px">' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">CrewAI</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">LangChain</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">LangGraph</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">Semantic Kernel</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">LlamaIndex</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">Microsoft Agent Framework</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">AutoGen</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">Google ADK</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">OpenAI Agents</span>' +
              '<span style="padding:4px 12px;border-radius:99px;font-size:12px;' +
                'background:var(--card-2);color:var(--fg-2)">Any MCP-compatible agent</span>' +
            '</div>' +
            '<p style="margin-top:12px;font-size:12.5px;color:var(--fg-2)">' +
              'Learn the full pattern: run <code>/slm-loop</code> (the slm-loop skill) ' +
              'inside Claude Code for an interactive walkthrough with live examples.' +
            '</p>' +
          '</div>' +
        '</div>' +

      '</div>'
    );
  }

  /* Bounded Loops bridge status + observed runs.
   *
   * Until 4.0.8 this tab was a static essay about loop gating. It said nothing
   * about the bridge, which is the part that concerns SLM: Bounded Loops is a
   * SEPARATE product, and what SLM does with it is import read-only evidence
   * over the published contract bounded-loops.dev/slm-bridge/v1.
   *
   * The guarantees are the point, and they must be stated from the DATA rather
   * than asserted in prose — `eligible_for_learning` is a hard field in every
   * document, always false in v1, so the pane can show that SLM is not allowed
   * to learn from these runs rather than merely promising it doesn't.
   */
  var _blLoaded = false;
  function loadEvidence() {
    var box = document.getElementById('od-bl-evidence');
    if (!box || _blLoaded) return;
    _blLoaded = true;

    fetch('/internal/token', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (t) {
        var tok = t && (t.token || t.install_token);
        return fetch('/api/v3/bounded-loops/evidence', {
          credentials: 'same-origin',
          headers: tok ? { 'X-Install-Token': tok } : {},
        });
      })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { box.innerHTML = _blNote('Bridge status unavailable.'); return; }
        box.innerHTML = renderBoundedLoopsEvidence(d);
      })
      .catch(function () {
        _blLoaded = false;   // allow a retry on the next tab visit
        box.innerHTML = _blNote('Could not read bridge status.');
      });
  }

  function _blNote(msg) {
    return '<div style="font-size:13px;color:var(--fg-2)">' + esc(msg) + '</div>';
  }

  function renderBoundedLoopsEvidence(d) {
    var installed = d.installed;
    var runs = d.runs || [];
    var badge = installed === true
      ? '<span class="badge ok">installed</span>'
      : (installed === false
          ? '<span class="badge neutral">not installed</span>'
          : '<span class="badge warn">unknown</span>');

    var head =
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">' +
        badge +
        '<span class="mono" style="font-size:12px;color:var(--fg-2)">' +
          esc(d.contract || '') + '</span>' +
      '</div>';

    // Optional by design — "not installed" is a normal, complete state.
    if (installed === false) {
      return head + '<p style="font-size:13px;line-height:1.6;color:var(--fg-2)">' +
        'Bounded Loops is a separate product and SuperLocalMemory does not ' +
        'require it. Install it and SLM can import read-only evidence about ' +
        'finished runs — it never sends anything back.</p>';
    }

    var guarantees =
      '<div style="background:var(--card-2);border-radius:var(--r-md);' +
        'padding:12px 14px;font-size:12.5px;line-height:1.75;margin-bottom:12px">' +
        '<div><strong>Observation only.</strong> Evidence records what a run did. ' +
          'It never authorises SLM to learn from it, re-rank memories, or change ' +
          'routing — every document carries <code>eligible_for_learning: false</code>.</div>' +
        '<div style="margin-top:6px"><strong>No paths leave the workspace.</strong> ' +
          'Locations travel as digests, so a project directory name never reaches ' +
          'the memory store. Gate text and artifact contents are excluded at source.</div>' +
        '<div style="margin-top:6px"><strong>Tamper-evident, not verified.</strong> ' +
          'Receipts form a local append-only hash chain (<code>local_hash_chain_only</code>). ' +
          'That makes edits detectable — it is not authentication or independent audit.</div>' +
      '</div>';

    if (!runs.length) {
      return head + guarantees +
        '<p style="font-size:13px;color:var(--fg-2)">' +
          'No runs observed yet. Import one with the <code>observe_bounded_loop_evidence</code> ' +
          'tool — nothing is imported automatically.</p>';
    }

    var demoNote = d.demonstration_count
      ? '<div style="font-size:12px;color:var(--fg-3);margin-bottom:8px">' +
          esc(String(d.demonstration_count)) + ' of ' + esc(String(d.total)) +
          ' observed run' + (d.total === 1 ? '' : 's') + ' ' +
          (d.demonstration_count === 1 ? 'is a demonstration' : 'are demonstrations') +
          ' — wiring proofs, not real work.</div>'
      : '';

    var rows = runs.map(function (r) {
      var cls = r.outcome === 'SUCCEEDED' ? 'ok'
              : (r.outcome === 'CANCELLED' ? 'neutral' : 'danger');
      // run_state, not just outcome: HALTED (budget/policy stop) and FAILED
      // (gate rejected the work) are different events and both map to FAILED.
      var state = r.run_state && r.run_state !== r.outcome
        ? ' <span style="color:var(--fg-3)">(' + esc(r.run_state) + ')</span>' : '';
      return '<div class="list-row" style="align-items:baseline">' +
        '<span class="mono" style="flex:1;font-size:12.5px">' + esc(r.run_ref || r.run_id) + '</span>' +
        (r.demonstration
          ? '<span class="badge neutral" style="margin-right:8px">demo</span>' : '') +
        '<span class="badge ' + cls + '" style="margin-right:8px">' + esc(r.outcome) + '</span>' +
        '<span style="font-size:12px;color:var(--fg-2)">' + state + '</span>' +
        '<span style="font-size:12px;color:var(--fg-3);margin-left:10px">seq ' +
          esc(String(r.receipt_sequence)) + '</span>' +
      '</div>';
    }).join('');

    return head + guarantees + demoNote + rows;
  }

  window.odRenderLoops = function (container) {
    if (!container) return;
    container.innerHTML = scaffold();
    _blLoaded = false;
    loadEvidence();
  };
})();
