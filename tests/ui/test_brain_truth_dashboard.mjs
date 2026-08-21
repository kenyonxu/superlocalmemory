/** Living Brain must distinguish stored activity from evidence and correction review. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourceRoot = join(__dirname, '../../src/superlocalmemory/ui/js');
const classic = readFileSync(join(sourceRoot, 'brain.js'), 'utf8');
const od = readFileSync(join(sourceRoot, 'od-brain.js'), 'utf8');

describe('Living Brain truth rendering', function () {
  it('uses the additive truth snapshot and labels every quality dimension honestly', function () {
    for (const source of [classic, od]) {
      assert.match(source, /brain_truth/);
      assert.match(source, /Memory activity/);
      assert.match(source, /Feedback signals/);
      assert.match(source, /Claimed evidence/);
      assert.match(source, /Independently verified evidence/);
      assert.match(source, /External observations/);
      assert.match(source, /Correction quality/);
      assert.match(source, /observation only/i);
    }
  });

  it('does not present observations as an automatic ranker or training input', function () {
    for (const source of [classic, od]) {
      assert.match(source, /do not change recall, ranking, or model routing/i);
    }
  });

});
