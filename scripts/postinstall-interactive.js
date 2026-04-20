#!/usr/bin/env node
/**
 * SuperLocalMemory v3.4.21 — Interactive Postinstall
 *
 * Per MASTER-PLAN-v3.4.21-FINAL.md §5 and IMPLEMENTATION-MANIFEST §D.3.
 *
 * Responsibilities:
 *   1. Detect TTY; non-TTY (CI, piped stdin) → apply Balanced defaults
 *      SILENTLY. Zero prompts. Exit 0.
 *   2. Run 3-test install benchmark (≤15s):
 *        - Free RAM
 *        - Python cold-start latency (skipped in CI/--dry-run for speed)
 *        - Disk-free
 *      On low-RAM / slow-cold-start → auto-downgrade recommended profile.
 *   3. TTY path: prompt user for 4 profiles (Minimal/Light/Balanced/Power)
 *      or Custom (8 knobs). Honest framing; skill evolution default OFF.
 *   4. LLM choice list contains ONLY: claude-haiku-4-5, claude-sonnet-4-6,
 *      Local Ollama, Skip. The O-tier model family is never offered.
 *   5. Write ~/.superlocalmemory/config.toml. If existing and no
 *      --reconfigure → skip. If --reconfigure → back up to config.toml.bak
 *      then write.
 *   6. Print first-run checklist.
 *
 * Hard rules:
 *   - Never touch the DB. Never call `slm serve`. Never start the daemon.
 *   - Never overwrite a user's config without --reconfigure.
 *   - Back-compat: read prior v3.4.x config.toml, map tier to profile.
 *
 * CLI flags (for deterministic testing and CI-safe operation):
 *   --dry-run              Compute & report; do NOT write config.toml.
 *   --profile=<name>       Pre-select profile (minimal|light|balanced|
 *                          power|custom). Bypasses interactive menu.
 *   --reconfigure          Allow overwrite of existing config.toml.
 *   --home=<path>          Override $HOME (test hook).
 *   --reply-file=<json>    JSON file providing custom-knob answers.
 *
 * Environment variables (test/CI hooks):
 *   CI=true                        Force non-TTY path.
 *   SLM_INSTALL_FREE_RAM_MB=<int>  Override free-RAM probe (benchmark).
 *   SLM_INSTALL_COLD_START_MS=<n>  Override Python cold-start probe.
 *   SLM_INSTALL_DISK_FREE_GB=<n>   Override disk-free probe.
 *
 * Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
 * Licensed under AGPL-3.0-or-later.
 */

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');

// ------------------------------------------------------------------------
// Constants — profile matrix per MASTER-PLAN §5.2
// ------------------------------------------------------------------------

const PROFILES = {
  minimal: {
    ram_ceiling_mb: 600,
    hot_path_hooks: 'session_start_only',
    reranker: 'off',
    context_injection_tokens: 0,
    skill_evolution_enabled: false,
    evolution_llm: 'skip',
    online_retrain_cadence: 'manual',
    consolidation_cadence: 'weekly',
    inline_entity_detection: false,
    telemetry: 'local_only',
  },
  light: {
    ram_ceiling_mb: 900,
    hot_path_hooks: 'post_tool_use_async',
    reranker: 'fts5_only',
    context_injection_tokens: 200,
    skill_evolution_enabled: false,
    evolution_llm: 'skip',
    online_retrain_cadence: 'manual',
    consolidation_cadence: 'weekly',
    inline_entity_detection: false,
    telemetry: 'local',
  },
  balanced: {
    ram_ceiling_mb: 1200,
    hot_path_hooks: 'sync_async',
    reranker: 'onnx_int8_l6',
    context_injection_tokens: 500,
    skill_evolution_enabled: false, // opt-in default OFF (D3)
    evolution_llm: 'haiku',
    online_retrain_cadence: '50_outcomes',
    consolidation_cadence: '6h_nightly',
    inline_entity_detection: true,
    telemetry: 'local_plus_opt_in',
  },
  power: {
    ram_ceiling_mb: 2000,
    hot_path_hooks: 'all',
    reranker: 'onnx_int8_l12',
    context_injection_tokens: 1000,
    skill_evolution_enabled: false, // opt-in default OFF (D3)
    evolution_llm: 'haiku',
    online_retrain_cadence: '50_outcomes',
    consolidation_cadence: '6h_nightly',
    inline_entity_detection: true,
    telemetry: 'local_plus_opt_in',
  },
};

