// Live stdio connections and local-host setup. Config writes never count as connected.
(function () {
  'use strict';
  var API = '/api/v3/local-apps';

  function call(path, init) {
    return fetch(path, init).then(function (response) {
      if (!response.ok) throw new Error('Request could not be confirmed');
      return response.json();
    });
  }

  function fillSelect(select, rows, label) {
    var old = select.value;
    select.textContent = '';
    rows.forEach(function (row) {
      var option = document.createElement('option');
      option.value = row.id;
      option.textContent = row.name + (row.description ? ' — ' + row.description : '')
        + (row.configured ? ' — set up' : '');
      select.appendChild(option);
    });
    if (rows.some(function (row) { return row.id === old; })) select.value = old;
    else {
      // Nothing chosen yet: suggest an app that still needs setting up.
      var fresh = rows.filter(function (row) { return !row.configured; })[0];
      if (fresh) select.value = fresh.id;
    }
    select.setAttribute('aria-label', label);
  }

  function profileName(data, id) {
    var match = (data.profiles || []).filter(function (p) { return p.id === id; })[0];
    return match ? match.name : '';
  }

  function appRow(state, app, data) {
    var h = state.h;
    var row = h('li', { className: 'apps-row apps-local-row' });
    row.appendChild(h('strong', { text: app.name }));
    row.appendChild(h('span', { className: 'badge neutral', text: 'MCP · ' + app.code_version }));
    row.appendChild(h('span', { text: app.version_reported === false ? 'Tools unreported' : (app.tools || []).length + ' tools' }));
    var tools = app.tools_profile ? profileName(data || {}, app.tools_profile) : '';
    if (tools) row.appendChild(h('span', { className: 'badge neutral', text: tools }));
    if (app.stale_version) row.appendChild(h('span', { className: 'badge warn', text: 'Stale version' }));
    if (app.version_reported === false) row.appendChild(h('span', { className: 'badge warn', text: 'Version unreported' }));
    if (app.connection_evidence === 'live-process') row.appendChild(h('span', { className: 'badge warn', text: 'Connection unconfirmed' }));
    var button = h('button', { type: 'button', className: 'btn secondary sm', text: 'Reconnect', 'data-reconnect': app.session_id });
    button.addEventListener('click', function () { reconnect(state, app, button); });
    row.appendChild(button);
    return row;
  }

  function reconnect(state, app, button) {
    if (state.busy) return;
    state.busy = true; button.disabled = true;
    state.message.textContent = 'Requesting a fresh connection for ' + app.name + '…';
    call(API + '/' + encodeURIComponent(app.session_id) + '/reconnect', { method: 'POST' })
      .then(function (data) {
        if (!data || data.requested !== true) throw new Error('Unconfirmed');
        state.message.textContent = 'Reconnect requested. Open the app’s MCP connection again, or restart ' + app.name + '.';
      }).catch(function () { state.message.textContent = 'Reconnect was not confirmed. Refresh status and retry.'; })
      .finally(function () { state.busy = false; button.disabled = false; });
  }

  function render(state, data) {
    if (!data || !Array.isArray(data.apps) || !Number.isSafeInteger(data.count)) throw new Error('Unavailable');
    var confirmed = Number.isSafeInteger(data.confirmed_count) ? data.confirmed_count
      : data.apps.filter(function (app) { return app.connection_evidence !== 'live-process'; }).length;
    state.count.textContent = confirmed + ' connected' + (confirmed !== data.count ? ' · ' + data.count + ' running' : '');
    state.hostCounts.textContent = '';
    var counts = Object.create(null);
    data.apps.forEach(function (app) { counts[app.name] = (counts[app.name] || 0) + 1; });
    Object.keys(counts).forEach(function (name) {
      state.hostCounts.appendChild(state.h('span', { className: 'badge neutral', text: name + ': ' + counts[name] }));
    });
    state.list.textContent = '';
    data.apps.forEach(function (app) { state.list.appendChild(appRow(state, app, data)); });
    // Apps that carry SLM in their own settings but have no live session yet.
    (data.configured_hosts || []).filter(function (host) { return !host.live; }).forEach(function (host) {
      var row = state.h('li', { className: 'apps-row apps-local-row' });
      row.appendChild(state.h('strong', { text: host.name }));
      row.appendChild(state.h('span', { className: 'badge neutral', text: 'Set up' }));
      row.appendChild(state.h('span', { className: 'muted-info', text: 'Open the app to connect.' }));
      state.list.appendChild(row);
    });
    var shown = data.apps.length + (data.configured_hosts || []).filter(function (host) { return !host.live; }).length;
    state.empty.hidden = shown > 0;
    state.list.hidden = !shown;
    if (state.failed) { state.message.textContent = ''; state.failed = false; }
    fillSelect(state.host, (data.available_hosts || []).filter(function (host) { return !host.connected; }), 'Local app');
    fillSelect(state.profile, data.profiles || [], 'MCP tools');
    state.submit.disabled = !(state.host.options.length && state.profile.options.length);
  }

  function refresh(state) {
    if (state.loading) return Promise.resolve();
    state.loading = true; state.refresh.disabled = true;
    // One quiet retry: a daemon that is busy for a moment is not a failed check.
    var attempt = call(API).catch(function () {
      return new Promise(function (resolve) { window.setTimeout(resolve, 1500); }).then(function () { return call(API); });
    });
    return attempt.then(function (data) { render(state, data); })
      .catch(function () {
        state.failed = true;
        state.count.textContent = 'Status unavailable';
        state.hostCounts.textContent = '';
        state.list.textContent = ''; state.empty.hidden = true; state.submit.disabled = true;
        state.message.textContent = 'Could not check local apps. Click Refresh status to try again.';
      }).finally(function () { state.loading = false; state.refresh.disabled = false; });
  }

  function connect(state, event) {
    event.preventDefault();
    if (state.busy || state.submit.disabled) return;
    state.busy = true; state.submit.disabled = true;
    state.message.textContent = 'Saving the app’s MCP settings…';
    call(API + '/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host: state.host.value, tools_profile: state.profile.value }) })
      .then(function (data) {
        if (!data || data.success !== true) throw new Error('Unconfirmed');
        state.message.textContent = data.message || 'Settings saved. Open or restart the app to connect.';
      }).catch(function () { state.message.textContent = 'App setup was not confirmed. Check the app is installed and retry.'; })
      .finally(function () { state.busy = false; state.submit.disabled = false; });
  }

  function buildForm(state) {
    var h = state.h;
    state.host = h('select', { className: 'tin', 'data-local-host': '' });
    state.profile = h('select', { className: 'tin', 'data-tools-profile': '' });
    state.submit = h('button', { type: 'submit', className: 'btn primary', text: 'Connect app' });
    state.submit.disabled = true;
    var form = h('form', { className: 'card-pad apps-local-setup' }, [
      h('h4', { text: 'Connect a local app' }),
      h('p', { text: 'Choose an app and the tools it can use. Settings are saved here; opening the app establishes its connection.' }),
      h('label', { text: 'App ' }, [state.host]),
      h('label', { text: 'MCP tools ' }, [state.profile]), state.submit
    ]);
    form.addEventListener('submit', function (event) { connect(state, event); });
    return form;
  }

  window.odCreateLocalAppsCard = function () {
    var ui = window.odAppsUi;
    if (!ui) return null;
    var state = { h: ui.h, busy: false, loading: false };
    var h = state.h;
    state.count = h('span', { className: 'badge neutral', text: 'Checking…', 'data-live-count': '' });
    state.refresh = h('button', { type: 'button', className: 'btn secondary sm', text: 'Refresh status' });
    state.list = h('ul', { className: 'apps-list' });
    state.hostCounts = h('div', { className: 'apps-local-counts', 'data-host-counts': '', 'aria-label': 'Live MCP processes by app' });
    state.empty = h('p', { className: 'muted-info', text: 'No local apps connected. Choose your app below to set up its memory tools.' });
    state.empty.hidden = true;
    state.message = h('p', { role: 'status', 'aria-live': 'polite' });
    var card = h('section', { className: 'card', 'data-local-apps': '' }, [
      h('div', { className: 'card-head' }, [h('h3', { text: 'Apps on this computer' }), state.count, state.refresh]),
      h('p', { className: 'card-pad muted-info', text: 'Connected counts include confirmed links. If an older app is missing, reopen its connection so it can report here.' }),
      h('div', { className: 'card-pad' }, [state.hostCounts, state.list, state.empty, state.message]), buildForm(state)
    ]);
    state.refresh.addEventListener('click', function () { refresh(state); });
    card.odRefreshLocalApps = function () { return refresh(state); };
    var timer = window.setInterval(function () {
      if (!card.isConnected) { window.clearInterval(timer); return; }
      if (!state.busy && document.visibilityState !== 'hidden') refresh(state);
    }, 5000);
    refresh(state);
    return card;
  };
}());
