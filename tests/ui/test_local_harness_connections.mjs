import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const ui = new URL('../../src/superlocalmemory/ui/js/', import.meta.url);
const windows = [];
afterEach(() => { while (windows.length) windows.pop().close(); });
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 0)); };
function boot(data) {
  const dom = new JSDOM('<div id="apps"></div><div id="health"></div>', { url: 'http://127.0.0.1:19781', runScripts: 'dangerously' });
  windows.push(dom.window);
  const calls = [];
  dom.window.fetch = async (path, init = {}) => {
    calls.push({ path, init });
    let body = path === '/api/v3/local-apps' ? data : { success: true, summary: { passed: 3 }, checks: [] };
    if (path.includes('/reconnect')) body = { requested: true };
    if (path.endsWith('/connect')) body = { success: true, connected: false, message: 'Settings saved. Open the app to connect.' };
    return { ok: true, status: 200, json: async () => body };
  };
  for (const name of ['od-apps-ui.js', 'od-local-apps.js', 'od-apps.js', 'od-daemon-controls.js', 'od-health.js']) {
    dom.window.eval(readFileSync(new URL(name, ui), 'utf8'));
  }
  return { dom, calls, window: dom.window, document: dom.window.document };
}
const status = () => ({ count: 2, stale_count: 1, installed_version: '4.1.25',
  apps: [
    { session_id: 'one', host: 'codex', name: 'Codex', code_version: '4.1.24', stale_version: true, tools: ['recall'] },
    { session_id: 'two', host: 'hermes', name: 'Hermes', code_version: '4.1.25', stale_version: false, tools: ['recall', 'remember'] }
  ], available_hosts: [{ id: 'codex', name: 'Codex' }], profiles: [{ id: 'core', name: 'Memory essentials', description: 'Save and find' }, { id: 'code', name: 'Coding' }] });

describe('live local harnesses in Connected apps', () => {
  it('shows live count, imported version and stale badge; reconnect requires a click', async () => {
    const { dom, window, document, calls } = boot(status());
    await window.odRenderApps(document.getElementById('apps'));
    await settle();
    const panel = document.querySelector('[data-local-apps]');
    assert.match(panel.textContent, /2 connected/);
    assert.match(panel.querySelector('[data-host-counts]').textContent, /Codex: 1/);
    assert.match(panel.querySelector('[data-host-counts]').textContent, /Hermes: 1/);
    assert.match(panel.textContent, /Codex/);
    assert.match(panel.textContent, /Stale version/);
    assert.equal(calls.filter(c => c.init.method === 'POST').length, 0);
    panel.querySelector('[data-reconnect="one"]').click();
    await settle();
    assert.ok(calls.some(c => c.path === '/api/v3/local-apps/one/reconnect' && c.init.method === 'POST'));
    dom.window.close();
  });

  it('connects the selected host and MCP tool profile without calling it live yet', async () => {
    const { dom, window, document, calls } = boot({ ...status(), apps: [], count: 0 });
    await window.odRenderApps(document.getElementById('apps'));
    await settle();
    const panel = document.querySelector('[data-local-apps]');
    assert.match(panel.textContent, /No local apps connected/);
    panel.querySelector('[data-tools-profile]').value = 'core';
    panel.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await settle();
    const call = calls.find(c => c.path.endsWith('/local-apps/connect'));
    assert.deepEqual(JSON.parse(call.init.body), { host: 'codex', tools_profile: 'core' });
    assert.match(panel.textContent, /Settings saved/);
    assert.equal(panel.querySelector('[data-live-count]').textContent, '0 connected');
    dom.window.close();
  });

  it('escapes host names and offers setup without terminal instructions', async () => {
    const data = status(); data.apps[0].name = '<img src=x onerror=alert(1)>';
    const { dom, window, document } = boot(data);
    await window.odRenderApps(document.getElementById('apps')); await settle();
    const panel = document.querySelector('[data-local-apps]');
    assert.equal(panel.querySelector('img'), null);
    assert.ok(panel.textContent.includes('<img'));
    assert.doesNotMatch(panel.textContent, /slm connect|terminal|Run:/);
    dom.window.close();
  });

  it('shows pre-registry sessions without inventing their version or tool count', async () => {
    const data = { ...status(), confirmed_count: 1 }; data.apps[0] = { ...data.apps[0], version_reported: false,
      connection_evidence: 'live-process', code_version: 'unreported', stale_version: false, tools: [] };
    const { dom, window, document } = boot(data);
    await window.odRenderApps(document.getElementById('apps')); await settle();
    const panel = document.querySelector('[data-local-apps]');
    assert.match(panel.textContent, /Version unreported/);
    assert.match(panel.textContent, /Tools unreported/);
    assert.match(panel.textContent, /Connection unconfirmed/);
    assert.match(panel.querySelector('[data-live-count]').textContent, /1 connected/);
    dom.window.close();
  });
});

