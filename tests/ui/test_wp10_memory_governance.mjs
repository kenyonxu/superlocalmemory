import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const note = { id:'m1', content:'port 6001', importance:0.8, created_at:'2026-10-11' };

async function memoryHarness() {
  const h = buildHarness(['root'], { ok:true, status:200, json:{} });
  let writes = 0;
  h.window.dashboardInstallToken = async () => 'fixture';
  h.window.fetch = async (url, init = {}) => {
    if (init.method === 'PATCH') return ++writes === 1 ?
      reply({ detail:'The memory store is busy finishing other writes; nothing was changed. Retry in a few seconds.' }, 503) : reply({ success:true });
    return reply(String(url).startsWith('/api/memories?') ? { memories:[note], total:1 } : {});
  };
  evalModule(h.window, 'od-memories.js');
  h.window.odRenderMemories(h.document.getElementById('root'));
  await flushPromises();
  return h;
}

it('Recall Lab page size keeps an accessible name after dynamic mounting', async () => {
  const h = await memoryHarness();
  assert.equal(h.document.getElementById('recall-lab-per-page').getAttribute('aria-label'), 'Results per page');
  h.dom.window.close();
});

it('UX-23: a named memory button opens its drawer and Escape returns focus', async () => {
  const h = await memoryHarness();
  const open = h.document.querySelector('button[data-od-act="drawer"]');
  assert.ok(open, 'the main list has no keyboard control for opening a memory');
  assert.match(open.getAttribute('aria-label'), /Open memory/);
  open.focus(); open.click();
  await flushPromises();
  assert.ok(h.document.getElementById('od-mem-drawer').contains(h.document.activeElement));
  h.document.dispatchEvent(new h.window.KeyboardEvent('keydown', { key:'Escape' }));
  assert.equal(h.document.activeElement, open);
  h.dom.window.close();
});

it('UX-09: a busy-store edit keeps the draft and has an explicit retry', async () => {
  const h = await memoryHarness();
  h.document.querySelector('[data-od-act="drawer"]').click();
  h.document.querySelector('[data-od-act="edit-mem"]').click();
  h.document.getElementById('od-ea').value = 'port 6002';
  h.document.querySelector('[data-od-act="save-edit"]').click();
  await flushPromises(); await flushPromises();
  assert.equal(h.document.getElementById('od-ea').value, 'port 6002');
  assert.match(h.document.getElementById('od-mem-drawer').textContent, /busy finishing other writes/);
  const retry = h.document.querySelector('[data-od-act="retry-memory-write"]');
  assert.ok(retry);
  retry.click();
  await flushPromises(); await flushPromises();
  assert.equal(h.document.getElementById('od-mem-drawer').classList.contains('open'), false);
  h.dom.window.close();
});

it('UX-37: entity selectors are named native buttons with selection state', async () => {
  const h = buildHarness(['root'], { ok:true, status:200, json:{ entities:[{name:'Atlas',type:'project',fact_count:2}], total:1 } });
  evalModule(h.window, 'od-entities.js');
  h.window.odRenderEntities(h.document.getElementById('root'));
  await flushPromises();
  const entity = h.document.querySelector('[data-od-act="select-entity"]');
  assert.equal(entity.tagName, 'BUTTON');
  assert.ok(['true','false'].includes(entity.getAttribute('aria-pressed')));
  entity.click(); await flushPromises();
  assert.equal(h.document.querySelector('[data-od-act="select-entity"]').getAttribute('aria-pressed'), 'true');
  h.dom.window.close();
});

it('UX-06/G-H2: real audit schema displays actor, action, target, outcome, profile and time', async () => {
  const h = buildHarness(['root'], { ok:true, status:200, json:{} });
  h.window.fetch = async url => reply(String(url).includes('/audit') ? { events:[{
    timestamp:'2026-10-11T10:00:00Z', operation:'store', agent_id:'agent-1', profile_id:'work',
    metadata:{ fact_id:'fact-1', outcome:'success' }
  }] } : { available:true });
  evalModule(h.window, 'od-operations.js');
  h.window.odRenderOperations(h.document.getElementById('root'));
  await flushPromises();
  const row = h.document.querySelector('#od-audit-body tr');
  for (const value of ['2026-10-11', 'agent-1', 'store', 'fact-1', 'success', 'work']) assert.ok(row.textContent.includes(value), row.textContent);
  h.dom.window.close();
});

it('UX-46: successful policy creation leaves Compliance selected', async () => {
  const h = buildHarness(['operations-pane'], { ok:true, status:200, json:{} });
  h.window.fetch = async () => reply({ available:true, success:true, retention_policies:[{name:'keep',retention_days:365}] });
  evalModule(h.window, 'od-operations.js');
  h.window.odRenderOperations(h.document.getElementById('operations-pane'));
  await flushPromises();
  h.document.querySelector('[data-od-tab="compliance"]').click();
  const create = [...h.document.querySelectorAll('button')].find(el => /New policy/.test(el.textContent));
  create.click();
  h.document.getElementById('od-rp-name').value = 'keep';
  h.document.getElementById('od-rp-days').value = '365';
  h.document.getElementById('od-rp-create').click();
  await flushPromises(); await flushPromises();
  assert.equal(h.document.querySelector('[data-od-tab].active').dataset.odTab, 'compliance');
  assert.match(h.document.querySelector('[data-od-pane="compliance"]').textContent, /keep/);
  h.dom.window.close();
});
