/**
 * The "Upgrade memory engine" card (js/od-engine-upgrade.js), in the Embeddings group of Settings.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { makeEnv, writes, flushPromises } from './features_helpers.mjs';
import { buildHarness, evalModule } from './harness.mjs';

const API = '/api/v3/embedding/reindex';
const PLAN = {
    available: true, reason: '', already: false, needs_media: false, turn_on_command: '',
    media_enabled: true, env_state: 'ready',
    from: { provider: 'sentence-transformers', model: 'nomic-ai/nomic-embed-text-v1.5', dimension: 768 },
    to: { provider: 'slm-media', model: 'google/embeddinggemma-2', dimension: 768 },
    memories: 1200, ram_mb: 3000, disk_new_mb: 4, disk_kept_mb: 4, disk_mb: 8, minutes: 10,
    minutes_label: 'about 10 minutes', rollback: true, label: 'upgrade',
    explain: 'Your memories are re-read with the new engine in the background. Recall keeps working '
        + 'on the current engine until it finishes, nothing is deleted, and you can roll back '
        + 'to the previous engine afterwards.',
};
const JOB = { job_id: 4, kind: 'switch', state: 'queued', from: 'a::768', to: 'b::768', done: 0, total: 1200 };
const STATUS = { job: null, live: 'nomic-ai/nomic-embed-text-v1.5::768', previous: null, previous_vectors_kept: false };

function setup(initial = {}, extra = []) {
    const state = { plan: { ...PLAN, ...initial.plan }, status: { ...STATUS, ...initial.status },
        planStatus: initial.planStatus || 200, tracked: [], conflicts: [] };
    const routes = [
        ['GET', API + '/upgrade', () => ({ status: state.planStatus, json: state.plan })],
        ['GET', API, () => ({ json: state.status })],
        ['POST', API + '/upgrade', () => ({ status: 202, json: { accepted: true, label: 'upgrade', job: JOB, detail: 'Upgrading' } })],
        ['POST', API + '/rollback', () => ({ status: 202, json: { accepted: true, job: { ...JOB, kind: 'rollback' } } })],
        ['POST', API + '/forget-previous', () => ({ json: { success: true, freed_vectors: 1200 } })],
        ['POST', '/api/v3/features/media/enable', () => {
            state.plan = { ...state.plan, media_enabled: true, env_state: 'installing', available: false,
                needs_media: false, reason: 'Images and documents are still being set up. Try again when they finish.' };
            return { status: 202, json: { media: { enabled: true } } };
        }],
        ...extra,
    ];
    const h = makeEnv(routes, { modules: [] });
    h.state = state;
    h.window.odReindex = { track: (b) => state.tracked.push(b), conflict: (b) => state.conflicts.push(b), onFinish: () => {} };
    h.window.fetch0 = h.window.fetch;
    h.post = (url, body) => h.window.fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) }).then(r => r.ok ? r : r.json().then(d => {
        const e = new Error((d && (typeof d.detail === 'string' ? d.detail : d.error)) || 'failed');
        e.status = r.status; e.body = d; throw e; }));
    evalModule(h.window, 'od-engine-upgrade.js');
    h.mount = async () => {
        const panel = h.window.odEngineUpgrade.panel(h.post);
        h.document.getElementById('pane').appendChild(panel);
        await h.window.odEngineUpgrade.refresh();
        await flushPromises();
        return panel;
    };
    h.btn = (label) => [...h.document.querySelectorAll('#od-engine-upgrade button')].find(b => b.textContent.includes(label));
    h.text = () => h.document.getElementById('od-engine-upgrade').textContent;
    return h;
}

const ON_GEMMA = { plan: { available: false, already: true,
    reason: 'Text search runs on google/embeddinggemma-2, and recall\'s answer check is not calibrated for it yet.' },
    status: { live: 'google/embeddinggemma-2::768', previous: 'nomic::768', previous_vectors_kept: true } };

describe('text search engine card (two-model design, 4.2.2)', () => {
    it('text on the text model: the card is hidden and offers nothing', async () => {
        const h = setup();
        const panel = await h.mount();
        assert.equal(panel.style.display, 'none');
        assert.equal(h.btn('Upgrade memory engine'), undefined);
        assert.equal(writes(h).length, 0);
    });

    it('even a plan the server marks available is never offered', async () => {
        const h = setup({ plan: { available: true } });
        const panel = await h.mount();
        assert.equal(panel.style.display, 'none');
        assert.equal(h.document.querySelectorAll('#od-engine-upgrade button').length, 0);
        assert.equal(h.timers.length, 0, 'nothing to poll for');
    });

    it('text already on the picture model: the server\'s words and a way back', async () => {
        const h = setup(ON_GEMMA);
        const panel = await h.mount();
        assert.notEqual(panel.style.display, 'none');
        assert.match(h.text(), /Text search engine/);
        assert.match(h.text(), /not calibrated/);
        assert.equal(h.btn('Upgrade memory engine'), undefined);
        h.btn('Roll back').click();
        await flushPromises();
        assert.equal(writes(h)[0].url, API + '/rollback');
        assert.equal(h.state.tracked.length, 1);
    });

    it('free the old data asks first, then calls forget-previous', async () => {
        const h = setup(ON_GEMMA);
        await h.mount();
        h.confirmAnswer = false;
        h.btn('Free the old').click();
        await flushPromises();
        assert.equal(writes(h).length, 0);
        h.confirmAnswer = true;
        h.btn('Free the old').click();
        await flushPromises();
        assert.equal(writes(h)[0].url, API + '/forget-previous');
    });

    it('server text is never parsed as HTML', async () => {
        const h = setup({ plan: { ...ON_GEMMA.plan, reason: '<img src=x onerror=alert(1)>' },
            status: ON_GEMMA.status });
        await h.mount();
        assert.equal(h.document.querySelectorAll('#od-engine-upgrade img').length, 0);
        assert.match(h.text(), /<img src=x/);
    });

    it('an older or stopped daemon hides the card', async () => {
        for (const planStatus of [404, 409]) {
            const h = setup({ planStatus, plan: { error: 'daemon_required', detail: 'start it with: slm serve' } });
            const panel = await h.mount();
            assert.equal(panel.style.display, 'none');
        }
    });

    it('the Settings Embeddings group carries the card and reads the plan when it opens', async () => {
        const h = buildHarness(['mount'], { ok: true, status: 200, json: {} });
        const urls = [];
        h.window.fetch = async (url, o = {}) => {
            urls.push(String(url));
            const ok = (json) => ({ ok: true, status: 200, json: async () => json });
            if (url === '/internal/token') return ok({ token: 't' });
            if (String(url) === API + '/upgrade') return ok(PLAN);
            if (String(url) === API) return ok(STATUS);
            return ok({});
        };
        h.window.showToast = () => {};
        for (const m of ['od-reindex.js', 'od-engine-upgrade.js', 'od-settings.js']) evalModule(h.window, m);
        h.window.odRenderSettings(h.document.getElementById('mount'));
        await flushPromises();
        await flushPromises();
        assert.ok(h.document.getElementById('od-engine-upgrade'), 'card in the Embeddings group');
        assert.ok(urls.includes(API + '/upgrade'), 'the plan was read');
    });
});