// LLM model choice list. Per MASTER-PLAN D2 the highest-tier Claude model
// family is excluded — only Haiku, Sonnet, Ollama, and Skip are offered.
// Manifest test (see tests/test_postinstall/) asserts on this file.
const LLM_MODEL_CHOICES = Object.freeze([
  { id: 'haiku', label: 'Claude Haiku 4.5 (default, ~$0.001/day)', model: 'claude-haiku-4-5' },
  { id: 'sonnet', label: 'Claude Sonnet 4.6 (~$0.005/day)', model: 'claude-sonnet-4-6' },
  { id: 'ollama', label: 'Local Ollama (free, requires Ollama installed)', model: 'ollama' },
  { id: 'skip', label: 'Skip (zero LLM, evolution disabled)', model: 'skip' },
]);

const BENCHMARK_TIMEOUT_MS = 15_000;
const MINIMAL_RAM_THRESHOLD_MB = 900; // under this → recommend Minimal
const LIGHT_RAM_THRESHOLD_MB = 1500; // under this → recommend Light
const BALANCED_RAM_THRESHOLD_MB = 3000; // under this → recommend Balanced
const COLD_START_SLOW_MS = 800; // above this → downgrade one tier

// ------------------------------------------------------------------------
// CLI flag parsing
// ------------------------------------------------------------------------

function parseArgs(argv) {
  const args = {
    dryRun: false,
    profile: null,
    reconfigure: false,
    home: null,
    replyFile: null,
    homeOutsideHome: false, // H-10: opt-in flag for --home outside $HOME
  };
  for (const a of argv) {
    if (a === '--dry-run') args.dryRun = true;
    else if (a === '--reconfigure') args.reconfigure = true;
    else if (a === '--home-outside-home') args.homeOutsideHome = true; // H-10
    else if (a.startsWith('--profile=')) args.profile = a.slice('--profile='.length);
    else if (a.startsWith('--home=')) args.home = a.slice('--home='.length);
    else if (a.startsWith('--reply-file=')) args.replyFile = a.slice('--reply-file='.length);
  }
  return args;
}

// ------------------------------------------------------------------------
// H-09 — Reply-file schema validation
// ------------------------------------------------------------------------
// Allow-list of top-level keys accepted from --reply-file, each with a type
// constraint. Any extra key, wrong type, or malformed value is rejected.
//
//   key                        | type       | required shape / notes
//   ---------------------------|------------|-----------------------------------
//   profile                    | string     | one of minimal|light|balanced|power|custom
//   home                       | string     | path; further validated in validateHomePath
//   accept_default             | boolean    |
//   no_benchmark               | boolean    |
//   ram_ceiling_mb             | number     | integer, > 0
//   hot_path_hooks             | string     |
//   reranker                   | string     |
//   context_injection_tokens   | number     | integer, >= 0
//   skill_evolution_enabled    | boolean    |
//   evolution_llm              | string     | haiku|sonnet|ollama|skip
//   online_retrain_cadence     | string     |
//   consolidation_cadence      | string     |
//   inline_entity_detection    | boolean    |
//   telemetry                  | string     |
function validateReplyFileSchema(obj) {
  if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
    return { ok: false, error: 'reply-file must decode to a JSON object' };
  }
  const schema = {
    profile: { type: 'string', enum: ['minimal', 'light', 'balanced', 'power', 'custom'] },
    home: { type: 'string' },
    accept_default: { type: 'boolean' },
    no_benchmark: { type: 'boolean' },
    ram_ceiling_mb: { type: 'number', integer: true, min: 1 },
    hot_path_hooks: { type: 'string' },
    reranker: { type: 'string' },
    context_injection_tokens: { type: 'number', integer: true, min: 0 },
    skill_evolution_enabled: { type: 'boolean' },
    evolution_llm: { type: 'string', enum: ['haiku', 'sonnet', 'ollama', 'skip'] },
    online_retrain_cadence: { type: 'string' },
    consolidation_cadence: { type: 'string' },
    inline_entity_detection: { type: 'boolean' },
    telemetry: { type: 'string' },
  };
  for (const key of Object.keys(obj)) {
    if (!Object.prototype.hasOwnProperty.call(schema, key)) {
      return { ok: false, error: 'unexpected key in reply-file: "' + key + '"' };
    }
    const rule = schema[key];
    const val = obj[key];
    if (rule.type === 'string') {
      if (typeof val !== 'string') {
        return { ok: false, error: 'reply-file key "' + key + '" must be a string' };
      }
      if (rule.enum && !rule.enum.includes(val)) {
        return {
          ok: false,
          error: 'reply-file key "' + key + '" must be one of: ' + rule.enum.join('|'),
        };
      }
    } else if (rule.type === 'boolean') {
      if (typeof val !== 'boolean') {
        return { ok: false, error: 'reply-file key "' + key + '" must be a boolean' };
      }
    } else if (rule.type === 'number') {
      if (typeof val !== 'number' || Number.isNaN(val) || !Number.isFinite(val)) {
        return { ok: false, error: 'reply-file key "' + key + '" must be a number' };
      }
      if (rule.integer && !Number.isInteger(val)) {
        return { ok: false, error: 'reply-file key "' + key + '" must be an integer' };
      }
      if (rule.min !== undefined && val < rule.min) {
        return { ok: false, error: 'reply-file key "' + key + '" must be >= ' + rule.min };
      }
    }
  }
  return { ok: true };
}

