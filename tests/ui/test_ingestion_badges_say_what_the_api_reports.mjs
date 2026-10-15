/**
 * The ingestion card badge comes from the adapter's reported state.
 *
 * PR 251 review, finding 8: setup_error was ranked before enabled, so a
 * disabled transcript adapter with no folder read "Setup needs attention",
 * and the colour still came from `running`, so a failed or completed adapter
 * could show a success colour beside an error word.
 */

import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

const CASES = [
    // [adapter fields, expected text, expected colour class]
    [{ enabled: false, running: false, state: 'stopped',
       setup_error: 'Choose a readable transcript folder in Configure, then retry Start.' },
     'Disabled', 'ng-badge-neutral'],
    [{ enabled: true, running: false, state: 'stopped', setup_error: '' },
     'Enabled (Stopped)', 'ng-badge-warning'],
    [{ enabled: true, running: true, state: 'running', setup_error: '' },
     'Running', 'ng-badge-success'],
    [{ enabled: true, running: false, state: 'failed',
       setup_error: 'The worker exited. Check its setup in Configure, then retry Start.' },
     'Setup needs attention', 'ng-badge-error'],
    [{ enabled: true, running: true, state: 'failed', setup_error: 'x' },
     'Setup needs attention', 'ng-badge-error'],
    [{ enabled: true, running: false, state: 'completed', setup_error: '' },
     'Import complete', 'ng-badge-success'],
];

for (const [fields, text, cls] of CASES) {
    it(`state ${fields.state}, enabled ${fields.enabled} reads "${text}"`, async () => {
        const h = buildHarness(['ingestion-overview', 'ingestion-adapters', 'ingestion-log'],
                               { ok: true, status: 200, json: {} });
        try {
            const adapter = { name: 'transcript', pid: null, tier: 'auto', watch_dir: '', ...fields };
            h.window.fetch = () => Promise.resolve({ ok: true, status: 200,
                json: () => Promise.resolve({ adapters: [adapter] }) });
            evalModule(h.window, 'ng-ingestion.js');
            h.window.loadIngestionStatus();
            await flushPromises();
            const badge = h.document.querySelectorAll('#ingestion-adapters .ng-badge')[2];
            assert.equal(badge.textContent, text);
            assert.ok(badge.classList.contains(cls), badge.className);
        } finally { h.dom.window.close(); }
    });
}
