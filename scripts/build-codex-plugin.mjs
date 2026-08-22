// Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
// Licensed under AGPL-3.0-or-later - see LICENSE file
//
// build-codex-plugin.mjs — generate the derived parts of codex-plugin/ from the
// SINGLE source in plugin-src/, at parity with plugin/ (Claude) and
// copilot-plugin/ (Copilot).
//
//   node scripts/build-codex-plugin.mjs           # write codex-plugin/
//   node scripts/build-codex-plugin.mjs --check   # verify in sync (exit 2 on drift)
//
// WHY THIS EXISTS
//   codex-plugin/ was maintained by hand. It carried a v4.0.4 footer across six
//   releases, and two of its twelve skills had drifted from their source because
//   somebody deleted a line locally instead of teaching the build about Codex.
//   Anything mechanically derivable belongs here so it cannot drift again.
//
// WHAT IS AND IS NOT DERIVED
//   Derived      skills/, scripts/, _GENERATED.md, and the version footers of
//                AGENTS.md and README.md.
//   Authored     AGENTS.md and README.md bodies (Codex has its own rules
//                document, not a copy of the Claude one), .codex/config.toml,
//                hooks/hooks.json. These are Codex-shaped and have no source
//                to derive from; _GENERATED.md names them so nobody has to guess.

import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CHECK = process.argv.includes('--check');

const SRC_SKILLS = join(ROOT, 'plugin-src', 'skills');
const MANIFEST = join(ROOT, 'plugin-src', 'manifest.json');
const OUT = join(ROOT, 'codex-plugin');

const manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'));
const VERSION = manifest.version;
if (!VERSION) throw new Error('manifest.version missing');

/** The host this plugin runs on, as SLM records it against every memory. */
const AGENT_ID = 'codex';

const nl = (s) => s.replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(/\n*$/, '\n');

/** Rewrite the plugin-wide host identity to this host's. */
function retarget(md) {
  return md.replace(/("SLM_AGENT_ID"\s*:\s*)"[^"]*"/g, `$1"${AGENT_ID}"`);
}

/** Keep every stated version equal to the manifest's. */
function stamp(md) {
  let out = md.replace(/SuperLocalMemory v\d+\.\d+\.\d+/g, `SuperLocalMemory v${VERSION}`);
  const fm = out.match(/^(---\n)([\s\S]*?)(\n---\n)/);
  if (fm && /^version:/m.test(fm[2])) {
    out = out.replace(fm[0], fm[1] + fm[2].replace(/^version:.*$/m, `version: "${VERSION}"`) + fm[3]);
  }
  return out;
}

// --- planned output files: path (relative to OUT) -> content ----------------
const files = new Map();

// 1. skills/<name>/SKILL.md — one source, retargeted to this host.
const skillNames = readdirSync(SRC_SKILLS, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name)
  .sort();
for (const name of skillNames) {
  const src = join(SRC_SKILLS, name, 'SKILL.md');
  if (!existsSync(src)) continue;
  files.set(`skills/${name}/SKILL.md`, nl(stamp(retarget(readFileSync(src, 'utf8')))));
}

// scripts/ is deliberately NOT derived. The Claude launcher requires
// CLAUDE_PLUGIN_ROOT and CLAUDE_PLUGIN_DATA, which Codex never sets, and
// resolves its venv from them; the Codex one resolves from SLM_DATA_DIR.
// Copying the source over it would break the plugin, so they are authored.

// 3. Version footers in the authored documents, without touching their bodies.
for (const rel of ['AGENTS.md', 'README.md']) {
  const abs = join(OUT, rel);
  if (existsSync(abs)) files.set(rel, nl(stamp(readFileSync(abs, 'utf8'))));
}

// 4. _GENERATED.md — says what is derived and what is not.
{
  const derived = [...files.keys()].sort().map((p) => `- \`${p}\``).join('\n');
  files.set('_GENERATED.md',
    '# codex-plugin/ — partly GENERATED\n\n' +
    'Built by `scripts/build-codex-plugin.mjs` from the single source in `plugin-src/`. ' +
    'Version stamped from `plugin-src/manifest.json`.\n\n' +
    `Version: **${VERSION}**\n\n` +
    '## Derived — do not edit by hand\n\n' +
    '| Output | Source |\n|---|---|\n' +
    '| `skills/*/SKILL.md` | `plugin-src/skills/*/SKILL.md`, with `SLM_AGENT_ID` retargeted to `' + AGENT_ID + '` |\n' +
    '| version footers | `plugin-src/manifest.json` |\n\n' +
    derived + '\n\n' +
    '## Authored here — no source to derive from\n\n' +
    'These are Codex-shaped and are maintained in this directory:\n\n' +
    '- `AGENTS.md` body — Codex has its own rules document; it is not a copy of the Claude one.\n' +
    '- `README.md` body\n' +
    '- `scripts/*` — the launcher and venv bootstrap resolve paths from\n' +
    '  `SLM_DATA_DIR`; the Claude versions require `CLAUDE_PLUGIN_ROOT`\n' +
    '  and `CLAUDE_PLUGIN_DATA`, which Codex does not set.\n' +
    '- `.codex/config.toml` — Codex MCP configuration\n' +
    '- `hooks/hooks.json` — Codex hook schema\n\n' +
    'Regenerate: `npm run build:codex-plugin` (or `node scripts/build-codex-plugin.mjs`).\n');
}

// --- write or check ---------------------------------------------------------
let drift = 0;
for (const [rel, content] of files) {
  const abs = join(OUT, rel);
  if (CHECK) {
    if (!existsSync(abs) || readFileSync(abs, 'utf8') !== content) {
      console.error('DRIFT: ' + rel);
      drift++;
    }
  } else {
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, content);
  }
}

if (CHECK) {
  if (drift) {
    console.error(`codex-plugin out of sync (${drift} file(s)). Run: node scripts/build-codex-plugin.mjs`);
    process.exit(2);
  }
  console.log(`codex-plugin in sync (${files.size} files, v${VERSION}).`);
} else {
  console.log(`codex-plugin built: ${files.size} files, v${VERSION}.`);
}
