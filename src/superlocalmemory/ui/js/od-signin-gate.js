// od-signin-gate.js: the sign-in screen when this SuperLocalMemory requires one.
//
// With "require every person to sign in" on (company mode), memory is read
// only by signed-in people. Without this screen a signed-out visitor saw panes
// that never loaded. Here they get one clear sign-in form; once signed in, the
// header says who they are, their role and workspace, with Sign out. The
// machine owner can always open Governance to manage users. Nothing here is
// shown when sign-in is not required.
(function () {
  'use strict';

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }

  function style() {
    if (document.getElementById('slm-signin-style')) return;
    var s = document.createElement('style');
    s.id = 'slm-signin-style';
    s.textContent =
      '#slm-signin{position:fixed;inset:0;z-index:2000;display:flex;align-items:center;justify-content:center;' +
      'background:var(--page,#0f0d17);padding:16px}' +
      '#slm-signin .card{max-width:420px;width:100%;padding:28px;border-radius:16px;' +
      'background:var(--card,#16131f);border:1px solid var(--border,#2a2540);color:var(--fg,#eee)}' +
      '#slm-signin img{width:72px;height:72px;display:block;margin:0 auto 12px}' +
      '#slm-signin h2{font-size:20px;margin:0 0 6px;text-align:center}' +
      '#slm-signin p{font-size:13px;color:var(--fg-2,#aaa);margin:0 0 14px;text-align:center}' +
      '#slm-signin input{width:100%;margin:0 0 10px;padding:10px 12px;border-radius:8px;' +
      'border:1px solid var(--border,#2a2540);background:var(--card-2,#1c1828);color:var(--fg,#eee);font-size:14px}' +
      '#slm-signin button{width:100%;padding:10px;border-radius:8px;font-size:14px;cursor:pointer}' +
      '#slm-signin .primary{background:var(--violet,#6d4dff);color:#fff;border:0}' +
      '#slm-signin .link{background:none;border:0;color:var(--violet-text,#a99bff);margin-top:10px}' +
      '#slm-signin .err{color:var(--danger,#ff7a8a);min-height:18px;margin:6px 0 0}' +
      '#slm-whoami{display:inline-flex;align-items:center;gap:8px;font-size:12px;margin-right:8px}' +
      '#slm-whoami button{font-size:12px;padding:3px 8px;border-radius:6px;cursor:pointer}';
    document.head.appendChild(s);
  }

  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().catch(function () { return {}; })
        .then(function (b) { return { ok: r.ok, status: r.status, body: b }; }); });
  }

  function gate(who) {
    style();
    if (document.getElementById('slm-signin')) return;
    var wrap = el('div'); wrap.id = 'slm-signin';
    wrap.setAttribute('role', 'dialog'); wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('aria-labelledby', 'slm-signin-title');
    var card = el('form', 'card');
    var img = el('img'); img.src = '/static/assets/mascot/hero-320.webp'; img.alt = '';
    card.appendChild(img);
    var h = el('h2', null, 'Sign in to SuperLocalMemory'); h.id = 'slm-signin-title';
    card.appendChild(h);
    card.appendChild(el('p', null, 'This memory is shared by a team, so each person signs in with the ' +
      'account an admin made for them. Ask your admin if you do not have one.'));
    var u = el('input'); u.id = 'slm-signin-user'; u.placeholder = 'Username'; u.autocomplete = 'username';
    var p = el('input'); p.id = 'slm-signin-pass'; p.type = 'password'; p.placeholder = 'Password';
    p.autocomplete = 'current-password';
    var go = el('button', 'primary', 'Sign in'); go.type = 'submit'; go.id = 'slm-signin-go';
    var err = el('p', 'err'); err.setAttribute('role', 'alert'); err.id = 'slm-signin-err';
    card.appendChild(u); card.appendChild(p); card.appendChild(go); card.appendChild(err);
    var manage = el('button', 'link', 'I own this computer: manage users');
    manage.type = 'button'; manage.id = 'slm-signin-manage';
    manage.addEventListener('click', function () {
      wrap.remove();
      window.location.hash = '#operations-pane';
    });
    card.appendChild(manage);
    card.addEventListener('submit', function (e) {
      e.preventDefault();
      err.textContent = '';
      go.disabled = true;
      post('/api/rbac/login', { username: u.value.trim(), password: p.value }).then(function (r) {
        go.disabled = false;
        if (r.ok) { window.location.reload(); return; }
        err.textContent = (r.body && r.body.detail && (r.body.detail.message || r.body.detail)) ||
          'That username and password did not match.';
        if (typeof err.textContent !== 'string') err.textContent = 'Sign in failed.';
      }, function () { go.disabled = false; err.textContent = 'SuperLocalMemory did not answer. Try again.'; });
    });
    wrap.appendChild(card);
    document.body.appendChild(wrap);
    setTimeout(function () { u.focus(); }, 50);
  }

  function badge(who, tries) {
    style();
    if (document.getElementById('slm-whoami')) return;
    var host = document.querySelector('.topbar');
    // The shell builds the top bar on load too; wait for it briefly.
    if (!host) {
      if ((tries || 0) < 20) setTimeout(function () { badge(who, (tries || 0) + 1); }, 250);
      return;
    }
    var b = el('span'); b.id = 'slm-whoami';
    b.appendChild(el('span', null, 'Signed in as ' + (who.display_name || who.username) + ' · ' +
      (who.role || 'no role') + ' · ' + (who.profile || '')));
    var out = el('button', 'btn sm', 'Sign out'); out.type = 'button';
    out.addEventListener('click', function () {
      post('/api/rbac/logout', {}).then(function () { window.location.reload(); });
    });
    b.appendChild(out);
    var star = host.querySelector('.star-cta');
    if (star) host.insertBefore(b, star); else host.appendChild(b);
  }

  function check() {
    fetch('/api/rbac/whoami', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (who) {
        if (!who) return;
        if (who.kind === 'user') return badge(who);
        if (who.require_login) gate(who);
      })
      .catch(function () { /* the startup bar reports a daemon that does not answer */ });
  }

  window.SLMSignInGate = { check: check };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', check);
  else check();
}());