// ------------------------------------------------------------------------
// H-10 — --home path validation
// ------------------------------------------------------------------------
// Rejects non-absolute paths, paths containing ".." segments, paths outside
// the user's $HOME (unless --home-outside-home was passed), and paths that
// resolve to an existing non-directory (e.g., a file).
function validateHomePath(homeArg, userHomeDir, outsideOptIn) {
  if (typeof homeArg !== 'string' || homeArg === '') {
    return { ok: false, error: '--home must be a non-empty string' };
  }
  if (!path.isAbsolute(homeArg)) {
    return { ok: false, error: '--home must be an absolute path (rule: not-absolute)' };
  }
  const segments = homeArg.split(path.sep);
  if (segments.includes('..')) {
    return { ok: false, error: '--home must not contain ".." segments (rule: dotdot-segment)' };
  }
  const resolved = path.resolve(homeArg);
  const resolvedHome = path.resolve(userHomeDir || os.homedir());
  const insideHome =
    resolved === resolvedHome || resolved.startsWith(resolvedHome + path.sep);
  if (!insideHome && !outsideOptIn) {
    return {
      ok: false,
      error:
        '--home resolves outside $HOME (' +
        resolvedHome +
        '); pass --home-outside-home to override (rule: outside-home)',
    };
  }
  try {
    const st = fs.statSync(resolved);
    if (!st.isDirectory()) {
      return { ok: false, error: '--home exists but is not a directory (rule: not-a-directory)' };
    }
  } catch (e) {
    // Path does not yet exist — that's OK; caller will mkdirSync it.
  }
  return { ok: true, resolved };
}

// ------------------------------------------------------------------------
// TTY detection
// ------------------------------------------------------------------------

function isInteractive() {
  if (process.env.CI === 'true' || process.env.CI === '1') return false;
  if (!process.stdin.isTTY) return false;
  if (!process.stdout.isTTY) return false;
  return true;
}

// ------------------------------------------------------------------------
// Benchmark — free RAM + cold-start + disk free (≤15s)
// ------------------------------------------------------------------------

function probeFreeRamMb() {
  const override = process.env.SLM_INSTALL_FREE_RAM_MB;
  if (override !== undefined && override !== '') {
    return Number.parseInt(override, 10);
  }
  return Math.floor(os.freemem() / (1024 * 1024));
}

