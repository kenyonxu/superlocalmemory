/**
 * Health → Memory health: one card in plain words. It shows the automatic
 * improvement progress, offers pause/resume and a single-run undo, a single
 * "Fix N ..." button per repair that needs a decision, and hosts the upgrade
 * store check (#store-check) inside the same card.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const SCRIPT = readFileSync(new URL('../../src/superlocalmemory/ui/js/od-memory-health.js', import.meta.url), 'utf8');
const tick = () => new Promise((r) => setTimeout(r, 0));

async function mount(state) {
  const dom = new JSDOM('<div id="health"></div>', { runScripts: 'outside-only' });
  const calls = [];
  dom.window.slmFetch = (path, init) => {
    calls.push({ path, method: (init && init.method) || 'GET', body: init && init.body });
    return Promise.resolve(new Response(JSON.stringify(state()), { status: 200 }));
  };
  dom.window.setInterval = () => 0;
  dom.window.eval(SCRIPT);
  dom.window.odMountMemoryHealth(dom.window.document.getElementById('health'));
  for (let i = 0; i < 6; i += 1) await tick();
  const card = dom.window.document.getElementById('memory-health-card');
  return { dom, calls, card };
}
const buttons = (card) => [...card.querySelectorAll('button')].map((b) => b.textContent);

test('the static source never writes untrusted text as HTML', () => {
  assert.ok(!SCRIPT.includes('.innerHTML'));
  for (const text of ['Improving your memories:', '/api/memory-health/action', 'textContent']) {
    assert.ok(SCRIPT.includes(text), text);
  }
});

test('progress is in plain words, with Pause while running and the store check in the same card', async () => {
  const f = await mount(() => ({ enrichment: { run_id: 'r1', status: 'running', processed: 3, total: 12, changed: 0 }, repairs: [] }));
  assert.match(f.card.textContent, /Memory health/);
  assert.match(f.card.textContent, /Improving your memories: 3 of 12/);
  assert.deepEqual(buttons(f.card), ['Pause']);
  assert.ok(f.card.querySelector('#store-check'), 'the store check renders inside Memory health');
  f.card.querySelector('button').click();
  for (let i = 0; i < 6; i += 1) await tick();
  const post = f.calls.find((c) => c.method === 'POST');
  assert.equal(post.path, '/api/memory-health/action');
  assert.deepEqual(JSON.parse(post.body), { action: 'pause', run_id: 'r1' });
});

test('a paused run offers Resume; the most recent changing run gets the one Undo', async () => {
  const paused = await mount(() => ({ enrichment: { run_id: 'r1', status: 'paused', processed: 3, total: 12 }, runs: [] }));
  assert.deepEqual(buttons(paused.card), ['Resume']);
  const done = await mount(() => ({
    enrichment: { run_id: 'r2', status: 'completed', processed: 12, total: 12, changed: 9 },
    runs: [
      { run_id: 'h9', kind: 'repair', status: 'undone', changed: 4, label: 'search sketches' },
      { run_id: 'r2', kind: 'improvement', status: 'completed', changed: 9, label: 'memory descriptions' },
    ],
  }));
  assert.deepEqual(buttons(done.card), ['Undo the last change (9 memory descriptions)']);
  done.card.querySelector('button').click();
  for (let i = 0; i < 6; i += 1) await tick();
  const post = done.calls.find((c) => c.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), { action: 'undo', run_id: 'r2' });
});

test('repairs that delete or move things are suggestions with exactly one Fix button each', async () => {
  const f = await mount(() => ({
    enrichment: { status: 'completed', processed: 12, total: 12, changed: 0 },
    suggestions: [{ action: 'entity-links', found: 35, label: 'entity links' }],
    runs: [],
  }));
  assert.match(f.card.textContent, /35 entity links can be fixed/);
  assert.deepEqual(buttons(f.card), ['Fix 35 entity links']);
  f.card.querySelector('button').click();
  for (let i = 0; i < 6; i += 1) await tick();
  const post = f.calls.find((c) => c.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), { action: 'repair', repair: 'entity-links' });
});

test('states are plain language and never name an exception', async () => {
  const f = await mount(() => ({
    state: 'Some improvements are waiting and will be tried again.',
    enrichment: { status: 'running', processed: 1, total: 2, last_error: 'OperationalError' },
    repairs: [{ run_id: 'e1', action: 'qjl-sketches', status: 'error', error: 'RuntimeError' }],
    runs: [],
  }));
  assert.match(f.card.textContent, /Some improvements are waiting and will be tried again\./);
  assert.doesNotMatch(f.card.textContent, /Error/);
  const idle = await mount(() => ({ enrichment: { status: 'unavailable' }, runs: [] }));
  assert.match(idle.card.textContent, /Not available on this machine\./);
});

test('a refusal shows the service reason, not a generic error', async () => {
  const dom = new JSDOM('<div id="health"></div>', { runScripts: 'outside-only' });
  dom.window.slmFetch = (path, init) => {
    if (init && init.method === 'POST') {
      return Promise.resolve(new Response(JSON.stringify({ detail: 'That run was already undone.' }), { status: 422 }));
    }
    return Promise.resolve(new Response(JSON.stringify({
      enrichment: { status: 'completed', changed: 3 },
      runs: [{ run_id: 'r1', status: 'completed', changed: 3, label: 'memory descriptions' }],
    }), { status: 200 }));
  };
  dom.window.setInterval = () => 0;
  dom.window.eval(SCRIPT);
  dom.window.odMountMemoryHealth(dom.window.document.getElementById('health'));
  for (let i = 0; i < 6; i += 1) await tick();
  const card = dom.window.document.getElementById('memory-health-card');
  card.querySelector('button').click();
  for (let i = 0; i < 12; i += 1) await tick();
  assert.match(card.textContent, /That run was already undone\./);
});

test('after an undo the card says how many changes were restored', async () => {
  const dom = new JSDOM('<div id="health"></div>', { runScripts: 'outside-only' });
  dom.window.slmFetch = (path, init) => {
    if (init && init.method === 'POST') {
      return Promise.resolve(new Response(JSON.stringify({ restored: 27, conflicts: 2 }), { status: 200 }));
    }
    return Promise.resolve(new Response(JSON.stringify({
      enrichment: { status: 'completed', changed: 0 },
      runs: [{ run_id: 'fix1', status: 'completed', changed: 27, label: 'entity links' }],
    }), { status: 200 }));
  };
  dom.window.setInterval = () => 0;
  dom.window.eval(SCRIPT);
  dom.window.odMountMemoryHealth(dom.window.document.getElementById('health'));
  for (let i = 0; i < 6; i += 1) await tick();
  const card = dom.window.document.getElementById('memory-health-card');
  card.querySelector('button').click();
  for (let i = 0; i < 12; i += 1) await tick();
  assert.match(card.textContent, /Undone: 27 changes restored\. 2 kept because they were changed since\./);
});

const SAVES = { action: 'repair-saves', found: 3, label: 'saves that did not finish', sentence: "3 saves didn't finish.", button: 'Fix', undoable: false, note: "Fixing this can't be undone. A safety copy of your memories is taken first, and your memories stay as they are until you confirm." };

test('saves that did not finish read in plain words with a Fix button that asks first', async () => {
  const f = await mount(() => ({
    enrichment: { status: 'completed', processed: 12, total: 12, changed: 0 },
    suggestions: [SAVES],
    runs: [],
  }));
  assert.match(f.card.textContent, /3 saves didn't finish\./);
  assert.match(f.card.textContent, /can't be undone/);
  assert.deepEqual(buttons(f.card), ['Fix']);
  f.card.querySelector('button').click();
  for (let i = 0; i < 6; i += 1) await tick();
  assert.ok(!f.calls.some((c) => c.method === 'POST'), 'the first press only asks');
  assert.deepEqual(buttons(f.card), ['Yes, fix them', 'Cancel']);
  [...f.card.querySelectorAll('button')][0].click();
  for (let i = 0; i < 6; i += 1) await tick();
  const post = f.calls.find((c) => c.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), { action: 'repair', repair: 'repair-saves', confirm: true });
});

test('cancelling the confirm sends nothing', async () => {
  const f = await mount(() => ({
    enrichment: { status: 'completed', processed: 12, total: 12 },
    suggestions: [SAVES],
  }));
  f.card.querySelector('button').click();
  for (let i = 0; i < 6; i += 1) await tick();
  [...f.card.querySelectorAll('button')][1].click();
  for (let i = 0; i < 6; i += 1) await tick();
  assert.ok(!f.calls.some((c) => c.method === 'POST'));
  assert.deepEqual(buttons(f.card), ['Fix']);
});

test('a re-filed-saves run never gets the Undo button', async () => {
  const f = await mount(() => ({
    enrichment: { status: 'completed', processed: 12, total: 12 },
    runs: [{ run_id: 's1', kind: 'repair', status: 'completed', changed: 3, undoable: false, label: 'saves re-filed' }],
  }));
  assert.deepEqual(buttons(f.card), []);
});
