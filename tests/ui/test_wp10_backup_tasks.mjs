import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

const id = '0123456789abcdef';

async function mounted() {
  const h = buildHarness(['root'], {ok:true,status:200,json:{}});
  const calls = [];
  h.window.fetch = async (url, init = {}) => {
    calls.push({url,init});
    let body = {};
    if (url === '/internal/token') body = {token:'fixture'};
    if (String(url).endsWith('/list')) body = {backups:[{recovery_id:id,created:'2026-10-11',size_mb:1,status:'Verify before restore'}]};
    if (String(url).endsWith('/destinations')) body = {destinations:[]};
    if (String(url).endsWith('/verify')) body = {id,created:'2026-10-11',verified:true,stores:['memory.db','learning.db'],omissions:['credentials']};
    if (String(url).endsWith('/restore')) body = {success:true,message:'Stopping, restoring and restarting.'};
    return {ok:true,status:200,json:async()=>body};
  };
  evalModule(h.window, 'od-backup-recovery.js');
  evalModule(h.window, 'od-backup.js');
  h.window.odRenderBackup(h.document.getElementById('root'));
  await flushPromises(); await flushPromises();
  return {...h,calls};
}

it('UX-44: S3 explains unavailability without opening an absent OAuth route', async () => {
  const h = await mounted();
  const row = [...h.document.querySelectorAll('.bk-conn')].find(r => /S3/.test(r.textContent));
  assert.ok(row);
  assert.match(row.textContent, /not available in this release/);
  const connect = row.querySelector('button');
  assert.equal(connect.disabled, true);
  h.dom.window.close();
});

it('UX-17: verify previews stores, then requires RESTORE before requesting recovery', async () => {
  const h = await mounted();
  const button = [...h.document.querySelectorAll('button')].find(b => /Verify \/ restore/.test(b.textContent));
  assert.ok(button);
  button.click(); await flushPromises();
  assert.match(h.document.body.textContent, /memory.db, learning.db/);
  assert.match(h.document.body.textContent, /Excludes: credentials/);
  const restore = [...h.document.querySelectorAll('button')].find(b => /Restore this backup/.test(b.textContent));
  assert.equal(restore.disabled, true);
  const confirmation = h.document.querySelector('input[aria-label="Type RESTORE to confirm"]');
  confirmation.value = 'RESTORE'; confirmation.dispatchEvent(new h.window.Event('input'));
  restore.click(); await flushPromises();
  const call = h.calls.find(r => String(r.url).endsWith('/restore'));
  assert.equal(call.init.headers['X-Install-Token'], 'fixture');
  assert.deepEqual(JSON.parse(call.init.body), {id,confirm:'RESTORE'});
  assert.match(h.document.body.textContent, /Stopping, restoring and restarting/);
  h.dom.window.close();
});

it('UX-35: dashboard exports the recovery unit and advertises the new format', async () => {
  const h = await mounted();
  assert.ok([...h.document.querySelectorAll('button')].some(b => b.textContent === 'Export recovery archive'));
  const backup = [...h.document.querySelectorAll('button')].find(b => /Back up now/.test(b.textContent));
  backup.click(); await flushPromises();
  assert.ok(h.calls.some(r => r.url === '/api/backup/recovery/create'));
  h.dom.window.close();
});

it('finding 7: a refused export shows the server reason and downloads nothing', async () => {
  const h = await mounted();
  const realFetch = h.window.fetch;
  h.window.fetch = async (url, init = {}) => {
    if (String(url).endsWith('/recovery/export')) {
      return {ok:false,status:409,headers:{get:()=>''},
              text:async()=>JSON.stringify({detail:'Backup verification failed. Nothing was restored; create a new recovery backup.'}),
              blob:async()=>{ throw new Error('no blob for an error'); }};
    }
    return realFetch(url, init);
  };
  const toasts = [];
  h.window.showToast = (msg) => toasts.push(msg);
  let downloads = 0;
  h.window.URL.createObjectURL = () => { downloads += 1; return 'blob:x'; };
  const button = [...h.document.querySelectorAll('button')].find(b => b.textContent === 'Export recovery archive');
  button.click();
  await flushPromises(); await flushPromises();
  assert.equal(downloads, 0);
  assert.ok(toasts.some(t => /verification failed/i.test(t)), JSON.stringify(toasts));
  assert.equal(button.disabled, false);
  h.dom.window.close();
});
