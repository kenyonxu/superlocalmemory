# SuperLocalMemory for VS Code

SuperLocalMemory (SLM) is local-first long-term memory for AI agents. This extension registers SLM as an MCP server in VS Code, so agent chat can remember and recall across sessions without you editing any MCP configuration by hand.

## What it does

When VS Code starts, the extension tells VS Code about one MCP server called "SuperLocalMemory". VS Code starts it on demand by running `slm mcp` from the engine you installed. The server is launched with the environment variable `SLM_AGENT_ID=vscode`, so memories written from VS Code are attributed to that agent.

The extension itself contains no memory logic. It finds the `slm` command and hands it to VS Code.

## Requirements

Install the SuperLocalMemory engine first. The extension does not install it for you.

```
npm install -g superlocalmemory
```

or

```
pipx install superlocalmemory
```

You need VS Code 1.101 or later, which is the first release with the extension MCP API. The extension looks for `slm` on your PATH and then in common install folders (`~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, and the npm global folder on Windows). If it cannot find the command, it shows a message with an "Install instructions" button that opens https://superlocalmemory.com. After installing, return to VS Code; if the server does not appear, run "Developer: Reload Window".

To see or manage the server, open the Command Palette and run "MCP: List Servers".

## Data disclosure

All memory stays on your machine. The extension makes no network calls, collects no telemetry, and sends nothing anywhere. The only thing it does is start a local process, `slm mcp`, and the engine stores its data in your user profile on your own disk. The "Install instructions" button opens a web page in your browser only when you click it.

## License

This extension is MIT licensed (see LICENSE). It is a launcher for the separately installed SuperLocalMemory engine, which is licensed under AGPL with a commercial option. Installing this extension does not install, bundle, or modify the engine.

## Links

- Website: https://superlocalmemory.com
- Source and issues: https://github.com/qualixar/superlocalmemory
