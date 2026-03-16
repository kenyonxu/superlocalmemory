# Getting Started

Get SuperLocalMemory running in under 5 minutes.

## Prerequisites

- **Node.js** 18 or later
- **npm** (comes with Node.js)
- Any supported IDE (Claude Code, Cursor, VS Code, Windsurf, etc.)

## Install

```bash
npm install -g superlocalmemory
```

## Setup Wizard

Run the setup wizard to configure your IDE connections:

```bash
slm setup
```

The wizard will:
1. Detect your installed IDEs
2. Configure MCP connections for each one
3. Create your default memory profile
4. Verify everything works

## Your First Memory

Store something:

```bash
slm remember "Our API uses JWT tokens with 24-hour expiry. Refresh tokens last 30 days."
```

Recall it later:

```bash
slm recall "JWT token expiry"
```

You should see the stored memory returned with a relevance score.

## Your First Search

Search is more flexible than recall — it finds partial matches and related memories:

```bash
slm search "authentication"
```

This returns all memories related to authentication, even if the word "authentication" was never used.

## Verify Installation

Check that everything is running correctly:

```bash
slm status
```

You should see:
- Database: connected
- Memories: count of stored memories
- Profile: your active profile name
- Mode: current operating mode (A, B, or C)

## Try Auto-Recall in Your IDE

Open your IDE (e.g., Claude Code) and start a conversation. SuperLocalMemory automatically injects relevant context before your AI responds. Try asking about something you stored — your AI will know about it without you re-explaining.

## Next Steps

- [Modes Explained](Modes-Explained) — Understand the three operating modes
- [CLI Reference](CLI-Reference) — Full command reference
- [IDE Setup](IDE-Setup) — Configure additional IDEs
- [Auto-Memory](Auto-Memory) — How auto-capture works

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
