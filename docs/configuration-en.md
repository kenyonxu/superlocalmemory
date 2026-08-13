[中文](./configuration-zh.md) | [English](./configuration-en.md)

# MSLM Configuration

> Control how MSLM stores, retrieves, and processes your memories.

---

## Three Operating Modes

MSLM runs in one of three modes. You pick the trade-off between privacy and power.

| Mode | What it does | Needs API key? | Data leaves your machine? |
|------|-------------|:--------------:|:-------------------------:|
| **A: Zero-Cloud** | Math-based retrieval. No LLM calls. | No | Never |
| **B: Local LLM** | Mode A + a local LLM via Ollama. | No | Never |
| **C: Cloud LLM** | Mode B + a cloud LLM for maximum recall quality. | Yes | Yes (queries only) |

### Check your current mode

```bash
mslm mode
```

### Switch modes

```bash
mslm mode a    # Zero-cloud (default)
mslm mode b    # Local LLM
mslm mode c    # Cloud LLM
```

Switching modes takes effect immediately. No data is lost.

### Mode A: Zero-Cloud (Default)

Memory-content operations run locally. Retrieval combines semantic similarity, keyword search, entity graph, temporal context, and associative producers with mathematical scoring. Optional enrichment, cloud backup, and connector features are networked, off by default, and explicitly opt-in.

Best for: privacy-sensitive work, air-gapped environments, and EU AI Act–aligned local deployments (legal compliance remains a deployment-context assessment).

### Mode B: Local LLM

Everything from Mode A, plus a local LLM (via Ollama) that improves recall by understanding query intent and reranking results.

**Setup:**

```bash
# Install Ollama (if not already installed)
brew install ollama          # macOS
curl -fsSL https://ollama.com/install.sh | sh  # Linux

# Pull a model
ollama pull llama3.2

# Switch to Mode B
mslm mode b
```

Best for: developers who want better recall without sending data to the cloud.

### Mode C: Cloud LLM

Everything from Mode B, plus a cloud LLM for cross-encoder reranking and agentic multi-round retrieval. Highest recall quality.

**Setup:**

```bash
mslm mode c
mslm provider set openai
```

You will be prompted for your API key (stored locally in your config file, never transmitted except to the provider you choose).

Best for: maximum recall quality when privacy constraints allow cloud calls.

---

## Provider Configuration

Mode C supports multiple LLM providers.

### Set your provider

```bash
mslm provider           # Show current provider
mslm provider set       # Interactive provider selector
```

### Supported providers

| Provider | Command | Env variable |
|----------|---------|-------------|
| OpenAI | `mslm provider set openai` | `OPENAI_API_KEY` |
| Anthropic | `mslm provider set anthropic` | `ANTHROPIC_API_KEY` |
| Azure OpenAI | `mslm provider set azure` | `AZURE_OPENAI_API_KEY` |
| Ollama (local) | `mslm provider set ollama` | None needed |
| OpenRouter | `mslm provider set openrouter` | `OPENROUTER_API_KEY` |

### Set API keys

You can set keys interactively or via environment variables:

```bash
# Interactive (stored in config file)
mslm provider set openai
# Prompts: Enter your OpenAI API key: sk-...

# Via environment variable (takes precedence)
export OPENAI_API_KEY="sk-..."
```

---

### Hermes Agent MemoryProvider Configuration

MSLM ships with a native Hermes Agent plugin. Configure it in your Hermes `config.yaml`:

```yaml
# ~/.hermes/profiles/<name>/config.yaml
memory:
  provider: superlocalmemory          # Enable MSLM MemoryProvider
  superlocalmemory:
    mslm_profile: default             # MSLM profile to use
    prefetch_limit: 10                # auto-injected memories per turn (default 10)
    include_global: true              # include global-scope memories
    include_shared: true              # include shared memories
    mslm_data_dir: ""                 # custom data dir (empty = default)
```

Once configured, Hermes auto-loads the plugin on startup — no `hermes mcp add` needed.

> See [Hermes Agent Integration Guide](hermes-agent-guide-en.md)

---

## Config File

All settings live in:

```
~/.superlocalmemory/config.json
```

### Example config

```json
{
  "mode": "a",
  "profile": "default",
  "provider": {
    "name": "openai",
    "model": "gpt-4o-mini",
    "api_key_env": "OPENAI_API_KEY"
  },
  "auto_capture": true,
  "auto_recall": true,
  "embedding_model": "all-MiniLM-L6-v2",
  "max_recall_results": 10,
  "retention": {
    "default_policy": "indefinite"
  },
  "scope_weights": {
    "personal": 1.0,
    "shared": 0.7,
    "global": 0.5
  }
}
```

### Key settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mode` | `"a"` | Operating mode: `a`, `b`, or `c` |
| `profile` | `"default"` | Active memory profile |
| `auto_capture` | `true` | Automatically store decisions and context |
| `auto_recall` | `true` | Automatically inject relevant memories |
| `embedding_model` | `"all-MiniLM-L6-v2"` | Sentence transformer for semantic search |
| `max_recall_results` | `10` | Maximum memories returned per query |
| `scope_weights` | `{"personal": 1.0, "shared": 0.7, "global": 0.5}` | Three-tier scope RRF fusion weights |

---

## Environment Variables

These override config file settings when set:

| Variable | Purpose |
|----------|---------|
| `SLM_MODE` | Override operating mode |
| `SLM_PROFILE` | Override active profile |
| `SLM_DATA_DIR` | Override data directory (default: `~/.superlocalmemory/`) |
| `OPENAI_API_KEY` | OpenAI API key for Mode C |
| `ANTHROPIC_API_KEY` | Anthropic API key for Mode C |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key for Mode C |
| `OPENROUTER_API_KEY` | OpenRouter API key for Mode C |

---

## Database Location

All data is stored locally in:

```
~/.superlocalmemory/memory.db    # SQLite database
~/.superlocalmemory/config.json  # Configuration
~/.superlocalmemory/backups/     # Automatic backups
```

To use a custom location:

```bash
export SLM_DATA_DIR="/path/to/your/data"
```

---

## Multi-Machine Mesh

| Variable | Default | Description |
|---|---|---|
| `SLM_MESH_PEER_URL` | unset | Full URL of remote MSLM instance (e.g., `http://192.168.1.100:8765`) |
| `SLM_MESH_SHARED_SECRET` | unset | Shared bearer token — same on both machines. Required when `SLM_MESH_HOST` is not localhost. |
| `SLM_MESH_HOST` | `127.0.0.1` | IP to bind this machine's mesh listener |
| `SLM_MESH_WS_PORT` | `7900` | Port used for mDNS service announcement |
| `SLM_MESH_DISCOVERY` | `on` | Set to `off` to disable mDNS auto-discovery |

See [SLM Multi-Machine Setup](slm/multi-machine.md) for the full guide.

---

## References

- [Getting Started](getting-started-en.md) — From installation to daily use
- [Multi-Scope Memory](multi-scope-memory-en.md) — scope_weights and other scope configuration
- [SLM Technical Docs](slm/INDEX.md) — Upstream technical reference

---

*MSLM (Multi-Scope Local Memory) — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
