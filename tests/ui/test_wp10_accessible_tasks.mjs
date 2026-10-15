import { it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

it('UX-32: decay legend uses readable text independently of curve colors', () => {
  const h = buildHarness([], { ok: true, status: 200, json: {} });
  evalModule(h.window, 'od-decay-preview.js');
  const preview = h.window.odDecayPreview.build();
  assert.ok([...preview.querySelectorAll('span')].every(el => el.style.color === 'var(--fg-2)'));
  h.dom.window.close();
});

it('UX-32: settings check failures use the contrast-safe danger text token', () => {
  const source = readFileSync(new URL('../../src/superlocalmemory/ui/js/answer-check.js', import.meta.url), 'utf8');
  assert.match(source, /bad: 'var\(--danger-text\)'/);
});

it('UX-04: every settings field and switch has an accessible name', async () => {
  const h = buildHarness(['settings-pane'], { ok: true, status: 200, json: {} });
  h.window.fetch = async url => reply(url === '/internal/token' ? { token: 'fixture' } : {});
  evalModule(h.window, 'od-settings.js');
  h.window.odRenderSettings(h.document.getElementById('settings-pane'));
  await flushPromises();
  const unnamed = [...h.document.querySelectorAll('#od-settings-hub input, #od-settings-hub select, #od-settings-hub button')]
    .filter(el => !el.textContent.trim() && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby') && !el.labels?.length);
  assert.deepEqual(unnamed.map(el => el.id), []);
  h.dom.window.close();
});

it('UX-15: local embedding can be selected and tested without a hosted endpoint', async () => {
  const h = buildHarness(['settings-pane'], { ok: true, status: 200, json: {} });
  const sent = [];
  h.window.fetch = async (url, init = {}) => {
    if (init.method === 'POST') sent.push({ url, body: JSON.parse(init.body) });
    return reply(url === '/internal/token' ? { token: 'fixture' } :
      url === '/api/v3/embedding/config' ? { model_name: 'nomic-embed-text', dimension: 768 } : { success: true });
  };
  evalModule(h.window, 'od-settings.js');
  h.window.odRenderSettings(h.document.getElementById('settings-pane'));
  await flushPromises();
  assert.equal(h.document.getElementById('od-s-emb-model').disabled, false);
  h.document.getElementById('od-s-emb-test').click();
  await flushPromises();
  assert.equal(sent.at(-1)?.url, '/api/dashboard/embedding-test');
  assert.doesNotMatch(h.document.getElementById('od-s-emb-st').textContent, /Enter endpoint/);
  h.dom.window.close();
});

it('UX-36: profile dialog contains Tab focus and restores the opener on Escape', async () => {
  const h = buildHarness(['opener'], { ok: true, status: 200, json: {} });
  const opener = h.document.createElement('button');
  h.document.getElementById('opener').appendChild(opener);
  opener.focus();
  evalModule(h.window, 'profiles.js');
  h.window.openCreateProfileModal();
  await new Promise(resolve => setTimeout(resolve, 40));
  const overlay = h.document.getElementById('od-create-profile-overlay');
  const controls = [...overlay.querySelectorAll('input,button')];
  controls.at(-1).focus();
  controls.at(-1).dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
  assert.equal(h.document.activeElement, controls[0]);
  controls[0].dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
  assert.equal(h.document.activeElement, controls.at(-1));
  h.document.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.equal(h.document.activeElement, opener);
  h.dom.window.close();
});

it('UX-10/ENG-21: a reported permanent failure overrides installed readiness', () => {
  const h = buildHarness([], { ok: true, status: 200, json: {} });
  evalModule(h.window, 'dashboard.js');
  const text = h.window.answerCheckSummaryText({ active: 'laya', mode: 'auto',
    laya: { state: 'failed', error: 'model could not load' } });
  assert.doesNotMatch(text, /ready/);
  assert.match(text, /failed|attention|unavailable/i);
  h.dom.window.close();
});

it('UX-10: installation readiness alone never claims the live judge is ready', () => {
  const h = buildHarness([], {ok:true,status:200,json:{}});
  evalModule(h.window, 'dashboard.js');
  const text = h.window.answerCheckSummaryText({active:'laya',mode:'auto',laya:{state:'ready'}});
  assert.doesNotMatch(text, /ready/);
  assert.match(text, /configured|installed/);
  h.dom.window.close();
});

it('UX-30/UI-4: summaries offer weeks and a labeled date range', () => {
  const h = buildHarness(['summary'], { ok: true, status: 200, json: {} });
  evalModule(h.window, 'od-summaries.js');
  h.document.getElementById('summary').innerHTML = h.window.ODSummaries.controls('summary');
  assert.match(h.document.body.textContent, /This week/);
  assert.match(h.document.body.textContent, /Last week/);
  assert.equal(h.document.querySelectorAll('input[type=date]').length, 2);
  assert.ok([...h.document.querySelectorAll('input[type=date]')].every(el => el.labels.length || el.getAttribute('aria-label')));
  h.dom.window.close();
});

it('UX-07: Recall Lab shows check failure and explains stored confidence', async () => {
  const h = buildHarness(['recall-lab-search', 'recall-lab-query', 'recall-lab-limit', 'recall-lab-results', 'recall-lab-meta'],
    { ok: true, status: 200, json: { result_count: 1, results: [{ content: 'unrelated', score: 0.5, confidence: 0.9 }],
      answer_check: { status: 'unavailable', answer_confidence: null }, answerability: 'unjudged' } });
  h.window.fetch = async () => reply({ result_count:1, results:[{ content:'unrelated', score:0.5, confidence:0.9 }], answer_check:{ status:'unavailable', answer_confidence:null }, answerability:'unjudged' });
  evalModule(h.window, 'od-answercheck-tryit.js');
  evalModule(h.window, 'recall-lab.js');
  h.document.getElementById('recall-lab-query').value = 'unknown question';
  h.document.getElementById('recall-lab-search').click();
  await flushPromises();
  assert.match(h.document.body.textContent, /Not checked.*unavailable/);
  assert.match(h.document.body.textContent, /Stored fact confidence/);
  assert.equal(h.document.querySelector('.bg-outline-info').style.color, 'var(--cyan-text)');
  h.dom.window.close();
});

it('UX-05: phone settings have a stacking and shrinking rule', () => {
  const css = readFileSync('src/superlocalmemory/ui/css/design-system.css', 'utf8');
  assert.match(css, /@media[^}]*640px[\s\S]*#od-settings-hub[^}]*flex-direction:\s*column/);
});

it('UX-32: the dim token remains readable on populated card backgrounds in both themes', () => {
  const css = readFileSync('src/superlocalmemory/ui/css/design-system.css', 'utf8');
  const tokens = [...css.matchAll(/--fg-3:\s*(#[0-9a-f]{6})/gi)].map(m => m[1]);
  const rgb = hex => [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)/255);
  const luminance = hex => rgb(hex).map(x => x <= 0.04045 ? x/12.92 : ((x+0.055)/1.055)**2.4)
    .reduce((sum,x,i) => sum + x*[0.2126,0.7152,0.0722][i],0);
  const ratio = (fg,bg) => { const a=luminance(fg),b=luminance(bg); return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05); };
  assert.equal(tokens.length,2);
  assert.ok(ratio(tokens[0],'#f6f8fa') >= 4.5);
  assert.ok(ratio(tokens[1],'#21262d') >= 4.5);
});

it('UX-43: the executable replacement example includes a rollback directory', () => {
  const docs = readFileSync('docs/cli-reference.md', 'utf8');
  const commands = docs.split('\n').filter(line => line.startsWith('slm evidence import') && line.includes('--replace'));
  assert.ok(commands.length);
  assert.ok(commands.every(line => line.includes('--rollback-dir')));
});
