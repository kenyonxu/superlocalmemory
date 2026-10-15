/**
 * The owner never needs to sign in; the sign-in form appears only once users exist.
 * No default account or password is ever offered.
 * Runner: npm test (scripts/run-ui-tests.mjs). Requires jsdom.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

function mount(userCount, users) {
  const h = buildHarness(['team'], { ok: true, status: 200, json: {} });
  const w = h.window;
  w.fetch = function (url) {
    const u = String(url);
    const body = u.endsWith('/whoami') ? { kind: 'owner', role: 'owner', permissions: ['manage'] }
      : u.endsWith('/status') ? { rbac_active: false, require_login: false, user_count: userCount }
      : u.endsWith('/users') ? { users } : u.endsWith('/members') ? { members: [] } : {};
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  };
  evalModule(w, 'od-team.js');
  return w;
}

async function render(w) {
  const c = w.document.getElementById('team');
  w.odRenderTeam(c);
  for (let i = 0; i < 6; i++) await flushPromises();
  return c;
}

describe('Team & access', function () {
  it('with no users: says no sign-in is needed and shows no sign-in form', async function () {
    const w = mount(0, []);
    const c = await render(w);
    assert.match(c.textContent, /never need to sign in/);
    assert.match(c.textContent, /Signing in is optional/);
    assert.equal(c.querySelector('#od-team-login-btn'), null);
    assert.doesNotMatch(c.textContent, /default password is|password: ?\w+/i);
    w.close();
  });

  it('once users exist: the sign-in form is offered', async function () {
    const w = mount(1, [{ user_id: 'u1', username: 'priya', status: 'active' }]);
    const c = await render(w);
    assert.ok(c.querySelector('#od-team-login-btn'));
    w.close();
  });
});