describe('Health controls and live connections', () => {
  it('doctor and repair send authorized actions on click and display results', async () => {
    const { dom, window, document, calls } = boot(status());
    await window.odRenderHealth(document.getElementById('health')); await settle();
    assert.match(document.getElementById('health').textContent, /Codex/);
    assert.equal(calls.filter(c => c.init.method === 'POST').length, 0);
    document.querySelector('[data-daemon-action="doctor"]').click(); await settle();
    document.querySelector('[data-daemon-action="repair"]').click(); await settle();
    assert.ok(calls.some(c => c.path === '/api/daemon/doctor'));
    assert.equal(calls.find(c => c.path === '/api/daemon/doctor').init.timeoutMs, 50000);
    assert.ok(calls.some(c => c.path === '/api/daemon/repair'));
    assert.match(document.getElementById('health').textContent, /3 passed/);
    dom.window.close();
  });

  it('components needing setup offer a clickable settings action', async () => {
    const { dom, window, document } = boot(status());
    window.fetch = async () => ({ ok: true, json: async () => ({ components: [
      { name: 'optional', label: 'Optional backend', status: 'missing', category: 'optional', auto_fixable: false, fix_cmd: 'pip install optional' }
    ], summary: { missing: 1 } }) });
    window.eval(readFileSync(new URL('od-components.js', ui), 'utf8'));
    window.odRenderComponents(document.getElementById('health')); await settle();
    assert.ok(document.querySelector('a[href="#settings-pane"]'));
    assert.doesNotMatch(document.getElementById('health').textContent, /pip install|Copy a command/);
    dom.window.close();
  });

  it('Settings distinguishes the live port from the saved next-start port', async () => {
    const { dom, window, document } = boot(status());
    window.fetch = async path => ({ ok: true, json: async () => path === '/api/v3/daemon/config'
      ? { port: 19781, configured_port: 19281, legacy_port: 19282, enable_legacy_port: false } : {} });
    ['od-settings-models.js','od-settings-groups.js','od-settings.js'].forEach(name => window.eval(readFileSync(new URL(name, ui), 'utf8')));
    window.odRenderSettings(document.getElementById('health')); await settle();
    assert.equal(document.getElementById('od-s-dmn-port').value, '19281');
    assert.match(document.getElementById('od-s-dmn-live-port').textContent, /19781/);
    dom.window.close();
  });

  it('queued embedding changes explain the wait while the current model serves', async () => {
    const { dom, window, document } = boot(status());
    window.eval(readFileSync(new URL('od-reindex.js', ui), 'utf8'));
    document.getElementById('health').appendChild(window.odReindex.panel());
    window.odReindex.track({ job: { job_id: 'q1', state: 'queued', from: 'old', to: 'new', done: 0, total: 4,
      queued_reason: 'Waiting for the background worker; the current model continues serving recall.' } });
    assert.match(document.getElementById('health').textContent, /background worker/);
    assert.match(document.getElementById('health').textContent, /Recall keeps using old/);
    dom.window.close();
  });

  it('restart waits for a different daemon instance before confirming it', async () => {
    const { dom, window, document } = boot(status());
    const queue = []; let reads = 0;
    window.setTimeout = callback => { queue.push(callback); return queue.length; };
    window.fetch = async path => ({ ok: true, json: async () => path === '/health'
      ? { ready: true, instance_id: ++reads < 3 ? 'old' : 'new' } : { success: true } });
    window.odMountDaemonControls(document.getElementById('health'));
    document.querySelector('[data-daemon-action="restart"]').click(); await settle();
    queue.shift()(); await settle();
    assert.match(document.getElementById('health').textContent, /Waiting for a new daemon/);
    assert.equal(document.querySelector('[data-daemon-action="doctor"]').disabled, true);
    queue.shift()(); await settle();
    assert.match(document.getElementById('health').textContent, /Daemon is ready/);
    assert.equal(document.querySelector('[data-daemon-action="doctor"]').disabled, false);
    dom.window.close();
  });
});
