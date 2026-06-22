<p align="center">
  <h1 align="center">MSLM</h1>
  <p align="center"><strong>Multi-Scope Local Memory</strong><br/><em>The multi-scope local memory system that keeps AI from forgetting.</em></p>
  <p align="center"><code>v4.1.0</code> — Persistent memory for Claude Code, Cursor, Hermes Agent, and any MCP-compatible AI client.</p>
</p>

<p align="center">
  <code>Three-Tier Scope</code> &nbsp;·&nbsp; <code>Fully Local</code> &nbsp;·&nbsp; <code>MCP Native</code> &nbsp;·&nbsp; <code>Math-Driven Retrieval</code>
</p>

<p align="center">
  <a href="https://pypi.org/project/mslm-memory/"><img src="https://img.shields.io/badge/PyPI-mslm--memory-blue?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"/></a>
  <a href="https://www.npmjs.com/package/mslm-memory"><img src="https://img.shields.io/badge/npm-mslm--memory-red?style=for-the-badge&logo=npm&logoColor=white" alt="npm"/></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=for-the-badge" alt="AGPL v3"/></a>
  <a href="#eu-ai-act-compliance"><img src="https://img.shields.io/badge/EU_AI_Act-Design_Compliant-brightgreen?style=for-the-badge" alt="EU AI Act Design Compliant"/></a>
</p>

---

## Why MSLM?

Every hosted AI memory platform sends your data to cloud LLMs by default. After **August 2, 2026**, those cloud paths become a compliance problem under the EU AI Act.

MSLM takes a fundamentally different approach: **mathematics instead of cloud compute.** Three techniques from differential geometry, algebraic topology, and stochastic analysis replace the work other systems need LLMs to do — similarity scoring, contradiction detection, and lifecycle management. The result: high-quality local memory retrieval on CPU — no Docker, no graph DB, no API keys.

**MSLM's key differentiator**: built on the SuperLocalMemory engine with added **multi-scope architecture** (personal / shared / global), enabling multiple AI Agents to maintain independent memories while sharing team knowledge.

### Three-Tier Scope Memory

| Scope | Visibility | Use Case |
|-------|-----------|----------|
| **Personal** | Self only | Private preferences, confidential info |
| **Shared** | Specified Agents | Team collaboration, domain sharing |
| **Global** | All Agents | Common knowledge, technical entities |

Retrieval automatically queries all three scopes in parallel, merging results via weighted RRF fusion for optimal ranking.

---

## Quick Install

```bash
pip install mslm-memory
```

> MSLM is also published as `superlocalmemory` — `pip install superlocalmemory` works identically.

```bash
mslm setup          # Interactive setup wizard
mslm serve start    # Start background daemon
```

Connect in Hermes Agent:

```bash
hermes mcp add mslm --command mslm --args mcp
```

---

## Core Features

### 🧠 Multi-Scope Architecture
- **Three-tier scope**: personal / shared / global — flexible memory visibility control
- **Global canonical entities**: React, Python, and other tech entities shared by all Agents
- **Auto domain tag matching**: 48 built-in entity→domain mappings with cross-Agent auto-sharing

### 🔬 Math-Driven Retrieval
- **7-channel hybrid retrieval**: semantic vectors + BM25 keywords + entity graph + temporal awareness + Hopfield associative + profile filtering + graph spreading activation
- **Weighted RRF fusion**: intelligent cross-scope result ranking
- **Fisher-Rao geodesic distance**: information-geometric similarity scoring
- **Sheaf consistency**: automatic contradiction detection across memories
- **Langevin lifecycle**: automatic memory strengthening and decay

### 🏠 Fully Local
- **Zero cloud dependency**: all data stored in local SQLite
- **CPU-only**: no GPU required, no Docker needed
- **EU AI Act compliant**: your data never leaves your machine

### 🔌 MCP Native
- **33 core tools**: memory storage, semantic retrieval, entity management, session tracking
- **Claude Code / Cursor / Windsurf**: works with any MCP-compatible client

### 🧩 Hermes MemoryProvider Plugin
- **Native integration**: no MCP subprocess, zero extra latency
- **Auto context injection**: relevant memories prefetched each turn
- **Scope-aware**: `slm_recall` / `slm_remember` / `slm_status` natively support three-tier scope
- **Zero config**: one YAML line, auto-loaded on Hermes startup

### 📊 Web Dashboard
- Memory network graph visualization
- Retrieval statistics and performance monitoring
- System health checks (Fisher-Rao / Sheaf / Langevin)
- Memory maintenance operations

---

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started-en.md) | Complete guide from installation to daily use |
| [Multi-Scope Memory](docs/multi-scope-memory-en.md) | Three-tier scope concepts and usage |
| [Hermes Agent Integration](docs/hermes-agent-guide-en.md) | MCP protocol integration guide |
| [Configuration Guide](docs/configuration-en.md) | Operating modes, providers, environment variables |
| [Memory Import Guide](docs/memory-import-guide-en.md) | Bulk import from external systems |
| [Docs Index](docs/INDEX-en.md) | Full documentation navigation |
| [Technical Reference](docs/slm/INDEX.md) | Upstream SLM architecture and API docs |

---

## Relationship to SuperLocalMemory

MSLM (Multi-Scope Local Memory) is an independent distribution of SuperLocalMemory, focused on multi-scope memory collaboration:

- **Same engine**: core retrieval, storage, and math layers are entirely SLM-based
- **Added collaboration layer**: three-tier scope, global entities, cross-Agent knowledge sharing
- **Independent brand**: MSLM focuses on team collaboration; SLM focuses on single-user memory

The core memory engine is powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory) (AGPL-3.0-or-later).

---

## Community

- **Issues**: [GitHub Issues](https://github.com/kenyonxu/superlocalmemory/issues)
- **Upstream**: [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)
- **License**: AGPL-3.0-or-later

---

*MSLM — powered by [SuperLocalMemory](https://github.com/qualixar/superlocalmemory)*
