/**
 * Runner: npm test   (scripts/run-ui-tests.mjs)
 *
 * WHY THIS EXISTS
 * ---------------
 * Two test files documented their runner as `node --test tests/ui/`. On Node 26
 * that does not scan the directory — it resolves the path as a MODULE and exits:
 *
 *     Error: Cannot find module '.../tests/ui'
 *     code: 'MODULE_NOT_FOUND'
 *     ✖ tests/ui (48ms)   1 test, 0 pass, 1 fail
 *
 * Every individual file passed. The suite was green. But anyone following the
 * documented command got a red result in ~48 ms that named no test and pointed
 * at no defect — and the natural conclusion is "the UI tests are broken", which
 * is exactly wrong. It cost this session a detour, and the instruction was
 * sitting inside the test files themselves.
 *
 * `scripts/run-ui-tests.mjs` (wired to `npm test`) enumerates test_*.mjs and
 * passes them as individual paths, which works on every Node version. These
 * tests keep the documentation pointing there.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const UI_DIR = __dirname;
const ROOT = join(__dirname, '../..');

/** `node --test <dir>` in any form — the invocation that cannot work. */
const BROKEN_INVOCATION = /node\s+--test\s+\S*tests\/ui\/?(?:\s|$)/;

describe('UI test runner is documented correctly', function () {
  const files = readdirSync(UI_DIR).filter((n) => /^test_.*\.mjs$/.test(n));

  it('finds UI test files to check', function () {
    assert.ok(files.length > 5, `expected several UI tests, found ${files.length}`);
  });

  for (const name of files) {
    it(`${name} does not document a directory invocation`, function () {
      const text = readFileSync(join(UI_DIR, name), 'utf8');
      // A line naming a specific .mjs file is fine — that form works.
      const offenders = text
        .split('\n')
        .filter((line) => BROKEN_INVOCATION.test(line) && !/\.mjs/.test(line));
      assert.equal(
        offenders.length,
        0,
        `${name} documents an invocation that fails on Node 26 with ` +
          `MODULE_NOT_FOUND before running anything:\n  ${offenders.join('\n  ')}\n` +
          'Use `npm test` (scripts/run-ui-tests.mjs) instead.',
      );
    });
  }

  it('npm test is wired to the enumerating runner', function () {
    const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));
    assert.equal(
      pkg.scripts?.test,
      'node scripts/run-ui-tests.mjs',
      'npm test must run the enumerating runner, not a directory argument',
    );
  });

  it('the runner passes individual files, never a directory', function () {
    const src = readFileSync(join(ROOT, 'scripts/run-ui-tests.mjs'), 'utf8');
    assert.ok(
      src.includes('readdirSync'),
      'runner must enumerate files rather than hand Node a directory',
    );
    assert.ok(
      src.includes("'--test', ...files") || src.includes('"--test", ...files'),
      'runner must spread individual file paths after --test',
    );
  });
});