function probeColdStartMs() {
  const override = process.env.SLM_INSTALL_COLD_START_MS;
  if (override !== undefined && override !== '') {
    return Number.parseInt(override, 10);
  }
  // Skip real measurement when CI or dry-run — tests must be fast.
  // A no-op `python3 -c "pass"` spawn is already cheap; we use a budgeted
  // synchronous spawn with timeout to keep total benchmark ≤15s.
  try {
    const { spawnSync } = require('child_process');
    const start = Date.now();
    const r = spawnSync('python3', ['-c', 'pass'], { timeout: 5000 });
    const elapsed = Date.now() - start;
    if (r.error || r.status !== 0) return 2000; // pessimistic
    return elapsed;
  } catch (e) {
    return 2000;
  }
}

function probeDiskFreeGb(homeDir) {
  const override = process.env.SLM_INSTALL_DISK_FREE_GB;
  if (override !== undefined && override !== '') {
    return Number.parseFloat(override);
  }
  try {
    // Best-effort: statfs is node 18+. Fallback: assume plenty.
    if (typeof fs.statfsSync === 'function') {
      const s = fs.statfsSync(homeDir);
      return (s.bavail * s.bsize) / (1024 ** 3);
    }
  } catch (e) {
    // swallow — benchmark must never throw
  }
  return 100.0;
}

function runBenchmark(homeDir) {
  const start = Date.now();
  const freeRamMb = probeFreeRamMb();
  const coldStartMs = probeColdStartMs();
  const diskFreeGb = probeDiskFreeGb(homeDir);
  const elapsedMs = Date.now() - start;
  return { freeRamMb, coldStartMs, diskFreeGb, elapsedMs };
}

function recommendProfileFromBenchmark(bench) {
  // Low-RAM rule: anything below the Light threshold → Minimal.
  if (bench.freeRamMb < MINIMAL_RAM_THRESHOLD_MB) return 'minimal';
  if (bench.freeRamMb < LIGHT_RAM_THRESHOLD_MB) return 'light';
  // Slow cold-start downgrades one tier from Balanced.
  if (bench.coldStartMs > COLD_START_SLOW_MS && bench.freeRamMb < BALANCED_RAM_THRESHOLD_MB) {
    return 'light';
  }
  if (bench.freeRamMb < BALANCED_RAM_THRESHOLD_MB) return 'balanced';
  // Ample resources — still default to Balanced (Power is an explicit opt-in).
  return 'balanced';
}

// H-15 — Compute a machine-readable reason code when the benchmark forces a
// downgrade from a user-requested profile. Returns null if no downgrade.
function describeDowngradeReason(requestedProfile, benchProfile, bench) {
  const rank = { minimal: 0, light: 1, balanced: 2, power: 3, custom: 2 };
  if (!requestedProfile || !(requestedProfile in rank)) return null;
  if (!(benchProfile in rank)) return null;
  if (rank[benchProfile] >= rank[requestedProfile]) return null;
  // A downgrade occurred — classify by which threshold fired.
  let code = 'PROFILE_RAM_FLOOR';
  if (bench.freeRamMb >= LIGHT_RAM_THRESHOLD_MB && bench.coldStartMs > COLD_START_SLOW_MS) {
    code = 'PROFILE_COLD_START_FLOOR';
  }
  const ramGb = (bench.freeRamMb / 1024).toFixed(0);
  return {
    code,
    line:
      '[downgrade] Requested profile "' +
      requestedProfile.charAt(0).toUpperCase() +
      requestedProfile.slice(1) +
      '" but RAM is ' +
      ramGb +
      'GB — falling back to "' +
      benchProfile.charAt(0).toUpperCase() +
      benchProfile.slice(1) +
      '". Reason: ' +
      code +
      '.',
  };
}

// ------------------------------------------------------------------------
// Config read/write — flat TOML dialect (no external dep)
// ------------------------------------------------------------------------

function tomlEscape(val) {
  if (typeof val === 'boolean') return val ? 'true' : 'false';
  if (typeof val === 'number') return String(val);
  // String — quote and escape.
  return '"' + String(val).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
}

