# V3 Architecture

SuperLocalMemory V3 is a complete architectural reinvention — a mathematical retrieval engine built on information geometry, algebraic topology, and stochastic dynamics.

---

## Overview

```
┌──────────────────────────────────────────────────────┐
│                 SuperLocalMemory V3                    │
│                                                        │
│  ┌──────────────────┐  ┌────────────────────────────┐ │
│  │  Product Shell     │  │  Mathematical Engine       │ │
│  │                    │  │                            │ │
│  │  CLI (15 commands) │  │  4-Channel Retrieval       │ │
│  │  MCP Server (24)   │  │  Fisher-Rao Similarity     │ │
│  │  Web Dashboard     │  │  Sheaf Consistency         │ │
│  │  17+ IDE Configs   │  │  Langevin Lifecycle        │ │
│  │  Learning (LightGBM│  │  11-Step Ingestion         │ │
│  │  Trust (Bayesian)  │  │  Scene + Bridge Discovery  │ │
│  │  Compliance (ABAC) │  │  Cross-Encoder Rerank      │ │
│  │  Profiles (16+)    │  │  3 Operating Modes         │ │
│  └──────────────────┘  └────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

---

## 4-Channel Hybrid Retrieval

V3 retrieves memories through four parallel channels, each capturing different aspects of relevance:

```
Query
  │
  ├─ Strategy Classification (single-hop / multi-hop / temporal / open-domain)
  │
  ├─ 4 Parallel Channels:
  │  ├─ Semantic Channel (Fisher-Rao weighted embedding similarity)
  │  ├─ BM25 Channel (keyword matching, k1=1.2, b=0.75)
  │  ├─ Entity Graph Channel (spreading activation, 3 hops, decay 0.7)
  │  └─ Temporal Channel (3-date model: observation, referenced, interval)
  │
  ├─ Profile Lookup (direct SQL shortcut for entity queries)
  │
  ├─ Weighted RRF Fusion (k=60, channel weights vary by query type)
  │
  ├─ Scene Expansion (pull all facts from matched scenes)
  │
  ├─ Bridge Discovery (multi-hop only: Steiner tree + spreading activation)
  │
  ├─ Cross-Encoder Rerank (energy-weighted: α·sigmoid(CE) + (1-α)·RRF)
  │
  └─ Top-K Results with per-channel scores
