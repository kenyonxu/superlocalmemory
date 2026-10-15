import * as vscode from 'vscode';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { slmAlreadyConfigured } from './configured';

const PROVIDER_ID = 'qualixar.superlocalmemory';
const INSTALL_URL = 'https://superlocalmemory.com';

/** Directories checked after PATH, because GUI-launched editors often lack the shell PATH. */
function fallbackDirs(): string[] {
  const home = os.homedir();
  if (process.platform === 'win32') {
    const appData = process.env.APPDATA ?? path.join(home, 'AppData', 'Roaming');
    return [path.join(appData, 'npm'), path.join(home, '.local', 'bin')];
  }
  return [
    path.join(home, '.local', 'bin'),
    '/opt/homebrew/bin',
    '/usr/local/bin',
    path.join(home, '.npm-global', 'bin'),
    path.join(home, '.volta', 'bin'),
  ];
}

function isExecutable(file: string): boolean {
  try {
    if (!fs.statSync(file).isFile()) {
      return false;
    }
    if (process.platform !== 'win32') {
      fs.accessSync(file, fs.constants.X_OK);
    }
    return true;
  } catch {
    return false;
  }
}

/** Returns the absolute path of the `slm` command, or undefined when it is not installed. */
export function resolveSlm(): string | undefined {
  const names = process.platform === 'win32' ? ['slm.cmd', 'slm.exe', 'slm.bat'] : ['slm'];
  const pathDirs = (process.env.PATH ?? '').split(path.delimiter).filter(Boolean);
  for (const dir of [...pathDirs, ...fallbackDirs()]) {
    for (const name of names) {
      const candidate = path.join(dir, name);
      if (isExecutable(candidate)) {
        return candidate;
      }
    }
  }
  return undefined;
}

function buildDefinition(slmPath: string, version: string): vscode.McpStdioServerDefinition {
  const env = { SLM_AGENT_ID: 'vscode' };
  // Windows .cmd shims cannot be spawned without a shell.
  const needsShell = process.platform === 'win32' && /\.(cmd|bat)$/i.test(slmPath);
  return new vscode.McpStdioServerDefinition(
    'SuperLocalMemory',
    needsShell ? 'cmd.exe' : slmPath,
    needsShell ? ['/c', slmPath, 'mcp'] : ['mcp'],
    env,
    version,
  );
}

/**
 * The MCP config files this editor reads: its workspace and user mcp.json, and
 * in Antigravity IDE its own MCP file, where `slm connect antigravity` may
 * already have wired SLM. Another editor's files are never read. (Cursor
 * stubs registerMcpServerDefinitionProvider, so this extension adds nothing
 * there; Cursor uses the Cursor plugin or `slm connect cursor`.)
 */
function mcpJsonFiles(context: vscode.ExtensionContext): string[] {
  const home = os.homedir();
  const app = vscode.env.appName.toLowerCase();
  const roots = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
  // globalStorageUri is <User>/globalStorage/<extension id>; mcp.json sits in <User>.
  const files = [
    ...roots.map((r) => path.join(r, '.vscode', 'mcp.json')),
    path.join(path.dirname(path.dirname(context.globalStorageUri.fsPath)), 'mcp.json'),
  ];
  if (!app.includes('antigravity')) {
    // VS Code's chat also discovers GitHub Copilot CLI's servers, and
    // `slm connect copilot-cli` writes SLM there for the Agents window.
    const copilotHome = process.env.COPILOT_HOME ?? path.join(home, '.copilot');
    files.push(path.join(copilotHome, 'mcp-config.json'));
  }
  if (app.includes('antigravity')) {
    files.push(
      ...roots.map((r) => path.join(r, '.agents', 'mcp_config.json')),
      path.join(home, '.gemini', 'config', 'mcp_config.json'),
    );
  }
  return files;
}

let warned = false;

async function showMissingMessage(): Promise<void> {
  if (warned) {
    return;
  }
  warned = true;
  const open = 'Install instructions';
  const choice = await vscode.window.showWarningMessage(
    'SuperLocalMemory: the "slm" command was not found. Install the engine (npm install -g superlocalmemory, or pipx install superlocalmemory), then reload the window.',
    open,
  );
  if (choice === open) {
    await vscode.env.openExternal(vscode.Uri.parse(INSTALL_URL));
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const changed = new vscode.EventEmitter<void>();
  const version = String(context.extension.packageJSON?.version ?? '0.0.0');

  context.subscriptions.push(
    changed,
    vscode.lm.registerMcpServerDefinitionProvider(PROVIDER_ID, {
      onDidChangeMcpServerDefinitions: changed.event,
      provideMcpServerDefinitions: async () => {
        // Already wired by `slm connect vscode-copilot` (workspace or user
        // mcp.json): add no second SLM server beside it.
        if (slmAlreadyConfigured(mcpJsonFiles(context))) {
          return [];
        }
        const slm = resolveSlm();
        if (!slm) {
          void showMissingMessage();
          return [];
        }
        return [buildDefinition(slm, version)];
      },
      resolveMcpServerDefinition: async (server) => server,
    }),
    vscode.window.onDidChangeWindowState((s) => {
      // Re-probe when the user returns to the window after installing the engine.
      if (s.focused && warned && resolveSlm()) {
        warned = false;
        changed.fire();
      }
    }),
  );
}

export function deactivate(): void {}
