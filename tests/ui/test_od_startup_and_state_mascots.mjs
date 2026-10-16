/**
 * The "please wait" bar while SLM starts, and mascots on loading/error text.
 * Runner: npm test (scripts/run-ui-tests.mjs). Requires jsdom.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

function health(body, ok = true) {
  return { ok, status: ok ? 200 : 503, json: body };
}

describe('startup bar', function () {
  it('shows while warming, naming what warms, and goes when ready', async function () {
    const h = buildHarness(['main'], health({ ready: false, readiness: { engine: true,
      recall_warmup: { warm: false, warming: ['indexes', 'entity_graph'] } } }));
    const w = h.window;
    evalModule(w, 'od-startup.js');
    await flushPromises(); await flushPromises();
    const bar = w.document.getElementById('slm-startup');
    assert.ok(bar, 'bar shown');
    assert.equal(bar.getAttribute('role'), 'status');
    assert.match(bar.textContent, /warming up/);
    assert.match(bar.textContent, /the search indexes, the knowledge graph/);
    assert.doesNotMatch(bar.textContent, /entity_graph/);
    assert.match(bar.querySelector('img').src, /mascot\/flying-320\.webp$/);
    w.__setFetchStub(health({ ready: true, readiness: { engine: true, recall_warmup: { warm: true } } }));
    w.SLMStartup.check();
    await flushPromises(); await flushPromises();
    assert.equal(w.document.getElementById('slm-startup'), null, 'bar removed once ready');
    w.close();
  });

  it('says it is reconnecting when the daemon does not answer', async function () {
    const h = buildHarness(['main'], health({}));
    const w = h.window;
    w.__setFetchReject(new Error('down'));
    evalModule(w, 'od-startup.js');
    await flushPromises(); await flushPromises();
    assert.match(w.document.getElementById('slm-startup').textContent, /Reconnecting/);
    w.close();  // stops the retry timer
  });

  it('shows nothing when already ready', async function () {
    const h = buildHarness(['main'], health({ ready: true, readiness: { engine: true, recall_warmup: { warm: true } } }));
    evalModule(h.window, 'od-startup.js');
    await flushPromises(); await flushPromises();
    assert.equal(h.window.document.getElementById('slm-startup'), null);
    h.window.close();
  });
});

describe('state mascots', function () {
  it('a loading message gets a moving mascot; an error a calm one; normal text none', async function () {
    const h = buildHarness(['p'], health({}));
    const w = h.window, d = w.document;
    d.getElementById('p').className = 'tab-pane';
    d.getElementById('p').innerHTML =
      '<div id="a">Loading timeline…</div><div id="b">Could not load the timeline.</div>' +
      '<div id="c">Twelve memories this week.</div>';
    evalModule(w, 'od-polish.js');
    await new Promise(r => setTimeout(r, 400));
    const a = d.querySelector('#a img.slm-state-mascot');
    const b = d.querySelector('#b img.slm-state-mascot');
    assert.ok(a && a.classList.contains('loading'));
    assert.match(a.src, /flying-320/);
    assert.ok(b && b.classList.contains('trouble'));
    assert.match(b.src, /peek-320/);
    assert.equal(d.querySelector('#c img'), null);
    assert.equal(a.getAttribute('alt'), '');
    w.close();
  });
});
