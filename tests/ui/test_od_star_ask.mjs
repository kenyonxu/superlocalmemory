/**
 * The GitHub star ask (js/od-star-ask.js, window.odStarAsk): a top-bar link plus a small
 * dismissible card on the Dashboard pane. Dismissing or clicking star is remembered in
 * localStorage; blocked storage must not break the card; the markup must satisfy the CSP.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { makeEnv } from './features_helpers.mjs';
import { evalModule } from './harness.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const ui = join(here, '../../src/superlocalmemory/ui');
const html = readFileSync(join(ui, 'index.html'), 'utf8');
const REPO = 'https://github.com/qualixar/superlocalmemory';
const KEY = 'slm.star.dismissed';

function setup({ throwingStorage = false, dismissed = false } = {}) {
    const h = makeEnv([], { ids: ['pane'] });
    h.store = {};
    if (dismissed) h.store[KEY] = '1';
    const storage = throwingStorage
        ? { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } }
        : { getItem: (k) => (k in h.store ? h.store[k] : null), setItem: (k, v) => { h.store[k] = String(v); } };
    Object.defineProperty(h.window, 'localStorage', { value: storage, configurable: true });
    evalModule(h.window, 'od-star-ask.js');
    h.host = h.document.getElementById('pane');
    h.btn = (t) => [...h.host.querySelectorAll('a,button')].find((b) => b.textContent.includes(t));
    return h;
}

describe('Star ask card', () => {
    it('shows the question, a star link opening GitHub safely, and Dismiss', () => {
        const h = setup();
        h.window.odStarAsk.mount(h.host);
        assert.match(h.host.textContent, /Is SuperLocalMemory useful to you\? A GitHub star helps other developers find it\./);
        const star = h.btn('Star on GitHub');
        assert.equal(star.getAttribute('href'), REPO);
        assert.equal(star.getAttribute('target'), '_blank');
        assert.match(star.getAttribute('rel'), /noopener/);
        assert.ok(h.btn('Dismiss'));
    });

    it('Dismiss removes the card and is remembered', () => {
        const h = setup();
        h.window.odStarAsk.mount(h.host);
        h.btn('Dismiss').click();
        assert.equal(h.host.children.length, 0);
        assert.equal(h.store[KEY], '1');
        h.window.odStarAsk.mount(h.host);
        assert.equal(h.host.children.length, 0, 'does not come back');
    });

    it('clicking the star button also removes the card for good', () => {
        const h = setup();
        h.window.odStarAsk.mount(h.host);
        h.btn('Star on GitHub').click();
        assert.equal(h.host.children.length, 0);
        assert.equal(h.store[KEY], '1');
    });

    it('stays hidden when it was dismissed in an earlier visit', () => {
        const h = setup({ dismissed: true });
        h.window.odStarAsk.mount(h.host);
        assert.equal(h.host.children.length, 0);
    });

    it('blocked storage never throws; Dismiss still closes it for this visit', () => {
        const h = setup({ throwingStorage: true });
        h.window.odStarAsk.mount(h.host);
        assert.ok(h.btn('Dismiss'));
        assert.doesNotThrow(() => h.btn('Dismiss').click());
        assert.equal(h.host.children.length, 0);
        h.window.odStarAsk.mount(h.host);
        assert.equal(h.host.children.length, 0);
    });
});

describe('Star ask markup', () => {
    it('has a persistent top-bar link that opens the repo in a new tab with rel noopener', () => {
        const link = html.match(/<a[^>]*id="star-cta"[^>]*>/);
        assert.ok(link, 'top-bar star link exists');
        assert.ok(link[0].includes('href="' + REPO + '"'));
        assert.ok(link[0].includes('target="_blank"'));
        assert.match(link[0], /rel="[^"]*noopener/);
        const header = html.slice(html.indexOf('<header class="topbar">'), html.indexOf('</header>'));
        assert.ok(header.includes('id="star-cta"'), 'link lives in the top bar');
    });

    it('adds no inline script, handler or style attribute (the CSP forbids them)', () => {
        const link = html.match(/<a[^>]*id="star-cta"[^>]*>/)[0];
        assert.doesNotMatch(link, /\son\w+=|\sstyle=/);
        const jsSrc = readFileSync(join(ui, 'js/od-star-ask.js'), 'utf8');
        assert.doesNotMatch(jsSrc, /innerHTML|\.style\.|setAttribute\('style'/);
        assert.ok(html.includes('id="od-star-ask"'));
        assert.ok(html.includes('static/js/od-star-ask.js?v='));
        assert.ok(html.includes('static/css/od-star-ask.css?v='));
    });
});
