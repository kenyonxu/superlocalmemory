/** UX-12: search must keep a shared decision's kind and visibility in its drawer. */
import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

it('search opens the same shared decision with its audience and project', async () => {
    const h = buildHarness(['root'], { ok: true, status: 200, json: {} });
    const memory = {
        fact_id: 'shared-decision', content: 'Ship Atlas on Friday.',
        scope: 'shared', shared_with: ['sales'], project_name: 'Atlas',
        memory_kind: 'decision', memory_kind_label: 'Decision', memory_kind_state: 'confirmed',
        category: 'episodic', created_at: '2026-10-11T12:00:00Z', score: 0.9,
    };
    const requested = [];
    h.window.fetch = (url) => {
        requested.push(url);
        const body = url.includes('/api/search') ? { results: [memory], total: 1 }
            : url.includes('kind-counts') ? { counts: { decision: 1 } }
                : { memories: [], total: 0 };
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    };
    try {
        evalModule(h.window, 'od-memories.js');
        const root = h.document.getElementById('root');
        h.window.odRenderMemories(root);
        await flushPromises();
        const input = root.querySelector('[data-od-act="search"]');
        assert.ok(input);
        input.value = 'Atlas';
        input.dispatchEvent(new h.window.Event('input', { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 450));
        await flushPromises();
        assert.ok(requested.some(url => url.includes('/api/search')));
        const row = root.querySelector('[data-od-act="drawer"]');
        assert.ok(row);
        assert.match(row.textContent, /Decision/);
        row.click();
        await flushPromises();
        const drawer = h.document.getElementById('od-mem-drawer');
        assert.ok(drawer);
        const badges = Array.from(drawer.querySelectorAll('.badge')).map(el => el.textContent);
        assert.ok(badges.includes('Atlas'), 'the drawer must preserve the project badge');
        assert.ok(badges.includes('Decision'), 'the drawer must preserve the kind badge');
        assert.equal(drawer.querySelector('#od-scope-sel').value, 'shared');
        assert.equal(drawer.querySelector('#od-scope-shared').value, 'sales');
    } finally {
        h.dom.window.close();
    }
});
