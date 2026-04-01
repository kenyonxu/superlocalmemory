# Installation

SuperLocalMemory V3 installs via **npm**, **pip**, or **git clone**. All three methods give you the same product — choose whichever fits your workflow.

> **No desktop app (DMG/EXE) for V3.** V3 is a CLI + MCP server, not a GUI application. The V2 desktop installers are deprecated. Use `slm dashboard` for the web UI.

## Prerequisites

| Requirement | Version | Check |
|:-----------|:--------|:------|
| **Python** | 3.11+ | `python3 --version` |
| **Node.js** (for npm install) | 14+ | `node --version` |

Python 3.11+ is required for the V3 engine. Node.js is only needed if you install via npm.

---

## Method 1: npm (Recommended)

One command installs everything — CLI, Python dependencies, and MCP server.

```bash
npm install -g superlocalmemory
```

This automatically:
- Installs the V3 engine and CLI (`slm` command)
- Auto-installs Python dependencies (numpy, scipy, networkx, sentence-transformers, torch)
- Creates data directory at `~/.superlocalmemory/`
- **Auto-installs Claude Code hooks** (v3.3.6+) — memory lifecycle is fully automatic
- Detects V2 installations and guides migration

**That's it.** Open Claude Code and memory just works. No `slm setup` or `slm init` needed for auto-memory.

For optional configuration:

```bash
slm setup     # Interactive wizard — choose Mode A/B/C, configure provider
slm warmup    # Pre-download embedding model (~500MB, one-time)
```

> **`slm warmup` is optional.** If you skip it, the model downloads automatically on your first `slm remember` or `slm recall`.

> **Don't want auto-hooks?** Run `slm hooks remove` to opt out. Re-enable anytime with `slm hooks install`.

### Verify

```bash
slm status
```

You should see:
```
SuperLocalMemory V3
  Mode: A
  Provider: none
  Base dir: /home/you/.superlocalmemory
  Database: /home/you/.superlocalmemory/memory.db
```

---

## Method 2: pip

```bash
pip install superlocalmemory
```

Then run:

```bash
slm setup
slm warmup    # Optional — pre-download embedding model
slm status    # Verify
```

---

## Method 3: Git Clone (for development or air-gapped environments)

```bash
git clone https://github.com/qualixar/superlocalmemory.git
cd superlocalmemory
pip install -e .
```

Then:

```bash
slm setup
slm warmup
slm status
```

---

## What Gets Installed

| Component | Size | When |
|:----------|:-----|:-----|
| Core math libraries (numpy, scipy, networkx) | ~50MB | During install |
| Search engine (sentence-transformers, einops, torch) | ~200MB | During install |
| Embedding model (nomic-ai/nomic-embed-text-v1.5, 768d) | ~500MB | First use or `slm warmup` |

**Total disk footprint:** ~750MB after first use (mostly PyTorch + embedding model).

**RAM usage:** ~500-800MB peak during embedding model load, ~20-50MB steady state. CPU-only — no GPU required.

> **If any dependency fails during install**, the installer prints the exact `pip install` command to fix it. BM25 keyword search works even without embeddings — you're never fully blocked.

---

## Platform Notes

### macOS (Apple Silicon + Intel)

```bash
npm install -g superlocalmemory
slm setup
```

Works out of the box. Python 3.11+ is included with Homebrew (`brew install python@3.12`) or available from python.org.

### Linux (Ubuntu/Debian/Fedora)

```bash
npm install -g superlocalmemory
slm setup
```

Ensure Python 3.11+ is installed: `sudo apt install python3.11` (Ubuntu) or `sudo dnf install python3.11` (Fedora).

### Windows

```bash
npm install -g superlocalmemory
slm setup
```

Requires Python 3.11+ from [python.org](https://www.python.org/downloads/). Add Python to PATH during installation.

---

## MCP Integration (IDE Setup)

After installing, connect to your AI IDE:

```json
{
  "mcpServers": {
    "superlocalmemory": {
      "command": "slm",
      "args": ["mcp"]
    }
  }
}
```

Or auto-configure all detected IDEs:

```bash
slm connect        # Configure all detected IDEs
slm connect --list # See which IDEs are configured
```

See [IDE Setup](IDE-Setup) for per-IDE instructions.

---

## Upgrading from V2

If you have V2 (2.8.6 or earlier) installed:

```bash
npm install -g superlocalmemory    # Installs V3 alongside V2
slm migrate                        # Migrates V2 data to V3 schema
```

V3 is a complete architectural reinvention — new mathematical engine, new retrieval pipeline, new storage schema. Your existing data is preserved. A backup is created automatically before migration.

See [Migration from V2](Migration-from-V2) for the full guide.

---

## Troubleshooting

### `slm: command not found`
- **npm install:** Make sure npm global bin is in your PATH. Run `npm bin -g` to find the location.
- **pip install:** Make sure Python scripts directory is in your PATH.

### `ModuleNotFoundError: No module named 'superlocalmemory'`
- Ensure Python 3.11+ is the default: `python3 --version`
- Reinstall: `pip install --force-reinstall superlocalmemory`

### Embedding model fails to download
- Check internet connection
- Try manual warmup: `slm warmup`
- If behind a proxy, set `HTTP_PROXY` and `HTTPS_PROXY` environment variables

### Permission errors on macOS/Linux
- Use `npm install -g superlocalmemory` (not sudo)
- If npm global directory needs permissions: `npm config set prefix ~/.npm-global` and add `~/.npm-global/bin` to PATH

---

## Next Steps

- [Quick Start Tutorial](Quick-Start-Tutorial) — Your first memory in 2 minutes
- [Modes Explained](Modes-Explained) — Choose between A (zero-cloud), B (local Ollama), C (full power)
- [CLI Reference](CLI-Reference) — All 14 commands with examples

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
