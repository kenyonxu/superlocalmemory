import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

async function mount(check) {
  const h = buildHarness(['dashboard-pane'], { ok:true, status:200, json:{} });
  const sent = [];
  h.window.fetch = async (url, init = {}) => {
    sent.push({url,init});
    return { ok:true, status:200, json:async () => String(url).includes('/recall/trace') ?
      {result_count:1, results:[{fact_id:'f1',content:'The release is Friday.',source:'meeting'}], answer_check:check } :
      {activities:[{timestamp:'2026-10-11T10:00:00Z',action_type:'merge',reason:'combined duplicate notes',existing_fact_id:'f1'}]} };
  };
  evalModule(h.window, 'od-answercheck-tryit.js');
  evalModule(h.window, 'od-summaries.js');
  evalModule(h.window, 'od-daily.js');
  h.window.SLMDaily.mount(h.document.getElementById('dashboard-pane'));
  return {...h,sent};
}

it('UI-3: home search calls checked recall and shows sourced answer and verdict', async () => {
  const h = await mount({status:'judged',abstained:false,answer_confidence:0.9,threshold:0.5});
  h.document.getElementById('daily-question').value = 'When is the release?';
  h.document.querySelector('#daily-question-form').dispatchEvent(new h.window.Event('submit', {cancelable:true}));
  await flushPromises();
  assert.match(h.document.body.textContent, /The release is Friday/);
  assert.match(h.document.body.textContent, /Answered/);
  assert.match(h.document.body.textContent, /f1/);
  const request = h.sent.find(r => String(r.url).includes('/recall/trace'));
  assert.equal(request.init.method, 'POST');
  assert.equal(JSON.parse(request.init.body).query, 'When is the release?');
  h.dom.window.close();
});

it('UI-3: refusal suppresses the answer while keeping sources inspectable', async () => {
  const h = await mount({status:'judged',abstained:true,answer_confidence:0.1});
  h.document.getElementById('daily-question').value = 'Unknown';
  h.document.querySelector('#daily-question-form').dispatchEvent(new h.window.Event('submit', {cancelable:true}));
  await flushPromises();
  assert.match(h.document.body.textContent, /I don’t have that/);
  assert.equal(h.document.getElementById('daily-answer').textContent, '');
  h.dom.window.close();
});

it('LOCAL: background activity names the recorded change and time', async () => {
  const h = await mount({status:'off'});
  h.document.getElementById('daily-work-refresh').click();
  await flushPromises();
  assert.match(h.document.body.textContent, /combined duplicate notes/);
  assert.match(h.document.body.textContent, /2026-10-11/);
  assert.match(h.document.body.textContent, /f1/);
  h.dom.window.close();
});
