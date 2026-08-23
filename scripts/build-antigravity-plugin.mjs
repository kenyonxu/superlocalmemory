#!/usr/bin/env node
// Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
// Licensed under AGPL-3.0-or-later - see LICENSE file
//
// build-antigravity-plugin.mjs — generate antigravity-plugin/ from plugin-src/.
//
// USAGE
//   node scripts/build-antigravity-plugin.mjs           # write
//   node scripts/build-antigravity-plugin.mjs --check   # verify in sync (exit 2)
//
// WHY
//   There was no Antigravity tree at all, so on that host SLM had no skills, no
//   agents, no commands and no hooks — nothing. Every other surface is generated
//   from plugin-src/; this one was simply missing, which is the least visible way
//   for a distribution channel to be broken.
//
//   Derived from the SAME source as the other three so the four cannot drift.

import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';

const ROOT = join(dirname(new URL(import.meta.url).pathname), '..');
const OUT = join(ROOT, 'antigravity-plugin');
const SRC = join(ROOT, 'plugin-src');
const AGENT_ID = 'antigravity';

const manifest = JSON.parse(readFileSync(join(SRC, 'manifest.json'), 'utf8'));
const VERSION = manifest.version;
if (!VERSION) throw new Error('manifest.version missing');

const check = process.argv.includes('--check');
const files = new Map();

const nl = (s) => s.replace(/\n*$/, '\n');
/** Point the memory agent id at this host, as the other builders do. */
const retarget = (s) => s.replace(/SLM_AGENT_ID["']?\s*[:=]\s*["'][^"']+["']/g,
  `SLM_AGENT_ID="${AGENT_ID}"`);
/** Keep every stated version equal to the manifest's. */
function stamp(md) {
  let out = md.replace(/SuperLocalMemory v\d+\.\d+\.\d+/g, `SuperLocalMemory v${VERSION}`);
  const fm = out.match(/^(---\n)([\s\S]*?)(\n---\n)/);
  if (fm && /^version:/m.test(fm[2])) {
    out = out.replace(fm[0], fm[1] + fm[2].replace(/^version:.*$/m, `version: "${VERSION}"`) + fm[3]);
  }
  return out;
}

// skills/, agents/, commands/ — one source, retargeted.
for (const [sub, out] of [['skills', 'skills'], ['agents', 'agents'], ['commands', 'commands']]) {
  const dir = join(SRC, sub);
  if (!existsSync(dir)) continue;
  if (sub === 'skills') {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (!e.isDirectory()) continue;
      const f = join(dir, e.name, 'SKILL.md');
      if (!existsSync(f)) continue;
      files.set(`${out}/${e.name}/SKILL.md`, nl(stamp(retarget(readFileSync(f, 'utf8')))));
    }
  } else {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (!e.isFile() || !e.name.endsWith('.md')) continue;
      files.set(`${out}/${e.name}`, nl(stamp(retarget(readFileSync(join(dir, e.name), 'utf8')))));
    }
  }
}

// hooks/hooks.json — reuse the Codex shape; Antigravity accepts the same schema.
{
  const codexHooks = join(ROOT, 'codex-plugin', 'hooks', 'hooks.json');
  if (existsSync(codexHooks)) {
    files.set('hooks/hooks.json', nl(readFileSync(codexHooks, 'utf8')));
  }
}

// mcp_config.json — how Antigravity starts the server.
files.set('mcp_config.json', JSON.stringify({
  mcpServers: {
    superlocalmemory: {
      command: 'slm',
      args: ['mcp'],
      // Agent id only. A plugin must not narrow the tool set or re-point
      // the store -- both belong to whoever installed it.
      env: { SLM_AGENT_ID: AGENT_ID },
    },
  },
}, null, 2) + '\n');

// plugin.json — the manifest Antigravity reads to list this plugin.
files.set('plugin.json', JSON.stringify({
  name: 'superlocalmemory',
  version: VERSION,
  description:
    'Local-first agent memory with auditable hybrid retrieval. Remember '
    + 'decisions and recall them by asking, entirely on your machine.',
  author: {
    name: 'Qualixar',
    email: 'varun.pratap.bhardwaj@gmail.com',
    url: 'https://github.com/qualixar',
  },
  homepage: 'https://qualixar.com',
  repository: 'https://github.com/qualixar/superlocalmemory',
  license: 'AGPL-3.0-or-later',
  keywords: ['memory', 'mcp', 'agents', 'local-first', 'context-compression'],
  skills: './skills/',
  agents: './agents/',
  commands: './commands/',
  hooks: './hooks/hooks.json',
  mcpServers: './mcp_config.json',
}, null, 2) + '\n');

files.set('_GENERATED.md',
  '# antigravity-plugin/ — GENERATED\n\n'
  + 'Built by `scripts/build-antigravity-plugin.mjs` from `plugin-src/`. '
  + `Version stamped from \`plugin-src/manifest.json\`.\n\nVersion: **${VERSION}**\n\n`
  + 'Do not edit by hand — regenerate instead.\n\n'
  + [...files.keys()].sort().map((p) => `- \`${p}\``).join('\n') + '\n');

let drift = 0;
for (const [rel, content] of files) {
  const path = join(OUT, rel);
  if (check) {
    if (!existsSync(path) || readFileSync(path, 'utf8') !== content) {
      console.error(`drift: ${rel}`);
      drift += 1;
    }
    continue;
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content);
}
if (check) {
  if (drift) { console.error(`antigravity-plugin: ${drift} file(s) out of sync.`); process.exit(2); }
  console.log('antigravity-plugin: in sync.');
} else {
  console.log(`antigravity-plugin built: ${files.size} files, v${VERSION}.`);
}