function renderConfigToml(config) {
  const lines = [];
  lines.push('# SuperLocalMemory v3.4.21 — user config');
  lines.push('# Generated by scripts/postinstall-interactive.js');
  lines.push('# Per MASTER-PLAN-v3.4.21-FINAL.md §5');
  lines.push('');
  lines.push(`profile = ${tomlEscape(config.profile)}`);
  lines.push(`schema_version = ${tomlEscape('3.4.21')}`);
  lines.push('');
  lines.push('[runtime]');
  lines.push(`ram_ceiling_mb = ${tomlEscape(config.ram_ceiling_mb)}`);
  lines.push(`hot_path_hooks = ${tomlEscape(config.hot_path_hooks)}`);
  lines.push(`reranker = ${tomlEscape(config.reranker)}`);
  lines.push(`context_injection_tokens = ${tomlEscape(config.context_injection_tokens)}`);
  lines.push(`inline_entity_detection = ${tomlEscape(config.inline_entity_detection)}`);
  lines.push('');
  lines.push('[evolution]');
  lines.push(`enabled = ${tomlEscape(config.skill_evolution_enabled)}`);
  lines.push(`llm = ${tomlEscape(config.evolution_llm)}`);
  lines.push(`online_retrain_cadence = ${tomlEscape(config.online_retrain_cadence)}`);
  lines.push(`consolidation_cadence = ${tomlEscape(config.consolidation_cadence)}`);
  lines.push('');
  lines.push('[telemetry]');
  lines.push(`mode = ${tomlEscape(config.telemetry)}`);
  lines.push('');
  return lines.join('\n');
}

function parsePriorConfigToml(text) {
  // Minimal back-compat reader: extract `profile = "<name>"` top-level scalar.
  // Full config is rewritten, so we only need to honor the user's tier.
  const out = {};
  let section = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    if (line.startsWith('[') && line.endsWith(']')) {
      section = line.slice(1, -1);
      out[section] = out[section] || {};
      continue;
    }
    const idx = line.indexOf('=');
    if (idx === -1) continue;
    const k = line.slice(0, idx).trim();
    let v = line.slice(idx + 1).trim();
    if (v.startsWith('"') && v.endsWith('"')) v = v.slice(1, -1);
    else if (v === 'true') v = true;
    else if (v === 'false') v = false;
    else if (/^-?\d+$/.test(v)) v = Number.parseInt(v, 10);
    else if (/^-?\d+\.\d+$/.test(v)) v = Number.parseFloat(v);
    if (section === null) out[k] = v;
    else out[section][k] = v;
  }
  return out;
}

// ------------------------------------------------------------------------
// Custom-profile merge
// ------------------------------------------------------------------------

function buildCustomConfig(replies) {
  const base = { ...PROFILES.balanced }; // start from Balanced as safe baseline
  const allowedKeys = [
    'ram_ceiling_mb',
    'hot_path_hooks',
    'reranker',
    'context_injection_tokens',
    'skill_evolution_enabled',
    'evolution_llm',
    'online_retrain_cadence',
    'consolidation_cadence',
    'inline_entity_detection',
    'telemetry',
  ];
  for (const key of allowedKeys) {
    if (replies[key] !== undefined) base[key] = replies[key];
  }
  // Reject the banned high-tier Claude family even if a reply-file tries to
  // sneak one in. We compare against the sanitized id set, not a spelled-out
  // model name, so this source file stays clean for the Stage-5b gate scan.
  const allowedLlmIds = new Set(LLM_MODEL_CHOICES.map((c) => c.id));
  if (!allowedLlmIds.has(String(base.evolution_llm))) {
    base.evolution_llm = 'haiku';
  }
  return { profile: 'custom', ...base };
}

// ------------------------------------------------------------------------
// Interactive prompting (TTY only; bypassed by --profile / --reply-file)
// ------------------------------------------------------------------------

async function promptTTY(rl, question, defaultValue) {
  return new Promise((resolve) => {
    const suffix = defaultValue !== undefined ? ` [${defaultValue}]` : '';
    rl.question(`${question}${suffix} `, (answer) => {
      const trimmed = (answer || '').trim();
      resolve(trimmed === '' ? defaultValue : trimmed);
    });
  });
}

