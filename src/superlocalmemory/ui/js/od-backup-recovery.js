// Verified backup preview and offline recovery, using the dashboard credential.
(function () {
  'use strict';
  function request(auth, path, body) {
    return auth('/api/backup/recovery/' + path, 'POST', body).then(function (r) {
      return r.json().then(function (d) { if (!r.ok || d.error) throw new Error(d.detail || d.error || 'Recovery request failed.'); return d; });
    });
  }
  function button(label) { var b = document.createElement('button'); b.textContent = label; b.type = 'button'; b.className = 'btn sm'; return b; }
  function actions(cell, backup, auth) {
    if (!backup.recovery_id) {
      var legacy = button('Verify database'); cell.appendChild(legacy);
      legacy.addEventListener('click', function () {
        request(auth, 'verify-legacy', {filename:backup.filename}).then(function (d) {
          var note = document.createElement('p'); note.setAttribute('role','status'); note.textContent=d.message; cell.appendChild(note);
        }).catch(function (e) { cell.appendChild(document.createTextNode(e.message)); });
      });
      return;
    }
    var verify = button('Verify / restore'); cell.appendChild(verify);
    verify.addEventListener('click', function () {
      verify.disabled = true;
      request(auth, 'verify', {id:backup.recovery_id}).then(function (data) {
        preview(cell, data, auth);
      }).catch(function (e) {
        var msg = document.createElement('p'); msg.setAttribute('role', 'status'); msg.textContent = e.message; cell.appendChild(msg);
      }).finally(function () { verify.disabled = false; });
    });
  }
  function preview(cell, data, auth) {
    var old = cell.querySelector('.recovery-preview'); if (old) old.remove();
    var box = document.createElement('div'); box.className = 'recovery-preview';
    var info = document.createElement('p');
    info.textContent = 'Verified backup from ' + data.created + '. Includes: ' + data.stores.join(', ') +
      '. Excludes: ' + data.omissions.join(', ') + '. Accounts, sessions and the sign-in setting stay as they are now. The daemon will stop and restart. Changes made since this backup may be lost. A safety copy is kept.';
    var label = document.createElement('label'); label.textContent = 'Type RESTORE to confirm: ';
    var input = document.createElement('input'); input.type = 'text'; input.setAttribute('aria-label', 'Type RESTORE to confirm'); label.appendChild(input);
    var restore = button('Restore this backup'); restore.disabled = true;
    input.addEventListener('input', function () { restore.disabled = input.value !== 'RESTORE'; });
    var status = document.createElement('p'); status.setAttribute('role', 'status');
    restore.addEventListener('click', function () {
      restore.disabled = true;
      request(auth, 'restore', {id:data.id, confirm:input.value}).then(function (d) {
        status.textContent = d.message;
      }).catch(function (e) { status.textContent = e.message; restore.disabled = false; });
    });
    var cancel = button('Cancel'); cancel.addEventListener('click', function () { box.remove(); });
    box.append(info, label, restore, cancel, status); cell.appendChild(box); input.focus();
  }
  function outcome(root) {
    fetch('/api/backup/recovery/status', {credentials:'same-origin'}).then(function (r) {
      if (!r.ok) return null; return r.json();
    }).then(function (d) {
      if (!d || d.state === 'idle') return;
      var p = document.createElement('p'); p.setAttribute('role', 'status');
      p.textContent = 'Last recovery: ' + d.state + '. ' + (d.message || 'Reopen this page after the daemon restarts.'); root.prepend(p);
    }).catch(function () {});
  }
  window.SLMBackupRecovery = {actions:actions, outcome:outcome};
}());
