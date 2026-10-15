/**
 * Dashboard home tiles, the help layer, the theme choice, and the mascot assets.
 *  - every tile loads on its own: a failing endpoint says "Unavailable" with a
 *    Retry button and never blanks the other tiles
 *  - every pane's "Read the guide" link points at a file that exists in docs/
 *  - info buttons carry an accessible name and open on focus and click
 *  - the theme follows the system until the toggle is used, then persists
 *  - every mascot image the UI references exists and ships with the package
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const UI = join(here, '../../src/superlocalmemory/ui');
const DOCS = join(here, '../../docs');
const read = (rel) => readFileSync(join(UI, rel), 'utf8');

function dom(html, fetchImpl, extra = '') {
  const d = new JSDOM(`<!DOCTYPE html><html><body>${html}</body></html>`, { runScripts: 'outside-only', url: 'http://localhost:8765/' });
  d.window.fetch = fetchImpl;
  d.window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  d.window.matchMedia = d.window.matchMedia || (() => ({ matches: false, addEventListener() {} }));
  if (extra) d.window.eval(extra);
  return d;
}
const flush = () => new Promise((r) => setTimeout(r, 30));
const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });

const TILES = `
  <div id="kpi-docs"><div id="k-docs"></div><div id="k-docs-delta"></div></div>
  <div id="kpi-apps"><div id="k-apps"></div><div id="k-apps-delta"></div></div>
  <div id="kpi-ac"><div id="k-ac"></div><div id="k-ac-delta"></div></div>
  <div id="kpi-health"><div id="k-health"></div><div id="k-health-delta"></div></div>
  <div id="dash-health-body"></div><div id="dash-apps-body"></div>`;

describe('dashboard home tiles', () => {
  it('one failing endpoint shows Unavailable and a Retry, the others still fill in', async () => {
    const d = dom(TILES, (url) => {
      if (url.includes('/api/memory-health')) return Promise.reject(new Error('slow'));
      if (url.includes('/api/v3/answer-check')) return ok({ active: 'laya', laya: { state: 'ready' } });
      if (url.includes('/api/v3/features')) return ok({ media: { enabled: false } });
      if (url.includes('/api/v3/local-apps')) return ok({ apps: [{ name: 'Claude Code' }], confirmed_count: 1 });
      if (url.includes('/api/v3/connections/status')) return ok({ connections: [] });
      if (url.includes('/api/v3/mesh/peers')) return ok({ peers: [{ id: 'p1' }, { id: 'p2' }] });
      return ok({});
    }, read('js/od-dash-home.js'));
    d.window.SLMDashHome.reload();
    await flush();
    const doc = d.window.document;
    assert.match(doc.getElementById('k-health').textContent, /Unavailable/);
    assert.ok(doc.querySelector('#k-health-delta button.kpi-retry'), 'Retry button offered');
    assert.equal(doc.getElementById('k-apps').textContent, '1');
    assert.match(doc.getElementById('k-apps-delta').textContent, /2 peers/);
    assert.match(doc.getElementById('k-ac').textContent, /On this Mac/);
    assert.match(doc.getElementById('k-docs').textContent, /Off/);
    assert.doesNotMatch(doc.body.textContent, /^\s*—\s*$/m);
    d.window.close();
  });

  it('health tile names the state from the API, never a bare dash', async () => {
    const d = dom(TILES, (url) => url.includes('/api/memory-health')
      ? ok({ enrichment: { status: 'completed', errors: 0 }, repairs: [], suggestions: [], state: 'Up to date.', coverage: { facts: 5 } })
      : ok({ media: {}, apps: [], connections: [], peers: [], active: 'off', laya: { state: 'not_installed' } }),
    read('js/od-dash-home.js'));
    d.window.SLMDashHome.reload();
    await flush();
    assert.match(d.window.document.getElementById('k-health').textContent, /Up to date/);
    assert.match(d.window.document.getElementById('dash-health-body').textContent, /5 facts/);
    d.window.close();
  });
});

describe('help layer', () => {
  const src = read('js/od-help.js');
  const pairs = [...src.matchAll(/'([a-z-]+-pane)': \['[^']+', '([^']+)'\]/g)];

  it('maps every pane to a docs file that exists', () => {
    assert.ok(pairs.length >= 15, 'a guide link for each pane');
    for (const [, pane, file] of pairs) {
      assert.ok(existsSync(join(DOCS, file)), `${pane} -> docs/${file} exists`);
    }
  });

  it('adds a subtitle with a new-tab guide link and accessible info buttons', async () => {
    const d = dom(`<div id="memories-pane" class="tab-pane"><div class="page-head"><h2>Everything</h2></div>
      <div class="tabs"><button class="tab">Saved views</button></div></div>
      <div id="od-sidebar-foot"><span id="od-version">v</span></div>`, () => ok({}), src);
    await new Promise((r) => setTimeout(r, 250));
    const doc = d.window.document;
    const a = doc.querySelector('#memories-pane .pane-help a');
    assert.ok(a, 'guide link present');
    assert.equal(a.getAttribute('target'), '_blank');
    assert.match(a.getAttribute('rel'), /noopener/);
    assert.match(a.href, /^https:\/\/github\.com\/qualixar\/superlocalmemory\/blob\/main\/docs\/recall\.md$/);
    const tip = doc.querySelector('#memories-pane .info-tip');
    assert.ok(tip, 'info button next to Saved views');
    assert.match(tip.getAttribute('aria-label'), /saved so you can run it again/);
    tip.dispatchEvent(new d.window.FocusEvent('focusin', { bubbles: true }));
    assert.equal(doc.getElementById('slm-info-pop').hidden, false, 'opens on keyboard focus');
    tip.click();
    assert.equal(doc.getElementById('slm-info-pop').hidden, false, 'stays open on tap');
    d.window.close();
  });

  it('Help & docs panel links to the website, docs, README, setup guide and wiki', async () => {
    const d = dom('<div id="od-sidebar-foot"><span id="od-version">v</span></div>', () => ok({}), src);
    await new Promise((r) => setTimeout(r, 250));
    const doc = d.window.document;
    doc.getElementById('od-help-btn').click();
    const hrefs = [...doc.querySelectorAll('#slm-help-panel a')].map((x) => x.href);
    for (const want of ['https://www.superlocalmemory.com/', '/tree/main/docs', '/blob/main/README.md', '/blob/main/SETUP_FOR_AGENTS.md', '/wiki']) {
      assert.ok(hrefs.some((h) => h.endsWith(want) || h.includes(want)), `links to ${want}`);
    }
    for (const w of ['Save a memory', 'Ask a question', 'Add pictures', 'Connect an app', 'Fix Memory health', 'Erase data']) {
      assert.match(doc.getElementById('slm-help-panel').textContent, new RegExp(w));
    }
    assert.ok(existsSync(join(here, '../../SETUP_FOR_AGENTS.md')));
    d.window.close();
  });
});

describe('theme choice', () => {
  const shell = read('js/od-shell.js');
  it('follows the system until the toggle is used, then persists the choice', () => {
    const d = new JSDOM('<!DOCTYPE html><html><body><div class="topbar"><button data-theme-icon></button></div></body></html>', { runScripts: 'outside-only', url: 'http://localhost:8765/' });
    d.window.matchMedia = (q) => ({ matches: /light/.test(q), addEventListener() {} });
    d.window.fetch = () => ok({});
    d.window.eval(shell);
    const html = d.window.document.documentElement;
    assert.equal(html.getAttribute('data-theme'), 'light', 'system prefers light');
    assert.equal(d.window.localStorage.getItem('slm-theme'), null, 'nothing stored before a choice');
    d.window.toggleDarkMode();
    assert.equal(html.getAttribute('data-theme'), 'dark');
    assert.equal(d.window.localStorage.getItem('slm-theme'), 'dark');
    d.window.close();
  });
});

describe('palette and assets', () => {
  it('no teal or mint literals remain in the stylesheets', () => {
    for (const f of readdirSync(join(UI, 'css'))) {
      const css = read(`css/${f}`);
      assert.doesNotMatch(css, /#(10b981|14b8a6|06b6d4|2dd4bf|34d399|5eead4|00d4aa|38f9d7|43e97b)/i, f);
    }
  });
  it('every mascot image the UI references exists', () => {
    const refs = new Set();
    for (const f of readdirSync(join(UI, 'js')).filter((n) => n.endsWith('.js')).concat(['../index.html'])) {
      const t = f === '../index.html' ? read('index.html') : read(`js/${f}`);
      for (const m of t.matchAll(/assets\/mascot\/([a-z]+-\d+\.webp)/g)) refs.add(m[1]);
    }
    assert.ok(refs.size >= 4);
    for (const r of refs) assert.ok(existsSync(join(UI, 'assets/mascot', r)), r);
  });
});