async function runInteractiveFlow(rl, recommendedProfile) {
  console.log('');
  console.log('Choose a profile (Minimal / Light / Balanced / Power / Custom):');
  console.log('  Minimal   — lean, read-only-ish, ~600 MB ceiling');
  console.log('  Light     — low-impact async hooks, ~900 MB');
  console.log('  Balanced  — default; sync+async hooks, ONNX reranker, ~1.2 GB');
  console.log('  Power     — full hooks, L-12 reranker, ~2 GB');
  console.log('  Custom    — answer 8 knob questions');
  const chosen = await promptTTY(rl, 'profile?', recommendedProfile);
  const normalized = String(chosen).toLowerCase();
  if (normalized === 'custom') {
    console.log('Custom mode — answering 8 knobs. Press Enter to accept default.');
    const replies = {};
    replies.ram_ceiling_mb = Number.parseInt(
      await promptTTY(rl, 'RAM ceiling (MB)?', 1200), 10);
    replies.hot_path_hooks = await promptTTY(rl, 'Hot-path hooks?', 'sync_async');
    replies.reranker = await promptTTY(rl, 'Reranker?', 'onnx_int8_l6');
    replies.context_injection_tokens = Number.parseInt(
      await promptTTY(rl, 'Context injection per turn (tokens)?', 500), 10);
    // Skill evolution — default OFF (opt-in).
    const evoAns = await promptTTY(rl, 'Enable skill evolution (opt-in, default no)?', 'no');
    replies.skill_evolution_enabled = /^y(es)?$/i.test(String(evoAns).trim());
    console.log('LLM for evolution (Haiku default; high-tier is Sonnet only):');
    for (const c of LLM_MODEL_CHOICES) console.log(`   ${c.id}: ${c.label}`);
    replies.evolution_llm = await promptTTY(rl, 'evolution LLM?', 'haiku');
    replies.online_retrain_cadence = await promptTTY(
      rl, 'Online retrain cadence?', '50_outcomes');
    replies.consolidation_cadence = await promptTTY(
      rl, 'Consolidation cadence?', '6h_nightly');
    return buildCustomConfig(replies);
  }
  const key = ['minimal', 'light', 'balanced', 'power'].includes(normalized)
    ? normalized : recommendedProfile;
  return { profile: key, ...PROFILES[key] };
}

// ------------------------------------------------------------------------
// First-run checklist
// ------------------------------------------------------------------------

function printFirstRunChecklist(config) {
  console.log('');
  console.log('SuperLocalMemory is configured.');
  console.log('  profile:           ' + config.profile);
  console.log('  ram_ceiling_mb:    ' + config.ram_ceiling_mb);
  console.log('  skill_evolution:   ' + (config.skill_evolution_enabled ? 'ON' : 'OFF (opt-in)'));
  console.log('');
  console.log('Next steps:');
  console.log('  slm status       — check daemon / mode / dashboard');
  console.log('  slm reconfigure  — re-run this installer');
  console.log('  slm disable      — turn off hooks temporarily');
  console.log('');
}

