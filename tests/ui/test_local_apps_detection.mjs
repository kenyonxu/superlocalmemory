import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const ui = new URL('../../src/superlocalmemory/ui/js/', import.meta.url);
const windows = [];
afterEach(() => { while (windows.length) windows.pop().close(); });
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0)); };

function boot(responses) {
  const dom = new JSDOM('<div id="apps"></div>', { url: 'http://127.0.0.1:19781', runScripts: 'dangerously' });
  windows.push(dom.window);
  const queue = [...responses];
  dom.window.setTimeout = (fn) => { fn(); return 1; };  // retry delay collapses
  dom.window.fetch = async () => {
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (next === 'fail') return { ok: false, status: 503, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => next };
  };
  for (const name of ['od-apps-ui.js', 'od-local-apps.js', 'od-apps.js']) {
    dom.window.eval(readFileSync(new URL(name, ui), 'utf8'));
  }
  return { window: dom.window, document: dom.window.document };
}

const profiles = [{ id: 'core', name: 'Memory essentials' }, { id: 'code', name: 'Coding' }];
const healthy = () => ({
  count: 1, confirmed_count: 1, installed_version: '4.2.0',
  apps: [{ session_id: 'a', host: 'codex', name: 'Codex', code_version: '4.2.0', stale_version: false,
    tools: ['recall'], tools_profile: 'core', connection_evidence: 'initialized-session' }],
  configured_hosts: [{ id: 'codex', name: 'Codex', live: 1 }, { id: 'claude-desktop', name: 'Claude Desktop', live: 0 }],
  available_hosts: [
    { id: 'claude-desktop', name: 'Claude Desktop', configured: true, connected: false },
    { id: 'codex', name: 'Codex', configured: true, connected: true },
    { id: 'cursor', name: 'Cursor', configured: false, connected: false },
  ],
  profiles,
});

describe('Connected apps detection', () => {
  it('a later successful check clears an earlier failure message', async () => {
    const { window, document } = boot(['fail', 'fail', healthy()]);
    await window.odRenderApps(document.getElementById('apps')); await settle();
    const panel = document.querySelector('[data-local-apps]');
    assert.match(panel.textContent, /Could not check local apps/);
    await panel.odRefreshLocalApps(); await settle();
    assert.doesNotMatch(panel.textContent, /Could not check local apps/);
    assert.match(panel.querySelector('[data-live-count]').textContent, /1 connected/);
  });

  it('retries once before reporting a failed check', async () => {
    const { window, document } = boot(['fail', healthy()]);
    await window.odRenderApps(document.getElementById('apps')); await settle();
    const panel = document.querySelector('[data-local-apps]');
    assert.doesNotMatch(panel.textContent, /Could not check local apps/);
    assert.match(panel.textContent, /Codex/);
  });

  it('shows the tools selection and configured-but-idle apps', async () => {
    const { window, document } = boot([healthy()]);
    await window.odRenderApps(document.getElementById('apps')); await settle();
    const panel = document.querySelector('[data-local-apps]');
    assert.match(panel.textContent, /Memory essentials/);
    assert.match(panel.textContent, /Claude Desktop[\s\S]*Set up/);
  });

  it('never offers an app that is already connected or set up as the default to connect', async () => {
    const { window, document } = boot([healthy()]);
    await window.odRenderApps(document.getElementById('apps')); await settle();
    const select = document.querySelector('[data-local-host]');
    const values = [...select.options].map(o => o.value);
    assert.ok(!values.includes('codex'));
    assert.equal(select.value, 'cursor');
  });
});
