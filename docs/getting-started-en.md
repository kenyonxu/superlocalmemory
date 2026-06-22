[中文](./getting-started-zh.md) | [English](./getting-started-en.md)

# MSLM Getting Started Guide

> A complete guide from installation to daily use, covering all common pitfalls.
> Based on hands-on experience with MSLM v4.0.

---

## 1. Installation

```bash
pip install mslm-memory
```

> MSLM is also published as `superlocalmemory` — `pip install superlocalmemory` works identically.
> The CLI commands `mslm` and `slm` are fully equivalent.

Verify:

```bash
mslm --version   # Should output 4.1.0+
mslm doctor      # Check dependency integrity
```

---

## 2. First-Time Setup

```bash
mslm setup
```

An interactive wizard lets you choose your operating mode. **Mode A** (zero-cloud, fully local) is recommended for most use cases.

`mslm setup` automatically downloads the embedding model (~500MB), cached at `~/.cache/huggingface/`. If the download fails, see [Network & Proxy](#5-network--proxy) below.

After initialization, the data directory `~/.superlocalmemory/` contains:

| File | Purpose |
|------|---------|
| `config.json` | Global configuration (mode, profile, proxy, etc.) |
| `memory.db` | Main database (facts, entities, graph edges, embeddings) |
| `pending.db` | Async memory queue |
| `learning.db` | Learning signals and evolution data |

---

## 3. Starting the Daemon

MSLM can run on-demand (~3s cold start), but a background daemon is recommended:

```bash
mslm serve start     # Start in background (port 8765)
mslm serve status    # Check status
mslm serve stop      # Stop
mslm restart         # One-click restart (kill zombies + cleanup + start + health check)
```

Verify after startup:

```bash
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
# {"status":"ok","pid":12345,"engine":"initialized","version":"4.1.0"}
```

### Built-in Daemon Capabilities

| Component | Function |
|-----------|----------|
| Materializer | Polls pending.db every 2s, writes async memories to main DB |
| HealthMonitor | Monitors memory/heartbeat/engine liveness/zombie process reaping |
| Maintenance | Runs Langevin/Fisher/Sheaf maintenance every 30 min + embedding backfill + graph pruning |
| Embed worker | Independent child process loading ONNX model for embedding generation |
| Entity backfill | Auto-backfills entities and graph edges for old memories on daemon startup |

---

## 4. Connecting Hermes Agent

### 4.1 Hermes MemoryProvider Plugin (Recommended)

MSLM ships with a native Hermes Agent MemoryProvider plugin — **no MCP subprocess**, zero extra latency, auto-loaded on Hermes startup:

```yaml
# ~/.hermes/profiles/<name>/config.yaml
memory:
  provider: superlocalmemory
  superlocalmemory:
    mslm_profile: my_profile        # MSLM profile name
    prefetch_limit: 10              # auto-injected memories per turn
    include_global: true            # include global-scope memories
    include_shared: true            # include shared memories
```

No extra commands needed — Hermes auto-loads the plugin on startup, providing three native tools: `slm_recall`, `slm_remember`, `slm_status`, with full three-tier scope support.

> See [Hermes Agent Integration Guide](hermes-agent-guide-en.md) for details.

### 4.2 MCP Method (Alternative)

Compatible with Claude Code, Cursor, Windsurf, and any MCP client.

#### Register

```bash
hermes mcp add mslm --command mslm --args mcp
```

With environment variables:

```bash
hermes mcp add mslm \
  --command mslm --args mcp \
  --env SLM_MODE=a
```

#### Manage

```bash
hermes mcp list            # List registered MCP servers
hermes mcp test mslm       # Test connectivity
hermes mcp remove mslm     # Remove
```

Run `/reload-mcp` after config changes — no restart needed.

> **Note**: Avoid `SLM_DATA_DIR`. MSLM defaults to `~/.superlocalmemory/`. Different directories = invisible databases.

### 4.3 Core Tools Quick Reference

| Tool | Function | Key Parameters |
|------|----------|---------------|
| `remember` / `slm_remember` | Store memory | `content` (required), `scope`, `shared_with` |
| `recall` / `slm_recall` | Semantic retrieval | `query` (required), `limit` |
| `search` | Keyword search | `query` |
| `status` / `slm_status` | System status | — |

Three-tier scope:

| `scope` | Visibility | Use Case |
|---------|-----------|----------|
| `personal` | Self only | Personal preferences, private info |
| `global` | All Agents | Shared knowledge, team conventions |
| `shared` | Specified Agent list | Collaborative information |

```yaml
# ~/.hermes/profiles/<name>/config.yaml
memory:
  provider: superlocalmemory
  superlocalmemory:
    mslm_profile: my_profile        # MSLM profile name
    prefetch_limit: 10              # auto-injected memories per turn
```

Once configured, no `hermes mcp add` needed — Hermes auto-loads the plugin on startup, providing three native tools: `slm_recall`, `slm_remember`, `slm_status`.

> See [Hermes Agent Integration Guide](hermes-agent-guide-en.md) for details.

---

## 5. Network & Proxy

### Background

The embed worker child process needs to reach `huggingface.co` to verify model files. In restricted networks, direct access may be unavailable, and mirror sites (e.g., `hf-mirror.com`) may have SSL compatibility issues. MSLM supports proxy configuration via `config.json`.

### Configuring a Proxy

```bash
mslm config set proxy.http http://127.0.0.1:7890
mslm config set proxy.https http://127.0.0.1:7890
```

Configuration is persisted to `~/.superlocalmemory/config.json` and read automatically by the embed worker at startup. No shell environment variables needed.

### Adjusting Embed Worker Timeout

First-time ONNX model cold loading may take 2-3 minutes. To increase the timeout:

```bash
# Set at daemon startup (seconds, default 180)
SLM_EMBED_RESPONSE_TIMEOUT=300 mslm restart
```

### Verifying Embed Worker Status

```bash
ps aux | grep embedding_worker
tail -f ~/.superlocalmemory/logs/daemon-error.log | grep -i embed
```

Seeing `Embedding worker pre-warmed (ONNX model loaded)` indicates successful loading.

---

## 6. Daily Maintenance

```bash
mslm status          # Mode, profile, DB size
mslm health          # Math layer health (Fisher-Rao, Sheaf, Langevin)
mslm list -n 20      # Last 20 memories
mslm recall "query"  # Semantic retrieval
mslm dashboard       # Web dashboard (http://localhost:8765)
```

Memory housekeeping:

```bash
mslm consolidate --cognitive   # Deduplication & merging
mslm decay --execute           # Forgetting curve decay
mslm entity list               # View entities
```

---

## 7. Data Directory Management

### 7.1 Location

Default: `~/.superlocalmemory/`. **Do not** change casually.

### 7.2 Migration

Use symlinks rather than `SLM_DATA_DIR`:

```bash
mslm serve stop
mv ~/.superlocalmemory /data/mslm-data
ln -s /data/mslm-data ~/.superlocalmemory
mslm serve start
```

### 7.3 Backup

```bash
mslm serve stop
cp -r ~/.superlocalmemory ~/.superlocalmemory.bak.$(date +%Y%m%d)
mslm serve start
```

---

## 8. Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Embed worker repeatedly times out (SIGKILL) | Network unreachable or timeout too short | Configure proxy + `SLM_EMBED_RESPONSE_TIMEOUT=300` |
| Poor retrieval quality | NULL embedding columns | Wait for maintenance backfill (30 min interval), or restart daemon to accelerate |
| MCP memories "disappear" | Inconsistent data directory | Always use `~/.superlocalmemory/`, don't set `SLM_DATA_DIR` |
| Empty knowledge graph | Old memories lack entities | Auto-backfilled on daemon restart (idempotent, cross-profile) |
| Recall returns no results without errors | Embeddings not ready, semantic channel skipped | 6/7 channels work normally; wait for embed worker |
| `mslm doctor` reports errors | Missing dependencies | `pip install mslm-memory[dev]` |
| Port 8765 already in use | Zombie uvicorn from old daemon | `mslm restart` auto-cleans |

### Key Log Locations

```bash
~/.superlocalmemory/logs/daemon.log        # Main log
~/.superlocalmemory/logs/daemon-error.log  # Errors (embed worker timeouts, etc.)
~/.superlocalmemory/logs/daemon.json.log   # Structured JSON (HealthMonitor)
```

---

## 9. Recommended Workflow

```bash
# One-time setup
pip install mslm-memory                        # 1. Install
mslm setup                              # 2. Initialize (choose Mode A)
mslm config set proxy.http ...          # 3. Proxy (if needed)
mslm config set proxy.https ...
SLM_EMBED_RESPONSE_TIMEOUT=300 \        # 4. Start daemon
  mslm serve start

# Daily use
mslm serve status                       # Check online status
mslm dashboard                          # Optional: Web panel
```

After setup, you only need `mslm serve start` daily. For auto-start on boot, configure a systemd service.

---

## About MSLM

MSLM (Multi-Scope Local Memory) is an independent distribution of SuperLocalMemory, focused on multi-scope memory collaboration. The core memory engine is powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory) (AGPL-3.0).

See [Multi-Scope Memory](multi-scope-memory-en.md) and [Docs Index](INDEX-en.md) for more.
