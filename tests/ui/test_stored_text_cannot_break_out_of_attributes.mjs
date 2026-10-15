/**
 * Stored text must not break out of an HTML attribute.
 *
 * PR 251 review, finding 2: the Entity Explorer put an entity name (stored
 * memory text, written by any agent or ingested from mail and transcripts)
 * into aria-label through an escaper that handled & < > but not quotes. A name
 * such as `Acme" onfocus="..." autofocus x="` added its own event handler,
 * which runs with the dashboard's credentials.
 *
 * Two guards:
 *   1. Every escaper defined in ui/js is quote-safe (checked by running it).
 *   2. The Entity Explorer and the memory drawer render hostile names as
 *      plain attribute values: no extra attributes, the text round-trips.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { JSDOM } from 'jsdom';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const JS = join(__dirname, '../../src/superlocalmemory/ui/js');

const HOSTILE = [
    'Acme" onfocus="window.__pwned=1" autofocus data-x="',
    "O'Brien' onmouseover='window.__pwned=1' x='",
    '<img src=x onerror="window.__pwned=1">',
    '`backtick` ${window.__pwned=1}',
    'javascript:window.__pwned=1',
];

const ESCAPER = /function\s+(_?esc[A-Za-z]*|_?escape[A-Za-z]*)\s*\(([^)]*)\)\s*\{/g;

function extractFunctions(source) {
    const found = [];
    for (const match of source.matchAll(ESCAPER)) {
        let depth = 0;
        let end = match.index + match[0].length - 1;
        for (; end < source.length; end++) {
            if (source[end] === '{') depth++;
            if (source[end] === '}' && --depth === 0) break;
        }
        found.push({ name: match[1], code: source.slice(match.index, end + 1) });
    }
    return found;
}

describe('every escaper in the dashboard is attribute-safe', () => {
    const dom = new JSDOM('<!DOCTYPE html><body></body>', { runScripts: 'outside-only' });
    const files = readdirSync(JS).filter(name => name.endsWith('.js'));
    for (const file of files) {
        for (const fn of extractFunctions(readFileSync(join(JS, file), 'utf8'))) {
            it(`${file}: ${fn.name} escapes quotes and angle brackets`, () => {
                const run = dom.window.eval(`(${fn.code})`);
                for (const value of HOSTILE) {
                    const out = String(run(value));
                    assert.doesNotMatch(out, /["'<>]/, `${fn.name}(${JSON.stringify(value)}) = ${out}`);
                }
            });
        }
    }
});

function entityResponse(names) {
    return {
        ok: true, status: 200,
        json: () => Promise.resolve({
            entities: names.map(name => ({ name, type: 'person', fact_count: 1 })),
            total: names.length, limit: 50, offset: 0,
        }),
    };
}

describe('Entity Explorer renders hostile names as inert text', () => {
    it('adds no attributes and keeps the exact name in aria-label and data-ename', async () => {
        const h = buildHarness(['root'], { ok: true, status: 200, json: {} });
        try {
            h.window.fetch = () => Promise.resolve(entityResponse(HOSTILE));
            evalModule(h.window, 'od-entities.js');
            h.window.odRenderEntities(h.document.getElementById('root'));
            await flushPromises();
            await flushPromises();
            const rows = Array.from(h.document.querySelectorAll('[data-od-act="select-entity"]'));
            assert.equal(rows.length, HOSTILE.length);
            rows.forEach((row, i) => {
                const names = row.getAttributeNames().sort();
                assert.deepEqual(names, ['aria-label', 'aria-pressed', 'class', 'data-ename',
                                         'data-od-act', 'type']);
                assert.equal(row.getAttribute('aria-label'), 'Show entity ' + HOSTILE[i]);
                assert.equal(row.dataset.ename, HOSTILE[i]);
                assert.equal(row.querySelector('img'), null);
            });
            assert.equal(h.window.__pwned, undefined);
        } finally { h.dom.window.close(); }
    });
});

describe('core escapeHtml', () => {
    it('escapes all five HTML-significant characters', () => {
        const h = buildHarness([], { ok: true, status: 200, json: {} });
        try {
            assert.equal(h.window.escapeHtml(`&<>"'`), '&amp;&lt;&gt;&quot;&#39;');
        } finally { h.dom.window.close(); }
    });
});
