import * as fs from 'fs';
import * as path from 'path';

/** Strip // and /* *\/ comments and trailing commas: VS Code's mcp.json is JSONC. */
function parseJsonc(text: string): unknown {
  const noComments = text.replace(/\/\*[\s\S]*?\*\/|("(?:\\.|[^"\\])*")|\/\/[^\n]*/g, (m, str) => str ?? '');
  return JSON.parse(noComments.replace(/,(\s*[}\]])/g, '$1'));
}

function isSlmServer(name: string, server: unknown): boolean {
  if (name === 'superlocalmemory') {
    return true;
  }
  if (!server || typeof server !== 'object') {
    return false;
  }
  const { command, args } = server as { command?: unknown; args?: unknown };
  if (!Array.isArray(args) || !args.includes('mcp')) {
    return false;
  }
  const base = typeof command === 'string' ? path.basename(command).replace(/\.(cmd|exe|bat)$/i, '') : '';
  // `slm connect` pins the interpreter: python -m superlocalmemory.cli mcp.
  return base === 'slm' || args.includes('superlocalmemory.cli');
}

/**
 * Whether SLM is already wired into one of these mcp.json files, e.g. by
 * `slm connect vscode-copilot`. The extension then adds no second server.
 */
export function slmAlreadyConfigured(files: string[]): boolean {
  for (const file of files) {
    let data: unknown;
    try {
      data = parseJsonc(fs.readFileSync(file, 'utf8'));
    } catch {
      continue;
    }
    // VS Code uses `servers`; Cursor and Antigravity use `mcpServers`.
    const holder = data as { servers?: Record<string, unknown>; mcpServers?: Record<string, unknown> } | null;
    const servers = holder?.servers ?? holder?.mcpServers;
    if (servers && typeof servers === 'object'
        && Object.entries(servers).some(([name, server]) => isSlmServer(name, server))) {
      return true;
    }
  }
  return false;
}
