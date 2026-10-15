/**
 * Company mode: a signed-out visitor gets one sign-in screen (never panes that
 * never load); a signed-in person sees who they are with Sign out; nothing is
 * shown when sign-in is not required.
 * Runner: npm test (scripts/run-ui-tests.mjs). Requires jsdom.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

function mount(who, loginReply) {
  const h = buildHarness(['main'], { ok: true, status: 200, json: {} });
  const w = h.window;
  w.document.body.insertAdjacentHTML('afterbegin', '<div class="topbar"><a class="star-cta">Star</a></div>');
  const posts = [];
  w.fetch = function (url, init) {
    const u = String(url);
    if (u.endsWith('/api/rbac/whoami')) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(who) });
    if (u.endsWith('/api/rbac/login')) { posts.push(JSON.parse(init.body)); return Promise.resolve(loginReply); }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  evalModule(w, 'od-signin-gate.js');
  return { w, d: w.document, posts };
}
const settle = async () => { for (let i = 0; i < 6; i++) await flushPromises(); };

describe('sign-in gate', function () {
  it('company mode, signed out: one sign-in screen with a way to manage users', async function () {
    const t = mount({ kind: 'owner', require_login: true }, { ok: false, status: 401,
      json: () => Promise.resolve({ detail: 'Invalid username or password.' }) });
    await settle();
    const gate = t.d.getElementById('slm-signin');
    assert.ok(gate, 'sign-in screen shown');
    assert.equal(gate.getAttribute('role'), 'dialog');
    assert.match(gate.textContent, /Sign in to SuperLocalMemory/);
    assert.ok(t.d.getElementById('slm-signin-manage'));
    t.d.getElementById('slm-signin-user').value = 'priya';
    t.d.getElementById('slm-signin-pass').value = 'wrong-password';
    t.d.querySelector('#slm-signin form, #slm-signin .card').dispatchEvent(new t.w.Event('submit', { cancelable: true }));
    await settle();
    assert.deepEqual(t.posts[0], { username: 'priya', password: 'wrong-password' });
    assert.match(t.d.getElementById('slm-signin-err').textContent, /Invalid username or password/);
    t.w.close();
  });

  it('signed in: no gate, a badge says who, role and workspace, with Sign out', async function () {
    const t = mount({ kind: 'user', username: 'arjun', role: 'viewer', profile: 'acme', require_login: true });
    await settle();
    assert.equal(t.d.getElementById('slm-signin'), null);
    const b = t.d.getElementById('slm-whoami');
    assert.ok(b);
    assert.match(b.textContent, /arjun · viewer · acme/);
    assert.match(b.textContent, /Sign out/);
    t.w.close();
  });

  it('personal mode: nothing at all', async function () {
    const t = mount({ kind: 'owner', require_login: false });
    await settle();
    assert.equal(t.d.getElementById('slm-signin'), null);
    assert.equal(t.d.getElementById('slm-whoami'), null);
    t.w.close();
  });
});
