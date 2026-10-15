/** UX-20/21: self-role changes are confirmed and refused changes remain visible. */
import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

async function team() {
    const h = buildHarness(['root'], { ok: true, status: 200, json: {} });
    const calls = [];
    const bodies = {
        '/api/rbac/whoami': { kind: 'user', user_id: 'admin', username: 'alice',
            role: 'admin', profile: 'default', permissions: ['read', 'write', 'manage'] },
        '/api/rbac/status': { require_login: true, user_count: 2 },
        '/api/rbac/users': { users: [
            { user_id: 'admin', username: 'alice', status: 'active' },
            { user_id: 'viewer', username: 'bob', status: 'active' },
        ] },
        '/api/rbac/members': { members: [
            { user_id: 'admin', role: 'admin' }, { user_id: 'viewer', role: 'viewer' },
        ] },
    };
    h.window.fetch = (url, options = {}) => {
        calls.push({ url, method: options.method || 'GET', body: options.body });
        const refused = url === '/api/rbac/members' && options.method === 'POST';
        const body = refused ? { detail: 'Add another administrator before changing access.' }
            : bodies[url] || { success: true };
        return Promise.resolve({ ok: !refused, status: refused ? 409 : 200,
            json: () => Promise.resolve(body) });
    };
    evalModule(h.window, 'od-team.js');
    h.window.odRenderTeam(h.document.getElementById('root'));
    await flushPromises();
    return { ...h, calls };
}

it('cancelled self-role change sends nothing and confirmed refusal stays visible', async () => {
    const h = await team();
    try {
        const select = h.document.querySelector('.od-team-role[data-uid="admin"]');
        assert.ok(select);
        let confirmations = 0;
        h.window.confirmDestructive = () => { confirmations++; return Promise.resolve(false); };
        select.value = 'viewer';
        select.dispatchEvent(new h.window.Event('change'));
        await flushPromises();
        assert.equal(confirmations, 1);
        assert.equal(select.value, 'admin');
        assert.ok(!h.calls.some(call => call.method === 'POST'));
        h.window.confirmDestructive = () => Promise.resolve(true);
        select.value = 'viewer';
        select.dispatchEvent(new h.window.Event('change'));
        await flushPromises();
        assert.equal(select.value, 'admin');
        assert.match(h.document.getElementById('od-team-msg').textContent, /another administrator/);
        assert.equal(h.calls.filter(call => call.method === 'POST').length, 1);
    } finally { h.dom.window.close(); }
});

it('the rendered recovery control resets another user through the account API', async () => {
    const h = await team();
    try {
        const reset = h.document.querySelector('.od-team-reset[data-uid="viewer"]');
        assert.ok(reset);
        assert.match(reset.textContent, /Reset password/);
        reset.click();
        h.document.getElementById('od-team-reset-pass').value = 'fixture-password';
        h.document.getElementById('od-team-reset-again').value = 'fixture-password';
        h.document.getElementById('od-team-reset-save').click();
        await flushPromises();
        const sent = h.calls.find(call => call.method === 'PATCH');
        assert.equal(sent.url, '/api/rbac/users/viewer');
        assert.equal(JSON.parse(sent.body).password, 'fixture-password');
        assert.match(h.document.getElementById('od-team-msg').textContent, /sessions are signed out/);
        assert.ok(h.document.querySelector('.od-team-status[data-uid="viewer"]'));
    } finally { h.dom.window.close(); }
});
