const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const src = fs.readFileSync(path.join(root, 'src', 'extension.ts'), 'utf8');

test('license, publisher, name', () => {
  assert.strictEqual(pkg.license, 'MIT');
  assert.strictEqual(pkg.publisher, 'qualixar');
  assert.strictEqual(pkg.name, 'superlocalmemory');
  assert.match(fs.readFileSync(path.join(root, 'LICENSE'), 'utf8'), /^MIT License/);
});

test('engine floor supports the MCP provider API', () => {
  assert.strictEqual(pkg.engines.vscode, '^1.101.0');
});

test('contributed provider id matches the id registered in extension.ts', () => {
  const providers = pkg.contributes.mcpServerDefinitionProviders;
  assert.strictEqual(providers.length, 1);
  const m = src.match(/PROVIDER_ID\s*=\s*'([^']+)'/);
  assert.ok(m, 'PROVIDER_ID constant not found');
  assert.strictEqual(providers[0].id, m[1]);
  assert.ok(src.includes('registerMcpServerDefinitionProvider(PROVIDER_ID'));
});

test('icon exists and entry point is declared', () => {
  assert.ok(fs.existsSync(path.join(root, pkg.icon)));
  assert.strictEqual(pkg.main, './out/extension.js');
});

test('server is launched as "slm mcp" with the vscode agent id', () => {
  assert.ok(src.includes("SLM_AGENT_ID: 'vscode'"));
  assert.ok(src.includes("['mcp']"));
});

test('no network or telemetry code in source', () => {
  assert.ok(!/require\(['"](https?|net|dgram)['"]\)|from ['"](https?|net)['"]|fetch\(/.test(src));
});

// 14 Oct 2026: the .vsix shipped out/extension.js but not out/configured.js, so
// activation failed with "Cannot find module './configured'" in VS Code while
// every unit test (which loads out/ directly) passed.
test('every module extension.js requires is packaged by .vscodeignore', () => {
  const rules = fs.readFileSync(path.join(root, '.vscodeignore'), 'utf8')
    .split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
  const packaged = (file) => rules.reduce((keep, rule) => (rule.startsWith('!')
    ? (path.matchesGlob(file, rule.slice(1)) ? true : keep)
    : (path.matchesGlob(file, rule) ? false : keep)), true);
  const seen = new Set();
  const visit = (rel) => {
    if (seen.has(rel)) return;
    seen.add(rel);
    assert.ok(packaged(rel), `${rel} is required at runtime but left out of the .vsix`);
    const code = fs.readFileSync(path.join(root, rel), 'utf8');
    for (const [, dep] of code.matchAll(/require\("(\.[^"]+)"\)/g)) {
      visit(path.posix.join(path.posix.dirname(rel), dep.endsWith('.js') ? dep : `${dep}.js`));
    }
  };
  visit(pkg.main.replace(/^\.\//, ''));
  assert.ok(seen.size > 1, 'extension.js is expected to require ./configured');
});
