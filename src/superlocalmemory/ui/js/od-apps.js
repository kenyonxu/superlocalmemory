// Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar — AGPL-3.0
// od-apps.js — the "Connected apps" pane (sidebar: Integrations -> Connected apps).
//
// Exposes: window.odRenderApps(pane, options)
// Called by the od() shell. The pane owns live sign-in state, so the shell does
// NOT clear it on re-entry (same contract odRenderMcp had for the old card):
// the mounted root, the form, focus and in-flight sign-in survive navigation,
// and a profile switch refreshes the data through odRefreshConnectionStatus.
//
// Composition: header + trust points here; "This computer", "Your connected
// apps", "Add an app" and "Technical details" come from od-connections.js
// (window.odCreateAiConnectionsCard) and od-apps-list.js.
// Requires od-apps-ui.js. No innerHTML, no inline handlers, no external assets.
(function () {
  'use strict';

  var TRUST = [
    { icon: 'monitor', title: 'Your memory stays on this computer', text: 'Apps ask this computer for what they need. Your database is never uploaded.' },
    { icon: 'approve', title: 'You approve every app', text: 'Nothing can connect until you set it up and sign in here.' },
    { icon: 'eye', title: 'Read-only unless you allow saving', text: 'Internet apps can only look things up until you allow saving. Local apps use the tools you choose below.' }
  ];

  function buildHeader(ui) {
    var h = ui.h;
    var titleRow = h('div', { className: 'apps-title-row' }, [
      h('h2', { className: 'apps-title', id: 'apps-page-title', text: 'Connected apps' }),
      h('span', { className: 'badge violet', text: 'Free daily allowance' })
    ]);
    var trust = h('ul', { className: 'apps-trust', 'aria-label': 'How your memory is protected' });
    TRUST.forEach(function (item) {
      trust.appendChild(h('li', { className: 'apps-trust-item' }, [
        h('span', { className: 'apps-trust-icon', 'aria-hidden': 'true' }, [ui.icon(item.icon, 20)]),
        h('div', { className: 'apps-trust-text' }, [h('strong', { text: item.title }), h('span', { text: item.text })])
      ]));
    });
    return h('header', { className: 'apps-hero' }, [
      titleRow,
      h('p', { className: 'apps-lead', text: 'See the AI apps connected on this computer and choose their memory tools. You can also connect internet apps — like ChatGPT, Claude on the web, Composio or Muse — below.' }),
      trust,
      h('p', { className: 'apps-note', text: 'Results an app reads are shared with that app. This computer must be on and online for apps to reach your memory.' })
    ]);
  }

  window.odRenderApps = function (pane, options) {
    if (!pane) return Promise.resolve();
    options = options || {};
    var ui = window.odAppsUi;
    if (!ui) return Promise.resolve();
    var root = pane.querySelector('#od-apps-root');
    var connections = pane.querySelector('#od-ai-connections');
    if (!root) {
      root = ui.h('div', { id: 'od-apps-root', className: 'apps-root' });
      root.appendChild(buildHeader(ui));
      if (typeof window.odCreateLocalAppsCard === 'function') root.appendChild(window.odCreateLocalAppsCard());
      if (!connections && typeof window.odCreateAiConnectionsCard === 'function') connections = window.odCreateAiConnectionsCard();
      if (connections) root.appendChild(connections);
      pane.textContent = ''; pane.appendChild(root);
    } else if (!options.preserveConnectionScope && connections && typeof connections.odRefreshConnectionStatus === 'function') {
      connections.odRefreshConnectionStatus();
    }
    var localApps = root.querySelector('[data-local-apps]');
    if (localApps && typeof localApps.odRefreshLocalApps === 'function') localApps.odRefreshLocalApps();
    return Promise.resolve();
  };
}());
