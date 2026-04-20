#!/usr/bin/env node
/**
 * Stage 8 High-finding regression tests for scripts/postinstall-interactive.js.
 *
 * Covers:
 *   H-09 — reply-file schema validation
 *   H-10 — --home path validation
 *   H-15 — downgrade notice visibility on TTY
 *
 * Uses Node's built-in `node:test` — no external deps. Run with:
 *   node --test tests/postinstall/test_postinstall_validation.js
 */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SCRIPT = path.join(REPO_ROOT, 'scripts', 'postinstall-interactive.js');
const mod = require(SCRIPT);

// ------------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------------

function mkTmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function writeReply(dir, obj) {
  const p = path.join(dir, 'reply.json');
  fs.writeFileSync(p, typeof obj === 'string' ? obj : JSON.stringify(obj));
  return p;
}

function runInstaller(args, extraEnv) {
  const env = { ...process.env, CI: 'true', ...(extraEnv || {}) };
  // Route the installer's $HOME to a temp dir so it never touches the real one.
  return spawnSync('node', [SCRIPT, ...args], {
    env,
    encoding: 'utf8',
    timeout: 30_000,
  });
}

// ------------------------------------------------------------------------
// H-09 — Reply-file schema validation
// ------------------------------------------------------------------------

test('H-09: unit — unknown top-level key rejected', () => {
  const r = mod.validateReplyFileSchema({ profile: 'balanced', evil_knob: true });
  assert.equal(r.ok, false);
  assert.match(r.error, /unexpected key/);
  assert.match(r.error, /evil_knob/);
});

test('H-09: unit — wrong-type value rejected', () => {
  const r = mod.validateReplyFileSchema({ accept_default: 'yes' });
  assert.equal(r.ok, false);
  assert.match(r.error, /accept_default/);
  assert.match(r.error, /boolean/);
});

test('H-09: unit — valid payload accepted', () => {
  const r = mod.validateReplyFileSchema({
    profile: 'custom',
    accept_default: true,
    no_benchmark: false,
    ram_ceiling_mb: 1200,
    evolution_llm: 'haiku',
  });
  assert.equal(r.ok, true);
});

test('H-09: unit — non-object JSON rejected', () => {
  assert.equal(mod.validateReplyFileSchema(null).ok, false);
  assert.equal(mod.validateReplyFileSchema([1, 2, 3]).ok, false);
  assert.equal(mod.validateReplyFileSchema('string').ok, false);
});

test('H-09: e2e — installer exits 2 on unknown key', () => {
  const tmp = mkTmpDir('slm-h09-');
  const reply = writeReply(tmp, { profile: 'balanced', evil_knob: 42 });
  const r = runInstaller([
    '--dry-run',
    '--home=' + tmp,
    '--home-outside-home',
    '--reply-file=' + reply,
  ]);
  assert.equal(r.status, 2, 'expected exit code 2, got ' + r.status + '\n' + r.stderr);
  assert.match(r.stderr, /invalid --reply-file/);
});

test('H-09: e2e — installer exits 2 on wrong-type value', () => {
  const tmp = mkTmpDir('slm-h09-');
  const reply = writeReply(tmp, { ram_ceiling_mb: 'lots' });
  const r = runInstaller([
    '--dry-run',
    '--home=' + tmp,
    '--home-outside-home',
    '--reply-file=' + reply,
  ]);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /ram_ceiling_mb/);
});

// ------------------------------------------------------------------------
// H-10 — --home path validation
// ------------------------------------------------------------------------

test('H-10: unit — relative path rejected', () => {
  const r = mod.validateHomePath('relative/path', os.homedir(), false);
  assert.equal(r.ok, false);
  assert.match(r.error, /absolute/);
});

test('H-10: unit — absolute path outside $HOME rejected without opt-in', () => {
  const r = mod.validateHomePath('/tmp/slm-test-home', os.homedir(), false);
  assert.equal(r.ok, false);
  assert.match(r.error, /outside \$HOME/);
});

test('H-10: unit — absolute path outside $HOME accepted WITH opt-in', () => {
  const tmp = mkTmpDir('slm-h10-');
  const r = mod.validateHomePath(tmp, os.homedir(), true);
  assert.equal(r.ok, true);
});

test('H-10: unit — path containing ".." rejected', () => {
  const r = mod.validateHomePath('/Users/someone/../etc', os.homedir(), true);
  assert.equal(r.ok, false);
  assert.match(r.error, /\.\./);
});

test('H-10: unit — path that is a file rejected', () => {
  const tmp = mkTmpDir('slm-h10-');
  const filePath = path.join(tmp, 'not-a-dir');
  fs.writeFileSync(filePath, 'hi');
  const r = mod.validateHomePath(filePath, os.homedir(), true);
  assert.equal(r.ok, false);
  assert.match(r.error, /not a directory/);
});

test('H-10: e2e — relative --home rejected with exit 2', () => {
  const r = runInstaller(['--dry-run', '--home=./relative']);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /invalid --home/);
});

