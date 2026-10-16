/**
 * tests/ui/test_erase_outcome_states_what_remains.mjs
 * Runner: npm test   (scripts/run-ui-tests.mjs)
 *
 * UX-31: erasing a profile deleted the facts, answered HTTP 200 with success=false,
 * and the page said "Erase failed: unknown" because it read only an `error` key the
 * body did not have. The page must say what is gone and what is left, from the
 * server's own `erasure_report`, and must only say "erased completely" on success.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule } from './harness.mjs';

function render(body) {
    const h = buildHarness(['pane'], () => Promise.resolve({ ok: true, status: 200, json: async () => ({}) }));
    evalModule(h.window, 'od-operations.js');
    const summary = h.document.createElement('div');
    h.window.slmRenderEraseOutcome(summary, 'project-x', body);
    return summary.textContent;
}

describe('the erase outcome', function () {
    it('says "erased completely" only when the server says success', function () {
        const text = render({ success: true, erasure_report: { complete: true, remains: [], removed: {} } });
        assert.match(text, /erased completely/);
    });

    it('lists what remains and how to retry, never "unknown", for an incomplete erasure', function () {
        const text = render({
            success: false,
            error: 'The erasure is not complete. Backups still hold this profile.',
            erasure_report: {
                complete: false,
                removed: { atomic_facts: 3, memories: 3 },
                remains: [{ step: 'backup_obligations_pending', what: 'Backups still hold this profile.',
                            retry: 'Delete those backups, or restore none of them.' }],
            },
        });
        assert.match(text, /INCOMPLETE/);
        assert.match(text, /Backups still hold this profile\. Delete those backups/);
        assert.match(text, /atomic_facts: 3/);
        assert.doesNotMatch(text, /unknown|erased completely/);
    });

    it('shows the server error when there is no report (nothing was attempted)', function () {
        const text = render({ success: false, error: 'Confirmation required.' });
        assert.match(text, /Erase failed: Confirmation required\./);
    });
});
