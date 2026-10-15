// od-team.js — Team & Access (RBAC / C3) card renderer.
// Renders into window.odRenderTeam(container). Mounted inside the Operations
// pane (#od-team-mount). Lets a non-technical admin manage who can use this
// workspace and at what role — entirely from the dashboard.
//
// Backend: /api/rbac/{whoami,status,login,logout,users,members,policy}
// Auth: same-origin loopback = machine owner (implicit root); a logged-in
// user carries an HttpOnly session cookie set by /api/rbac/login.
//
// CSP-safe: no inline handlers — every control is wired via addEventListener.
//
// Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar — AGPL-3.0

/* global window, document, fetch */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function getJSON(url) {
    return fetch(url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  function postJSON(url, body, method) {
    return fetch(url, {
      method: method || 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        return { ok: r.ok, status: r.status, data: d };
      });
    });
  }

  var ROLES = ['admin', 'member', 'viewer'];

  function roleOptions(selected) {
    return ROLES.map(function (r) {
      return '<option value="' + r + '"' + (r === selected ? ' selected' : '') + '>' +
        r.charAt(0).toUpperCase() + r.slice(1) + '</option>';
    }).join('');
  }

  // ─── Render ────────────────────────────────────────────────────────────────

  function odRenderTeam(container) {
    if (!container) return;
    container.innerHTML =
      '<div class="card-head"><h3>Team &amp; access</h3></div>' +
      '<div class="card-pad" id="od-team-body">' +
      '<p class="muted">Loading team &amp; access…</p></div>';

    Promise.all([
      getJSON('/api/rbac/whoami'),
      getJSON('/api/rbac/status'),
    ]).then(function (res) {
      var who = res[0] || { kind: 'owner', permissions: [], role: 'owner' };
      var status = res[1] || { rbac_active: false, require_login: false, user_count: 0 };
      var canManage = (who.permissions || []).indexOf('manage') !== -1;

      var extras = canManage
        ? Promise.all([getJSON('/api/rbac/users'), getJSON('/api/rbac/members')])
        : Promise.resolve([null, null]);

      extras.then(function (mgmt) {
        var users = (mgmt[0] && mgmt[0].users) || [];
        var members = (mgmt[1] && mgmt[1].members) || [];
        paint(container, who, status, canManage, users, members);
      });
    });
  }

  function identityLine(who) {
    if (who.kind === 'user') {
      return '<div class="row" style="justify-content:space-between;align-items:center">' +
        '<div>Signed in as <strong>' + esc(who.display_name || who.username) + '</strong>' +
        ' — role <strong>' + esc(who.role || 'none') + '</strong> on workspace <code>' +
        esc(who.profile) + '</code></div>' +
        '<button class="btn sm" id="od-team-logout">Sign out</button></div>';
    }
    return '<div>You are the <strong>owner of this computer</strong> with full control. ' +
      '<strong>You never need to sign in</strong> to use SuperLocalMemory here.</div>';
  }

  // Users and roles are for a shared workspace (a team on one server). Until a
  // user exists there is nothing to sign in with, so the form is not shown:
  // showing it made people think SLM needed a password. There is no default
  // account and never a default password: a known password would open every
  // install that is reachable from a network (CWE-1393).
  function noUsersNote() {
    return '<div class="card" style="margin-top:12px;max-width:640px"><div class="card-pad">' +
      '<p style="margin:0 0 6px"><strong>Signing in is optional.</strong></p>' +
      '<p class="muted" style="margin:0">It is only for a shared workspace where several people ' +
      'use one SuperLocalMemory with different roles. Add a user below and choose their password ' +
      'yourself; they sign in with it. SuperLocalMemory never ships a default account or password.</p>' +
      '</div></div>';
  }

  function loginForm() {
    return '<div class="card" style="margin-top:12px;max-width:520px">' +
      '<div class="card-head"><h4 style="margin:0">Sign in</h4></div>' +
      '<div class="card-pad">' +
      '<div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">' +
      '<input id="od-team-login-user" class="input sm" placeholder="Username" autocomplete="username">' +
      '<input id="od-team-login-pass" class="input sm" type="password" placeholder="Password" autocomplete="current-password">' +
      '<button class="btn sm primary" id="od-team-login-btn">Sign in</button>' +
      '</div><p class="muted" id="od-team-login-msg" style="margin:8px 0 0"></p>' +
      '</div></div>';
  }

  function usersTable(users, members) {
    var roleByUser = {};
    members.forEach(function (m) { roleByUser[m.user_id] = m.role; });
    if (!users.length) {
      return '<p class="muted">No users yet. Add your first admin below.</p>';
    }
    var rows = users.map(function (u) {
      var role = roleByUser[u.user_id] || '';
      return '<tr>' +
        '<td>' + esc(u.username) + '</td>' +
        '<td>' + esc(u.display_name || '') + '</td>' +
        '<td><select class="input sm od-team-role" data-uid="' + esc(u.user_id) + '">' +
        '<option value="">— none —</option>' + roleOptions(role) + '</select></td>' +
        '<td>' + esc(u.status) + '</td>' +
        '<td class="row" style="gap:6px;flex-wrap:wrap">' +
        '<button class="btn sm od-team-reset" data-uid="' + esc(u.user_id) +
        '" data-uname="' + esc(u.username) + '">Reset password</button>' +
        '<button class="btn sm od-team-status" data-uid="' + esc(u.user_id) +
        '" data-uname="' + esc(u.username) + '" data-next="' +
        (u.status === 'disabled' ? 'active' : 'disabled') + '">' +
        (u.status === 'disabled' ? 'Enable' : 'Disable') + '</button>' +
        '<button class="btn sm danger od-team-del" data-uid="' + esc(u.user_id) +
        '" data-uname="' + esc(u.username) + '">Remove</button></td>' +
        '</tr>';
    }).join('');
    return '<table class="tbl"><thead><tr>' +
      '<th>Username</th><th>Name</th><th>Role (this workspace)</th><th>Status</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function manageBlock(status, users, members) {
    return '<div class="card" style="margin-top:12px">' +
      '<div class="card-head"><h4 style="margin:0">Users &amp; roles</h4></div>' +
      '<div class="card-pad">' +
      '<div id="od-team-users">' + usersTable(users, members) + '</div>' +
      // Reset-password form, shown for one account at a time (UX-21).
      '<div id="od-team-reset" class="row" style="display:none;gap:8px;flex-wrap:wrap;' +
      'align-items:center;margin-top:10px">' +
      '<span id="od-team-reset-who"></span>' +
      '<input id="od-team-reset-pass" class="input sm" type="password" placeholder="New password (min 8)" autocomplete="new-password">' +
      '<input id="od-team-reset-again" class="input sm" type="password" placeholder="Repeat it" autocomplete="new-password">' +
      '<button class="btn sm primary" id="od-team-reset-save">Set password</button>' +
      '<button class="btn sm" id="od-team-reset-cancel">Cancel</button></div>' +
      '<div class="row" style="gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px">' +
      '<input id="od-team-new-user" class="input sm" placeholder="Username">' +
      '<input id="od-team-new-name" class="input sm" placeholder="Display name (optional)">' +
      '<input id="od-team-new-pass" class="input sm" type="password" placeholder="Password (min 8)">' +
      '<select id="od-team-new-role" class="input sm">' +
      // The first person added runs the workspace; later ones start as members.
      roleOptions(users.length ? 'member' : 'admin') + '</select>' +
      '<button class="btn sm primary" id="od-team-add-btn">Add user</button>' +
      '</div><p class="muted" id="od-team-msg" style="margin:8px 0 0"></p>' +
      '</div></div>' +
      // Policy
      '<div class="card" style="margin-top:12px;max-width:640px">' +
      '<div class="card-head"><h4 style="margin:0">Access policy</h4></div>' +
      '<div class="card-pad">' +
      '<label class="row" style="gap:8px;align-items:center;cursor:pointer">' +
      '<input type="checkbox" id="od-team-require-login"' +
      (status.require_login ? ' checked' : '') + '>' +
      '<span>Require every person to sign in before reading or writing memory ' +
      '(company mode). The machine owner can always manage users.</span></label>' +
      '<p class="muted" id="od-team-policy-msg" style="margin:8px 0 0"></p>' +
      '</div></div>';
  }

  function paint(container, who, status, canManage, users, members) {
    var body = container.querySelector('#od-team-body');
    if (!body) return;
    var html = identityLine(who);
    var anyUsers = (status && status.user_count > 0) || users.length > 0;
    if (who.kind !== 'user') html += anyUsers ? loginForm() : noUsersNote();
    if (canManage) html += manageBlock(status, users, members);
    else if (who.kind === 'user') {
      html += '<p class="muted" style="margin-top:12px">Your role does not include ' +
        'user administration. Ask a workspace admin to change roles.</p>';
    }
    body.innerHTML = html;
    wire(container, who);
  }

  // ─── Wiring (CSP-safe) ───────────────────────────────────────────────────────

  function wire(container, who) {
    var q = function (id) { return container.querySelector('#' + id); };
    var reload = function () { odRenderTeam(container); };

    var loginBtn = q('od-team-login-btn');
    if (loginBtn) {
      loginBtn.addEventListener('click', function () {
        var u = (q('od-team-login-user') || {}).value || '';
        var p = (q('od-team-login-pass') || {}).value || '';
        var msg = q('od-team-login-msg');
        postJSON('/api/rbac/login', { username: u, password: p }).then(function (r) {
          if (r.ok) { reload(); }
          else if (msg) { msg.textContent = (r.data && r.data.detail) || 'Sign in failed.'; }
        });
      });
    }

    var logoutBtn = q('od-team-logout');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', function () {
        postJSON('/api/rbac/logout', {}).then(reload);
      });
    }

    var addBtn = q('od-team-add-btn');
    if (addBtn) {
      addBtn.addEventListener('click', function () {
        var msg = q('od-team-msg');
        var body = {
          username: (q('od-team-new-user') || {}).value || '',
          password: (q('od-team-new-pass') || {}).value || '',
          display_name: (q('od-team-new-name') || {}).value || '',
          role: (q('od-team-new-role') || {}).value || 'member',
        };
        addBtn.disabled = true;
        postJSON('/api/rbac/users', body).then(function (r) {
          addBtn.disabled = false;
          if (r.ok) { reload(); }
          else if (msg) { msg.textContent = (r.data && r.data.detail) || 'Could not add user.'; }
        });
      });
    }

    var showError = function (r, fallback) {
      var msg = q('od-team-msg');
      if (msg) {
        msg.style.color = '#ff7b7b';
        msg.textContent = (r && r.data && (r.data.detail || r.data.error)) || fallback;
      }
    };

    // Role change dropdowns. A refused change keeps its message on screen and
    // puts the dropdown back (UX-20: the page used to reload over the error).
    container.querySelectorAll('.od-team-role').forEach(function (sel) {
      var before = sel.value;
      sel.addEventListener('change', function () {
        var uid = sel.getAttribute('data-uid');
        var role = sel.value;
        var self = who && who.kind === 'user' && who.user_id === uid;
        var apply = function () {
          var req = role
            ? postJSON('/api/rbac/members', { user_id: uid, role: role })
            : postJSON('/api/rbac/members', { user_id: uid }, 'DELETE');
          req.then(function (r) {
            if (r.ok) { reload(); return; }
            sel.value = before;
            showError(r, 'Role change failed.');
          });
        };
        if (!self) { apply(); return; }
        window.confirmDestructive({
          title: 'Change your own access',
          target: 'your role on this workspace',
          consequence: role === 'admin' ? 'You stay an administrator.'
            : 'You will no longer be able to manage users here.',
          confirmLabel: 'Change my role',
        }).then(function (confirmed) {
          if (confirmed) { apply(); } else { sel.value = before; }
        });
      });
    });

    // Reset password (UX-21): the control the sign-in screen points to.
    var resetBox = q('od-team-reset');
    var resetUid = '';
    container.querySelectorAll('.od-team-reset').forEach(function (btn) {
      btn.addEventListener('click', function () {
        resetUid = btn.getAttribute('data-uid');
        var label = q('od-team-reset-who');
        if (label) label.textContent = 'New password for ' + btn.getAttribute('data-uname') + ':';
        if (resetBox) resetBox.style.display = 'flex';
        var first = q('od-team-reset-pass');
        if (first) first.focus();
      });
    });
    var resetCancel = q('od-team-reset-cancel');
    if (resetCancel) {
      resetCancel.addEventListener('click', function () {
        resetUid = '';
        if (resetBox) resetBox.style.display = 'none';
      });
    }
    var resetSave = q('od-team-reset-save');
    if (resetSave) {
      resetSave.addEventListener('click', function () {
        var p1 = (q('od-team-reset-pass') || {}).value || '';
        var p2 = (q('od-team-reset-again') || {}).value || '';
        if (p1.length < 8) { showError(null, 'The password must be at least 8 characters.'); return; }
        if (p1 !== p2) { showError(null, 'The two passwords differ.'); return; }
        postJSON('/api/rbac/users/' + encodeURIComponent(resetUid), { password: p1 }, 'PATCH')
          .then(function (r) {
            if (!r.ok) { showError(r, 'Could not set the password.'); return; }
            var msg = q('od-team-msg');
            if (msg) {
              msg.style.color = '';
              msg.textContent = 'Password set. Their other sessions are signed out.';
            }
            if (resetBox) resetBox.style.display = 'none';
          });
      });
    }

    // Disable / enable an account.
    container.querySelectorAll('.od-team-status').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var next = btn.getAttribute('data-next');
        var act = function () {
          postJSON('/api/rbac/users/' + encodeURIComponent(btn.getAttribute('data-uid')),
                   { status: next }, 'PATCH').then(function (r) {
            if (r.ok) { reload(); } else { showError(r, 'Could not change the account.'); }
          });
        };
        if (next !== 'disabled') { act(); return; }
        window.confirmDestructive({
          title: 'Disable account',
          target: '"' + btn.getAttribute('data-uname') + '"',
          consequence: 'They are signed out and cannot sign in until enabled again.',
          confirmLabel: 'Disable',
        }).then(function (confirmed) { if (confirmed) act(); });
      });
    });

    // Remove user
    container.querySelectorAll('.od-team-del').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var uid = btn.getAttribute('data-uid');
        var uname = btn.getAttribute('data-uname');
        window.confirmDestructive({
          title: 'Remove user',
          target: '"' + uname + '"',
          consequence: 'This deletes their account and access.',
          confirmLabel: 'Remove',
        }).then(function(confirmed) {
          if (!confirmed) return;
          postJSON('/api/rbac/users/' + encodeURIComponent(uid), {}, 'DELETE').then(function (r) {
            if (r.ok) { reload(); } else { showError(r, 'Could not remove the account.'); }
          });
        });
      });
    });

    // Require-login policy toggle
    var reqLogin = q('od-team-require-login');
    if (reqLogin) {
      reqLogin.addEventListener('change', function () {
        var msg = q('od-team-policy-msg');
        postJSON('/api/rbac/policy', { require_login: reqLogin.checked }).then(function (r) {
          if (msg) {
            msg.textContent = r.ok
              ? (reqLogin.checked ? 'Company mode on — everyone must sign in.'
                                  : 'Company mode off — single-operator use.')
              : ((r.data && r.data.detail) || 'Could not update policy.');
          }
        });
      });
    }
  }

  window.odRenderTeam = odRenderTeam;
}());