test('H-10: e2e — outside-home --home rejected without opt-in', () => {
  const tmp = mkTmpDir('slm-h10-');
  const r = runInstaller(['--dry-run', '--home=' + tmp]);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /outside \$HOME/);
});

test('H-10: e2e — ".." in --home rejected', () => {
  const r = runInstaller(['--dry-run', '--home=/Users/x/../y', '--home-outside-home']);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /\.\./);
});

test('H-10: e2e — outside-home --home accepted WITH --home-outside-home', () => {
  const tmp = mkTmpDir('slm-h10-');
  const r = runInstaller(['--dry-run', '--home=' + tmp, '--home-outside-home']);
  assert.equal(r.status, 0, 'expected success; stderr=' + r.stderr);
});

// ------------------------------------------------------------------------
// H-15 — Downgrade notice visibility on TTY
// ------------------------------------------------------------------------

test('H-15: unit — describeDowngradeReason returns null when no downgrade', () => {
  const bench = { freeRamMb: 8192, coldStartMs: 100, diskFreeGb: 100, elapsedMs: 10 };
  assert.equal(mod.describeDowngradeReason('balanced', 'balanced', bench), null);
  assert.equal(mod.describeDowngradeReason(null, 'balanced', bench), null);
});

test('H-15: unit — describeDowngradeReason fires for power→minimal', () => {
  const bench = { freeRamMb: 500, coldStartMs: 100, diskFreeGb: 100, elapsedMs: 10 };
  const r = mod.describeDowngradeReason('power', 'minimal', bench);
  assert.ok(r);
  assert.equal(r.code, 'PROFILE_RAM_FLOOR');
  assert.match(r.line, /\[downgrade\]/);
  assert.match(r.line, /Requested profile "Power"/);
  assert.match(r.line, /falling back to "Minimal"/);
  assert.match(r.line, /PROFILE_RAM_FLOOR/);
});

test('H-15: unit — cold-start downgrade tagged PROFILE_COLD_START_FLOOR', () => {
  const bench = { freeRamMb: 2000, coldStartMs: 1500, diskFreeGb: 100, elapsedMs: 10 };
  const r = mod.describeDowngradeReason('power', 'light', bench);
  assert.ok(r);
  assert.equal(r.code, 'PROFILE_COLD_START_FLOOR');
});

test('H-15: e2e — no downgrade line on non-TTY (CI stdout is piped)', () => {
  // In the test subprocess, stdout.isTTY is false — so the installer must NOT
  // print the [downgrade] line, even when the benchmark would downgrade.
  const tmp = mkTmpDir('slm-h15-');
  const r = runInstaller(
    ['--dry-run', '--home=' + tmp, '--home-outside-home', '--profile=power'],
    { SLM_INSTALL_FREE_RAM_MB: '600', SLM_INSTALL_COLD_START_MS: '50', SLM_INSTALL_DISK_FREE_GB: '100' },
  );
  assert.equal(r.status, 0, 'dry-run failed: ' + r.stderr);
  assert.doesNotMatch(r.stdout, /\[downgrade\]/, 'stdout must be clean for non-TTY');
});

test('H-15: e2e — downgrade line prints when stdout.isTTY is simulated true', () => {
  // We simulate a TTY by launching the installer under a small harness that
  // overrides process.stdout.isTTY before the installer module runs. Because
  // the script uses `require.main === module`, we spawn a child that
  // pre-sets the flag and then requires the script.
  const tmp = mkTmpDir('slm-h15-');
  const harness = path.join(tmp, 'harness.js');
  fs.writeFileSync(
    harness,
    [
      "'use strict';",
      // Force isTTY true on stdout, keep stdin non-TTY so the installer still
      // takes the non-interactive path (isInteractive() also requires stdin).
      'Object.defineProperty(process.stdout, "isTTY", { value: true });',
      'process.argv = [process.argv[0], ' +
        JSON.stringify(SCRIPT) +
        ', "--dry-run", "--home=' +
        tmp.replace(/\\/g, '\\\\') +
        '", "--home-outside-home", "--profile=power"];',
      'const m = require(' + JSON.stringify(SCRIPT) + ');',
      'm.main().then((code) => process.exit(code || 0), (e) => { console.error(e); process.exit(1); });',
    ].join('\n'),
  );
  const r = spawnSync('node', [harness], {
    env: {
      ...process.env,
      CI: 'true',
      SLM_INSTALL_FREE_RAM_MB: '600',
      SLM_INSTALL_COLD_START_MS: '50',
      SLM_INSTALL_DISK_FREE_GB: '100',
    },
    encoding: 'utf8',
    timeout: 30_000,
  });
  assert.equal(r.status, 0, 'harness failed: ' + r.stderr);
  assert.match(r.stdout, /\[downgrade\]/);
  assert.match(r.stdout, /PROFILE_RAM_FLOOR/);
});
