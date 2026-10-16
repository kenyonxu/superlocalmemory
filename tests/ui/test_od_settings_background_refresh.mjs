/**
 * Settings must render into a fresh pane copy even while the old pane is still
 * in the page (the shell's background refresh). A page-wide "already built"
 * check rendered nothing there and the empty copy was swapped in: blank Settings.
 * Runner: npm test (scripts/run-ui-tests.mjs). Requires jsdom.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

describe('Settings background refresh', function () {
  it('a fresh pane copy gets its own hub while the old one is still mounted', async function () {
    const h = buildHarness(['old-pane', 'fresh-pane'], { ok: true, status: 200, json: {} });
    const w = h.window, d = w.document;
    evalModule(w, 'od-settings.js');
    w.odRenderSettings(d.getElementById('old-pane'));
    await flushPromises();
    assert.ok(d.querySelector('#old-pane #od-settings-hub'), 'old pane rendered');
    w.odRenderSettings(d.getElementById('fresh-pane'));
    await flushPromises();
    const fresh = d.querySelector('#fresh-pane #od-settings-hub');
    assert.ok(fresh, 'the fresh copy must get its own hub, not stay empty');
    assert.match(fresh.textContent, /Search every setting|Settings/);
    w.close();
  });
});
