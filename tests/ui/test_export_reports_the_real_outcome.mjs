/**
 * tests/ui/test_export_reports_the_real_outcome.mjs
 * Runner: npm test   (scripts/run-ui-tests.mjs)
 *
 * 4.1.25 audit G-C2: "Export my data" set an anchor's href and printed
 * "Export started - check your downloads." without looking at the daemon's
 * answer. The daemon answered an error, the person received nothing, and the
 * page said it had worked. The page must now fetch the file and say what
 * actually happened: the saved file's name and size, or the daemon's error.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

const EXPORT_URL = '/api/compliance/gdpr/export';

function exportResponse({ status = 200, body = 'x'.repeat(2048), name = 'slm-export-default.json',
                          records = '42', json = null } = {}) {
    const headers = new Map();
    if (status === 200) {
        headers.set('content-disposition', `attachment; filename="${name}"`);
        if (records !== null) headers.set('x-slm-records', records);
    }
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: (k) => headers.get(String(k).toLowerCase()) ?? null },
        json: async () => { if (json === null) throw new Error('not json'); return json; },
        blob: async () => ({ size: body.length }),
    };
}

function setup(exportAnswer) {
    const calls = [];
    const h = buildHarness(['pane'], function (url) {
        const path = String(url);
        calls.push(path);
        if (path.startsWith(EXPORT_URL) || path.startsWith('/api/export')) {
            return typeof exportAnswer === 'function' ? exportAnswer(path) : exportAnswer;
        }
        return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    });
    const saved = [];
    h.window.URL.createObjectURL = () => 'blob:test';
    h.window.URL.revokeObjectURL = () => {};
    h.window.HTMLAnchorElement.prototype.click = function () { saved.push(this.download); };
    return { ...h, calls, saved };
}

async function renderedGdprControls(exportAnswer) {
    const ctx = setup(exportAnswer);
    evalModule(ctx.window, 'od-operations.js');
    ctx.window.odRenderOperations(ctx.document.getElementById('pane'));
    await flushPromises();
    await flushPromises();
    return ctx;
}

describe('slmDownloadExport', function () {
    it('saves the file and reports its name, size and record count', async function () {
        const ctx = setup(Promise.resolve(exportResponse({ body: 'y'.repeat(3000), records: '7' })));
        const result = await ctx.window.slmDownloadExport(EXPORT_URL);
        assert.equal(result.ok, true);
        assert.equal(result.filename, 'slm-export-default.json');
        assert.equal(result.size, 3000);
        assert.equal(result.records, 7);
        assert.deepEqual(ctx.saved, ['slm-export-default.json']);
        assert.match(ctx.window.slmExportMessage(result), /slm-export-default\.json.*7 records.*2\.9 KB/);
    });

    it('reports the daemon error and saves nothing when the export fails', async function () {
        const ctx = setup(Promise.resolve(exportResponse({
            status: 500, json: { detail: 'Export failed (TypeError). The daemon log has the details.' },
        })));
        const result = await ctx.window.slmDownloadExport(EXPORT_URL);
        assert.equal(result.ok, false);
        assert.equal(result.status, 500);
        assert.deepEqual(ctx.saved, []);
        assert.match(ctx.window.slmExportMessage(result), /^Export failed: Export failed \(TypeError\)/);
    });

    it('names the status when the daemon answers without a reason', async function () {
        const ctx = setup(Promise.resolve(exportResponse({ status: 403 })));
        const result = await ctx.window.slmDownloadExport(EXPORT_URL);
        assert.equal(result.ok, false);
        assert.match(ctx.window.slmExportMessage(result), /HTTP 403/);
    });

    it('reports an unreachable daemon', async function () {
        const ctx = setup(Promise.reject(new Error('NetworkError')));
        const result = await ctx.window.slmDownloadExport(EXPORT_URL);
        assert.equal(result.ok, false);
        assert.match(ctx.window.slmExportMessage(result), /daemon did not answer/);
        assert.deepEqual(ctx.saved, []);
    });
});

describe('Export my data button', function () {
    it('shows the saved file instead of "Export started"', async function () {
        const ctx = await renderedGdprControls(Promise.resolve(exportResponse({ records: '12' })));
        const button = ctx.document.getElementById('od-gdpr-export-btn');
        assert.ok(button, 'the export button is rendered');
        button.click();
        await flushPromises();
        await flushPromises();
        const summary = ctx.document.getElementById('od-gdpr-summary').textContent;
        assert.doesNotMatch(summary, /Export started/);
        assert.match(summary, /Export saved as slm-export-default\.json \(12 records, 2\.0 KB\)/);
        assert.deepEqual(ctx.saved, ['slm-export-default.json']);
        assert.equal(button.disabled, false);
    });

    it('shows the error when the export fails, and does not claim success', async function () {
        const ctx = await renderedGdprControls(Promise.resolve(exportResponse({
            status: 500, json: { detail: 'Export failed (TypeError). The daemon log has the details.' },
        })));
        ctx.document.getElementById('od-gdpr-export-btn').click();
        await flushPromises();
        await flushPromises();
        const summary = ctx.document.getElementById('od-gdpr-summary').textContent;
        assert.match(summary, /^Export failed: Export failed \(TypeError\)/);
        assert.doesNotMatch(summary, /started|saved/i);
        assert.deepEqual(ctx.saved, []);
    });
});

describe('Memories export (search.js)', function () {
    it('fetches the export and reports the outcome rather than navigating to it', async function () {
        const ctx = setup(Promise.resolve(exportResponse({
            status: 500, json: { detail: 'Export failed (OperationalError). The daemon log has the details.' },
        })));
        ctx.document.body.insertAdjacentHTML('beforeend',
            '<select id="filter-category"></select><select id="filter-project"></select>');
        const toasts = [];
        ctx.window.showToast = (m) => toasts.push(m);
        evalModule(ctx.window, 'search.js');
        const result = await ctx.window.exportAll('csv');
        assert.equal(result.ok, false);
        assert.ok(ctx.calls.some((c) => c.startsWith('/api/export?format=csv')));
        assert.match(toasts[toasts.length - 1], /^Export failed: Export failed \(OperationalError\)/);
    });
});
