// The Health pane's explicit doctor, repair and restart controls.
(function () {
  'use strict';

  function describe(data) {
    var summary = data.summary || {};
    return (summary.passed || 0) + ' passed · ' + (summary.warned || 0) + ' warnings · ' + (summary.failed || 0) + ' failed';
  }

  function runAction(action, panel, button, status, results) {
    if (panel.dataset.busy === 'true') return;
    panel.dataset.busy = 'true';
    panel.querySelectorAll('button').forEach(function (item) { item.disabled = true; });
    status.textContent = action === 'restart' ? 'Restarting this daemon…' : 'Checking this installation…';
    requestAction(action).then(function (result) {
      var data = result.data;
      if (!data || data.success !== true) throw new Error('Unconfirmed');
      if (action === 'restart') { status.textContent = 'Restart requested. Waiting for a new daemon.'; return pollRestart(status, result.previous); }
      status.textContent = (action === 'repair' ? 'Repair check complete: ' : 'Doctor complete: ') + describe(data);
      results.textContent = '';
      (data.checks || []).forEach(function (check) {
        var row = document.createElement('li');
        row.textContent = check.status + ' · ' + check.name + ': ' + check.detail;
        results.appendChild(row);
      });
    }).catch(function () { status.textContent = 'The action could not be confirmed. Click again to retry.'; })
      .finally(function () {
        panel.dataset.busy = 'false'; panel.querySelectorAll('button').forEach(function (item) { item.disabled = false; });
      });
  }

  function requestAction(action) {
    var previous = null;
    var baseline = action === 'restart' ? fetch('/health', { cache: 'no-store' }).then(function (response) {
      if (!response.ok) throw new Error('Health unavailable');
      return response.json();
    }).then(function (data) {
      if (!data || !data.instance_id) throw new Error('Daemon identity unavailable');
      previous = data.instance_id;
    }) : Promise.resolve();
    return baseline.then(function () { return fetch('/api/daemon/' + action, { method: 'POST', timeoutMs: 50000 }); })
      .then(function (response) {
        if (!response.ok) throw new Error('Unavailable');
        return response.json();
      }).then(function (data) { return { data: data, previous: previous }; });
  }

  function pollRestart(status, previous) {
    return new Promise(function (resolve) {
      var deadline = Date.now() + 60000;
      function finish(message) { status.textContent = message; resolve(); }
      function retry() {
        if (Date.now() < deadline) window.setTimeout(poll, 2000);
        else finish('Restart is still unconfirmed. Refresh health to check again.');
      }
      function poll() {
        fetch('/health', { cache: 'no-store', timeoutMs: 2000 }).then(function (response) { return response.ok ? response.json() : null; }).then(function (data) {
          if (data && data.instance_id && data.instance_id !== previous) {
            finish(data.ready ? 'Daemon is ready. Refresh health to see its status.' : 'New daemon is running. Retrieval is still warming up.');
          } else retry();
        }).catch(retry);
      }
      window.setTimeout(poll, 2000);
    });
  }

  window.odMountDaemonControls = function (container) {
    if (!container) return;
    var panel = document.createElement('section'); panel.className = 'card card-pad';
    var title = document.createElement('h3'); title.textContent = 'Daemon controls'; panel.appendChild(title);
    var status = document.createElement('p'); status.setAttribute('role', 'status'); panel.appendChild(status);
    var results = document.createElement('ul'); panel.appendChild(results);
    ['doctor', 'repair', 'restart'].forEach(function (action) {
      var button = document.createElement('button'); button.type = 'button'; button.className = 'btn secondary';
      button.dataset.daemonAction = action;
      button.textContent = { doctor: 'Run doctor', repair: 'Repair', restart: 'Restart daemon' }[action];
      button.addEventListener('click', function () { runAction(action, panel, button, status, results); });
      panel.appendChild(button);
    });
    container.appendChild(panel);
  };
}());
