# IDE Setup

SuperLocalMemory works with 17+ IDEs via the Model Context Protocol (MCP). The fastest way to connect is auto-detection.

## Auto-Detection

```bash
slm connect
```

This scans your system for installed IDEs and configures MCP connections automatically. It supports Claude Code, Cursor, VS Code, Windsurf, and more.

After running, restart your IDE to activate the connection.

## Manual Setup by IDE

If auto-detection does not find your IDE, configure it manually.

### Claude Code

Claude Code reads MCP config from `~/.claude.json`:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "superlocalmemory",
      "args": ["--mcp"]
    }
  }
}
```

Restart Claude Code after adding this.

### Cursor

Cursor reads MCP config from `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "superlocalmemory",
      "args": ["--mcp"]
    }
  }
}
```

### VS Code (with Copilot MCP extension)

VS Code reads MCP config from `~/.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "superlocalmemory",
      "args": ["--mcp"]
    }
  }
}
```

Requires the MCP-compatible Copilot extension or similar MCP client.

### Windsurf

Windsurf reads MCP config from `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "superlocalmemory",
      "args": ["--mcp"]
    }
  }
}
```

### Gemini CLI

Gemini CLI reads MCP config from `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "superlocalmemory",
      "args": ["--mcp"]
    }
  }
}
```

### JetBrains IDEs (IntelliJ, PyCharm, WebStorm, etc.)

JetBrains IDEs with AI Assistant read MCP config from the IDE settings:

1. Open Settings > Tools > AI Assistant > MCP Servers
2. Add a new server:
   - Name: `superlocalmemory`
   - Command: `superlocalmemory`
   - Args: `--mcp`
3. Apply and restart

### Continue.dev

Continue reads MCP config from `~/.continue/config.json`. Add to the `mcpServers` section:

```json
{
  "mcpServers": [
    {
      "name": "superlocalmemory",
      "command": "superlocalmemory",
      "args": ["--mcp"]
    }
  ]
}
```

### Zed

Zed reads MCP config from `~/.config/zed/settings.json`:

```json
{
  "language_models": {
    "mcp_servers": {
      "superlocalmemory": {
        "command": "superlocalmemory",
        "args": ["--mcp"]
      }
    }
  }
}
```

## Verifying the Connection

After configuring your IDE, verify the connection:

```bash
slm status
```

You should see your IDE listed under active connections.

In your IDE, try asking your AI: "What do you know about my recent work?" If SuperLocalMemory is connected and has stored memories, the AI will reference them.

## Troubleshooting

**IDE does not detect SuperLocalMemory:**
- Verify installation: `which superlocalmemory` should return a path
- Verify MCP server starts: `superlocalmemory --mcp --test`
- Check your IDE's MCP config file path is correct

**Memories not appearing in AI responses:**
- Check that you have stored memories: `slm list`
- Check the active profile: `slm profile`
- Restart your IDE after config changes

**Multiple IDEs:**
All IDEs share the same memory database. A memory stored via Claude Code is available in Cursor, VS Code, and every other connected IDE.

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
