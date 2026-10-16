import { it } from 'node:test';
import assert from 'node:assert/strict';
import { buildHarness, evalModule } from './harness.mjs';

it('Recall Lab distinguishes abstention, unavailable, warming and skipped', () => {
  const h = buildHarness(['recall-lab-form', 'recall-lab-results', 'recall-lab-meta']);
  evalModule(h.window, 'recall-lab.js');
  for (const [body, expected] of [
    [{ abstained: true }, "don't have"],
    [{ answer_check_status: 'unavailable' }, 'unavailable'],
    [{ answer_check_status: 'warming' }, 'warming'],
    [{ answer_check_status: 'skipped' }, 'skipped'],
  ]) {
    assert.match(h.window.buildAnswerCheckNotice(body).textContent.toLowerCase(), new RegExp(expected));
  }
});
