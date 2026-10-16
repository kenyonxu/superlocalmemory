// One Memory health place: automatic improvement, safe repair, and undo.
(function () {
  'use strict';
  var timer = null;
  var mounted = null;
  var generation = 0;
  var requestNumber = 0;
  var activeRead = false;
  var mutationBusy = false;

  function el(tag, text) {
    var node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  var STATES = {
    waiting_for_idle: 'Waiting for your computer to be idle.',
    queued: 'Waiting for your computer to be idle.',
    running: 'Improving your memories in the background.',
    paused: 'Paused. Your memories still work.',
    completed: 'Up to date. Checked again while your computer is idle.',
    undone: 'The last improvement was undone. Your memories still work.',
    unavailable: 'Not available on this machine.'
  };
  var notice = '';
  var confirming = '';
  var lastData = {};

  function call(method, payload) {
    var fetcher = window.slmFetch || window.fetch;
    return fetcher((method === 'POST' ? '/api/memory-health/action' : '/api/memory-health'), {
      method: method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: payload ? JSON.stringify(payload) : undefined
    }).then(function (r) {
      if (r.ok) return r.json();
      // A refusal carries the service's own plain reason ("That run was
      // already undone."); anything else gets one generic sentence.
      return r.json().catch(function () { return {}; }).then(function (body) {
        var reason = body && typeof body.detail === 'string' ? body.detail : '';
        throw new Error(reason || 'Memory health is temporarily unavailable.');
      });
    });
  }
  function button(parent, label, action, local) {
    var node = el('button', label);
    node.className = 'btn secondary';
    node.disabled = mutationBusy;
    node.addEventListener('click', function () {
      if (mutationBusy) return;
      if (local) {
        // A step that sends nothing: it only changes what the card shows next.
        local();
        render(lastData);
        return;
      }
      confirming = '';
      mutationBusy = true;
      requestNumber += 1;
      node.disabled = true;
      notice = '';
      call('POST', action).then(function (result) {
        if (action.action === 'undo' && result && typeof result.restored === 'number') {
          notice = 'Undone: ' + result.restored + ' changes restored.' +
            (result.conflicts ? ' ' + result.conflicts + ' kept because they were changed since.' : '');
        }
      }).catch(function (error) {
        notice = error.message;
      }).finally(function () { mutationBusy = false; refresh(); });
    });
    parent.appendChild(node);
  }
  function render(data) {
    if (!mounted || !mounted.isConnected) return;
    lastData = data;
    mounted.replaceChildren();
    var run = data.enrichment || {};
    var status = run.status || 'waiting_for_idle';
    mounted.appendChild(el('p', 'Improving your memories: ' + (run.processed || 0) +
                           ' of ' + (run.total || 0)));
    // Plain sentences only: exception names stay in the service log.
    mounted.appendChild(el('p', data.state || STATES[status] || 'Your memories still work.'));
    if (run.run_id && status === 'paused') {
      button(mounted, 'Resume', { action: 'resume', run_id: run.run_id });
    } else if (run.run_id && (status === 'queued' || status === 'running')) {
      button(mounted, 'Pause', { action: 'pause', run_id: run.run_id });
    }
    // Repairs that delete or move things never run on their own: one Fix each.
    (data.suggestions || []).forEach(function (item) {
      mounted.appendChild(el('p', item.sentence || (item.found + ' ' + item.label +
                             ' can be fixed. Nothing changes until you choose to.')));
      // A repair with no undo says so plainly and asks before it runs.
      if (item.undoable === false) {
        mounted.appendChild(el('p', item.note || "Fixing this can't be undone."));
      }
      if (confirming === item.action) {
        button(mounted, 'Yes, fix them', { action: 'repair', repair: item.action, confirm: true });
        button(mounted, 'Cancel', null, function () { confirming = ''; });
        return;
      }
      var label = item.button || ('Fix ' + item.found + ' ' + item.label);
      if (item.undoable === false) {
        button(mounted, label, null, function () { confirming = item.action; });
      } else {
        button(mounted, label, { action: 'repair', repair: item.action });
      }
    });
    if ((data.repairs || []).some(function (r) { return r.status === 'error'; })) {
      mounted.appendChild(el('p', 'Some repairs are waiting and will be tried again.'));
    }
    var latest = (data.runs || []).find(function (r) {
      return r.status !== 'undone' && r.undoable !== false;
    });
    if (latest) {
      button(mounted, 'Undo the last change (' + latest.changed + ' ' + latest.label + ')',
             { action: 'undo', run_id: latest.run_id });
    }
    if (notice) {
      var message = el('p', notice);
      message.setAttribute('role', 'alert');
      mounted.appendChild(message);
    }
  }
  function refresh() {
    if (!mounted || !mounted.isConnected || activeRead || mutationBusy) return;
    var target = mounted;
    var mountGeneration = generation;
    var sequence = ++requestNumber;
    activeRead = true;
    return call('GET').then(function (data) {
      if (generation === mountGeneration && requestNumber === sequence && mounted === target) {
        render(data);
      }
    }).catch(function (error) {
      if (generation === mountGeneration && requestNumber === sequence && mounted === target) {
        target.replaceChildren(el('p', error.message));
      }
    }).finally(function () {
      if (generation === mountGeneration) activeRead = false;
    });
  }
  window.odMountMemoryHealth = function (parent) {
    clearInterval(timer);
    generation += 1;
    activeRead = false;
    // One card: the automatic improvements and repairs (refreshed below) and
    // the upgrade store check (od-store-check.js renders into #store-check).
    var card = el('section');
    card.className = 'ng-glass';
    card.id = 'memory-health-card';
    card.style.padding = '20px';
    card.appendChild(el('h3', 'Memory health'));
    mounted = el('div');
    mounted.setAttribute('aria-live', 'polite');
    card.appendChild(mounted);
    var store = el('div');
    store.id = 'store-check';
    store.style.marginTop = '12px';
    card.appendChild(store);
    parent.prepend(card);
    if (window.odStoreCheck) window.odStoreCheck.init();
    refresh();
    timer = setInterval(function () {
      if (!mounted || !mounted.isConnected) {
        clearInterval(timer);
        return;
      }
      refresh();
    }, 5000);
  };
}());