```

### Why Four Channels?

| Channel | What It Catches | What It Misses |
|---------|----------------|----------------|
| Semantic | Meaning similarity | Exact keywords, entity names |
| BM25 | Exact terms, rare words | Paraphrases, synonyms |
| Entity Graph | Relational connections | Unconnected memories |
| Temporal | Time-relevant facts | Atemporal knowledge |

No single channel handles all query types. The fusion combines their strengths.

---

## Three Operating Modes

| Mode | Description | LLM | EU AI Act |
|:----:|:-----------|:---:|:---------:|
| **A: Local Guardian** | Pure mathematical retrieval. Zero cloud calls. | None | Compliant |
| **B: Smart Local** | Mode A + local LLM (Ollama) for extraction. | Local | Compliant |
| **C: Full Power** | Mode B + cloud LLM + agentic retrieval. | Cloud | Partial |

**Mode A** is architecturally unique: no other memory system achieves meaningful accuracy without LLM calls. The 4-channel retrieval + cross-encoder reranking provides high-quality results without generative AI.

---

## 11-Step Ingestion Pipeline

Every memory is processed through structured encoding before storage:

| Step | What Happens |
|:----:|:------------|
| 1 | **Entropy gating** — information-theoretic filtering (low-entropy = skip) |
| 2 | **Fact extraction** — atomic, typed facts (episodic/semantic/opinion/temporal) |
| 3 | **Entity resolution** — canonical names with alias tracking |
| 4 | **Temporal parsing** — 3-date model (observation, referenced, interval) |
| 5 | **Type routing** — classify fact types for specialized handling |
| 6 | **Emotional signal extraction** — valence and arousal tagging |
| 7 | **Knowledge graph construction** — entities as nodes, relationships as edges |
| 8 | **Consolidation** — merge/update/supersede existing facts |
| 9 | **Scene clustering** — group facts by temporal-semantic coherence |
| 10 | **Observation building** — structured entity profiles |
| 11 | **Foresight generation** — anticipatory indexing for future queries |

---

## Database Schema

V3 uses a **17-table** SQLite schema with FTS5 full-text search:

**Core:** `profiles`, `memories`, `atomic_facts`, `atomic_facts_fts` (FTS5)
**Entities:** `canonical_entities`, `entity_aliases`, `entity_profiles`
**Graph:** `graph_edges`, `memory_scenes`, `temporal_events`
**Quality:** `consolidation_log`, `trust_scores`, `provenance`
**Learning:** `feedback_records`, `behavioral_patterns`, `action_outcomes`
**Compliance:** `compliance_audit`
**Infrastructure:** `bm25_tokens`, `config`, `schema_version`

All tables are partitioned by `profile_id` for multi-context isolation (16+ profiles).

---

## Code Structure

```
superlocalmemory/src/superlocalmemory/
├── core/           Engine, config, modes, profiles, embeddings
├── retrieval/      4-channel engine, semantic, BM25, entity, temporal, fusion, reranker
├── math/           Fisher-Rao metric, sheaf cohomology
├── dynamics/       Langevin lifecycle, Fisher-Langevin coupling
├── encoding/       11-step pipeline (entity resolver, fact extractor, scene builder...)
├── storage/        Database, schema, migrations, V2 migrator
├── compliance/     EU AI Act, GDPR, ABAC
├── learning/       Adaptive learning, behavioral tracking, outcomes
├── trust/          Trust scoring, provenance tracking, gates
├── llm/            LLM backbone (Ollama / Azure / OpenAI)
├── mcp/            MCP server (24 tools, 6 resources)
├── cli/            CLI with setup wizard (15 commands)
├── server/         Dashboard API + UI server
└── tests/          1400+ tests
```

---

## Dashboard

```bash
slm dashboard    # Opens at http://localhost:8765
```

<details open>
<summary><strong>V3 Dashboard</strong></summary>

![Dashboard](https://raw.githubusercontent.com/qualixar/superlocalmemory/main/docs/screenshots/01-dashboard-main.png)

<table><tr>
<td><img src="https://raw.githubusercontent.com/qualixar/superlocalmemory/main/docs/screenshots/04-recall-lab.png" width="280"/></td>
<td><img src="https://raw.githubusercontent.com/qualixar/superlocalmemory/main/docs/screenshots/03-math-health.png" width="280"/></td>
</tr></table>

</details>

17 tabs: Dashboard, Recall Lab, Knowledge Graph, Memories, Trust, Math Health, Compliance, Learning, IDE Connections, Settings, and more.

## Benchmarks

Evaluated on the [LoCoMo benchmark](https://arxiv.org/abs/2402.09714) — 10 multi-session conversations, 1,986 total questions.

| Configuration | Aggregate | Multi-Hop | Open Domain |
|:-------|:--:|:--:|:--:|
| **Mode A Retrieval (10 convs, 1,276 questions)** | **74.8%** | **70.3%** | **85.0%** |
| **Mode A Raw (zero-LLM)** | **60.4%** | **43.0%** | **72.0%** |
| **Mode C (conv-30, 81 questions)** | **87.7%** | **100.0%** | **86.0%** |

### Ablation (conv-30, 81 questions)

| Removed | Impact |
|:--------|:------:|
| Cross-encoder reranking | **-30.7pp** |
| Fisher-Rao metric | **-10.8pp** |
| All math layers | **-7.6pp** |
| BM25 channel | **-6.5pp** |
| Sheaf consistency | -1.7pp |
| Entity graph | -1.0pp |

Mathematical layers contribute **+12.7pp average** across 6 conversations (n=832), with up to **+19.9pp** on the most challenging dialogues.

Full methodology and results in the [V3 paper](https://arxiv.org/abs/2603.14588) ([Zenodo](https://zenodo.org/records/19038659)).

---

*Part of [Qualixar](https://qualixar.com) · Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
