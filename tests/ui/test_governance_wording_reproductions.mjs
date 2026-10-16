/** G-L1/G-L4: governance empty states and peer chips describe actual UI actions/state. */
import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';
import { makeEnv, flushPromises as flushFeatures } from './features_helpers.mjs';

it('retention empty state points to the working New policy control', async () => {
    const h = buildHarness(['root'], { ok: true, status: 200, json: {} });
    h.window.fetch = () => Promise.resolve({ ok: true, status: 200,
        json: () => Promise.resolve({ available: true, retention_policies: [], events: [] }) });
    try {
        evalModule(h.window, 'od-operations.js');
        h.window.odRenderOperations(h.document.getElementById('root'));
        await flushPromises();
        const retention = h.document.querySelector('#od-ret');
        assert.ok(retention);
        assert.match(retention.textContent, /Use New policy/);
        assert.doesNotMatch(retention.textContent, /MCP tool/);
        const button = Array.from(h.document.querySelectorAll('button')).find(
            button => button.textContent.includes('New policy'));
        assert.ok(button);
        assert.equal(h.document.getElementById('od-retention-overlay'), null);
        button.click();
        const dialog = h.document.getElementById('od-retention-overlay');
        assert.equal(dialog.getAttribute('role'), 'dialog');
        assert.ok(dialog.querySelector('#od-rp-name'), 'New policy opens its named form');
    } finally { h.dom.window.close(); }
});

it('Bot messages uses the same descriptive states for active, stale, dead and absent peers', async () => {
    const peers = ['active', 'stale', 'dead'].map(status => (
        { peer_id: status, app: `app-${status}`, kind: 'local', status }
    ));
    const h = makeEnv([
        ['GET', '/api/v3/mesh/messages', () => ({ json: { messages: [
            { id: 1, from: { peer_id: 'absent', app: 'app-absent', kind: 'local' },
                to: 'owner', content: 'fixture-only', trust: 'local-peer',
                sent_at: '2026-10-11T12:00:00Z', refs: [] },
        ] } })],
        ['GET', '/api/v3/mesh/peers', () => ({ json: { peers } })],
    ], { modules: ['od-features.js', 'od-botmessages.js'] });
    try {
        const pane = h.document.getElementById('pane');
        h.window.odRenderBotMessages(pane);
        await flushFeatures();
        const labels = Array.from(pane.querySelectorAll('.od-peer-state')).map(el => el.textContent);
        assert.deepEqual(labels, ['connected', 'not seen recently', 'gone quiet', 'left the mesh']);
    } finally { h.window.close(); }
});
