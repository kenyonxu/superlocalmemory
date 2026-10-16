/**
 * Memories -> All memories: a stored stub (session receipt, operation id) is
 * listed and counted like any memory, with a small "stub" badge so the user
 * can see why recall never answers with it (WP-19 review H3).
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule, flushPromises } from './harness.mjs';

function response(body) {
  return { ok: true, status: 200, json: function () { return Promise.resolve(body); } };
}

describe('od-memories.js — stub badge', function () {
  it('badges only the stub row', async function () {
    const h = buildHarness(['root'], { ok: true, status: 200, json: {} });
    h.window.fetch = function (url) {
      if (url.includes('kind-counts')) return Promise.resolve(response({ counts: {}, truncated: false }));
      return Promise.resolve(response({
        memories: [
          { id: 'stub-1', content: 'Session ID: 9f8e7d6c5b4a', stub: true, importance: 0.1, created_at: '2026-01-01T00:00:00Z' },
          { id: 'keep-1', content: 'Zephyr uses PostgreSQL.', stub: false, importance: 0.8, created_at: '2026-01-01T00:00:00Z' },
        ],
        total: 2, limit: 50, offset: 0, has_more: false,
      }));
    };
    evalModule(h.window, 'od-memories.js');
    h.window.odRenderMemories(h.document.getElementById('root'));
    await flushPromises();
    const rows = Array.from(h.document.querySelectorAll('tr.row'));
    assert.equal(rows.length, 2);
    const badges = rows.map(function (r) {
      return Array.from(r.querySelectorAll('.badge')).some(function (b) { return b.textContent === 'stub'; });
    });
    assert.deepEqual(badges, [true, false]);
  });
});