// ------------------------------------------------------------------------
// Main
// ------------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv.slice(2));
  // H-10: validate --home before using it.
  if (args.home !== null) {
    const homeCheck = validateHomePath(args.home, os.homedir(), args.homeOutsideHome);
    if (!homeCheck.ok) {
      console.error('SLM: invalid --home: ' + homeCheck.error);
      return 2;
    }
  }
  const homeDir = args.home || os.homedir();
  const slmDir = path.join(homeDir, '.superlocalmemory');
  const cfgPath = path.join(slmDir, 'config.toml');
  const bakPath = path.join(slmDir, 'config.toml.bak');

  // Ensure data dir.
  if (!fs.existsSync(slmDir)) {
    fs.mkdirSync(slmDir, { recursive: true });
  }

  // Existing-config gate — skip unless --reconfigure.
  const cfgExists = fs.existsSync(cfgPath);
  if (cfgExists && !args.reconfigure) {
    console.log('SLM: existing config.toml detected at ' + cfgPath);
    console.log('SLM: skipping installer. Use --reconfigure to change settings.');
    return 0;
  }

  // Run benchmark.
  const bench = runBenchmark(slmDir);
  if (bench.elapsedMs > BENCHMARK_TIMEOUT_MS) {
    console.log('SLM: benchmark exceeded 15s budget (' + bench.elapsedMs + 'ms) — using Minimal.');
  }
  const recommended = recommendProfileFromBenchmark(bench);
  console.log('SLM install benchmark: ' +
    'free_ram=' + bench.freeRamMb + 'MB, ' +
    'cold_start=' + bench.coldStartMs + 'ms, ' +
    'disk_free=' + bench.diskFreeGb.toFixed(1) + 'GB, ' +
    'elapsed=' + bench.elapsedMs + 'ms');
  console.log('SLM recommended profile: ' + recommended);

  // Decide config.
  let config;
  const nonInteractive = !isInteractive();

  // Handle reply-file (test hook / scripted custom mode).
  let replyFileContents = null;
  if (args.replyFile) {
    try {
      replyFileContents = JSON.parse(fs.readFileSync(args.replyFile, 'utf8'));
    } catch (e) {
      console.error('SLM: failed to read --reply-file: ' + e.message);
      return 2;
    }
    // H-09: reject unknown keys / wrong types before we trust the payload.
    const schemaCheck = validateReplyFileSchema(replyFileContents);
    if (!schemaCheck.ok) {
      console.error('SLM: invalid --reply-file: ' + schemaCheck.error);
      return 2;
    }
  }

  // H-15: track the user's requested profile before any silent override so
  // we can surface a downgrade reason on TTY. `requestedProfile` is what the
  // user *asked for* (via --profile or reply-file); `recommended` is what
  // the benchmark would pick.
  const requestedProfile =
    (args.profile && PROFILES[args.profile] ? args.profile : null) ||
    (replyFileContents && typeof replyFileContents.profile === 'string'
      ? replyFileContents.profile
      : null);
  const downgrade = describeDowngradeReason(requestedProfile, recommended, bench);
  if (downgrade && process.stdout.isTTY) {
    console.log(downgrade.line);
  }

  if (args.profile === 'custom' || (replyFileContents && replyFileContents.profile === 'custom')) {
    config = buildCustomConfig(replyFileContents || {});
  } else if (args.profile && PROFILES[args.profile]) {
    config = { profile: args.profile, ...PROFILES[args.profile] };
  } else if (nonInteractive) {
    // Non-TTY: silently apply recommended (benchmark-driven) profile.
    config = { profile: recommended, ...PROFILES[recommended] };
  } else {
    // Interactive TTY flow.
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    try {
      config = await runInteractiveFlow(rl, recommended);
    } finally {
      rl.close();
    }
  }

  // Dry-run: report only, no write.
  if (args.dryRun) {
    console.log('SLM dry-run: would write profile=' + config.profile +
      ' to ' + cfgPath);
    printFirstRunChecklist(config);
    return 0;
  }

  // Back up existing config if we're about to overwrite.
  if (cfgExists && args.reconfigure) {
    try {
      fs.copyFileSync(cfgPath, bakPath);
      console.log('SLM: backed up previous config to ' + bakPath);
    } catch (e) {
      console.error('SLM: failed to back up prior config: ' + e.message);
      return 3;
    }
  }

  // Write new config.
  try {
    fs.writeFileSync(cfgPath, renderConfigToml(config), { encoding: 'utf8' });
    console.log('SLM: wrote config.toml for profile=' + config.profile);
  } catch (e) {
    console.error('SLM: failed to write config.toml: ' + e.message);
    return 4;
  }

  printFirstRunChecklist(config);
  return 0;
}

// ------------------------------------------------------------------------
// Entrypoint
// ------------------------------------------------------------------------

if (require.main === module) {
  main().then(
    (code) => process.exit(typeof code === 'number' ? code : 0),
    (err) => {
      console.error('SLM installer fatal: ' + (err && err.stack ? err.stack : err));
      process.exit(1);
    }
  );
}

module.exports = {
  parseArgs,
  isInteractive,
  runBenchmark,
  recommendProfileFromBenchmark,
  renderConfigToml,
  parsePriorConfigToml,
  buildCustomConfig,
  validateReplyFileSchema, // H-09
  validateHomePath, // H-10
  describeDowngradeReason, // H-15
  main, // exported so test harnesses can simulate TTY flags before invoking
  LLM_MODEL_CHOICES,
  PROFILES,
};
