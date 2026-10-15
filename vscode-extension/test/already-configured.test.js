const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// Compiled by `npm test` (tsc) before this runs; the module imports nothing from vscode.
const { slmAlreadyConfigured } = require('../out/configured.js');

function file(content) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'slm-ext-'));
  const p = path.join(dir, 'mcp.json');
  fs.writeFileSync(p, typeof content === 'string' ? content : JSON.stringify(content));
  return p;
}

test('an mcp.json written by `slm connect vscode-copilot` counts as configured', () => {
  const p = file({ servers: { superlocalmemory: { type: 'stdio', command: 'slm', args: ['mcp'] } } });
  assert.strictEqual(slmAlreadyConfigured([p]), true);
});

test('an SLM server under another name still counts (command slm, arg mcp)', () => {
  const p = file({ servers: { memory: { command: '/opt/homebrew/bin/slm', args: ['mcp'] } } });
  assert.strictEqual(slmAlreadyConfigured([p]), true);
});

test('other servers, missing files and broken JSON do not', () => {
  const other = file({ servers: { github: { command: 'gh-mcp', args: [] } } });
  const broken = file('{ not json');
  assert.strictEqual(slmAlreadyConfigured([other, broken, '/nope/mcp.json']), false);
});

test('comments and trailing commas (VS Code JSONC) are tolerated', () => {
  const p = file('{\n // mine\n "servers": { "superlocalmemory": { "command": "slm", "args": ["mcp"], }, },\n}');
  assert.strictEqual(slmAlreadyConfigured([p]), true);
});

test('Antigravity configs (mcpServers) count, so that editor gets no second SLM', () => {
  const cursor = file({ mcpServers: { superlocalmemory: { command: 'slm', args: ['mcp'] } } });
  assert.strictEqual(slmAlreadyConfigured([cursor]), true);
});

test('the pinned interpreter form written by `slm connect` counts under any name', () => {
  const pinned = file({ mcpServers: { memory: {
    command: '/Users/me/.superlocalmemory/venv/bin/python',
    args: ['-m', 'superlocalmemory.cli', 'mcp'],
  } } });
  assert.strictEqual(slmAlreadyConfigured([pinned]), true);
});

test('a Copilot CLI mcp-config.json written by `slm connect copilot-cli` counts', () => {
  const p = file({ mcpServers: { superlocalmemory: { type: 'local', command: 'slm', args: ['mcp'], tools: ['*'] } } });
  assert.strictEqual(slmAlreadyConfigured([p]), true);
});
