# V3 Mathematical Foundations

SuperLocalMemory V3 introduces three mathematical pillars — each a **world first** in agent memory systems. These are described in our peer-reviewed paper ([Zenodo DOI: 10.5281/zenodo.19038659](https://zenodo.org/records/19038659)).

---

## 1. Fisher-Rao Information Geometry

**The problem:** Cosine similarity treats embeddings as direction vectors. Two memories with the same meaning but different confidence look identical.

**Our solution:** We use the Fisher-Rao geodesic distance — the natural metric on statistical manifolds. Each memory embedding is modeled as a diagonal Gaussian distribution with learned mean and variance. Distance is measured along the geodesic (shortest path on the manifold), not through Euclidean space.

**What this means in practice:**
- A high-confidence memory and a low-confidence memory about the same topic are distinguished
- Retrieval improves as the system learns — variance shrinks with repeated access (graduated ramp)
- After 10 accesses, the system transitions from cosine similarity to full Fisher-Rao distance

**Benchmark impact:** +10.8 percentage points when Fisher-Rao is removed in ablation. Across six conversations, the three mathematical layers collectively contribute +12.7pp average improvement over the engineering baseline.

**Code:** [`superlocalmemory/math/fisher.py`](../blob/main/superlocalmemory/math/fisher.py)

---

## 2. Sheaf Cohomology for Memory Consistency

**The problem:** As memories accumulate, contradictions emerge. "Alice moved to London in March" vs "Alice lives in Paris as of April." Pairwise checking doesn't scale and misses transitive contradictions.

**Our solution:** We model the knowledge graph as a cellular sheaf — an algebraic structure from topology. Each edge carries a restriction map that relates adjacent memories. Computing the first cohomology group H¹(G,F) reveals global inconsistencies:

- **H¹ = 0** — All memories are globally consistent
- **H¹ ≠ 0** — Contradictions exist, even if every local pair looks fine

This catches contradictions that no pairwise method can detect.

**What this means in practice:**
- The system detects when new information contradicts existing knowledge
- Contradictions are flagged and can be resolved automatically (newer supersedes older) or surfaced to the user
- Knowledge graph maintains algebraic consistency at all times

**Code:** [`superlocalmemory/math/sheaf.py`](../blob/main/superlocalmemory/math/sheaf.py)

---

## 3. Riemannian Langevin Dynamics for Memory Lifecycle

**The problem:** Memory systems need lifecycle management — old, unused memories should be archived. Current systems use hardcoded thresholds (e.g., "archive after 30 days"). This doesn't adapt to usage patterns.

**Our solution:** Memory lifecycle evolves via stochastic gradient flow on a Riemannian manifold. The potential function encodes access frequency, trust score, and recency. The dynamics provably converge to a stationary distribution — the mathematically optimal allocation of memories across lifecycle states.

**Four lifecycle states:**
- **Active** — Frequently used, instantly available
- **Warm** — Recently used, included in searches
- **Cold** — Older, retrievable on demand
- **Archived** — Compressed, restorable when needed

**What this means in practice:**
- No manual thresholds — the system self-organizes
- Frequently accessed memories stay active longer
- Low-trust memories decay faster (coupled with Fisher-Rao via information geometry)
- Mathematically guaranteed convergence — not heuristic

**Code:** [`superlocalmemory/dynamics/fisher_langevin_coupling.py`](../blob/main/superlocalmemory/dynamics/fisher_langevin_coupling.py)

---

## Ablation Results

Each row disables one component of the full system. Delta denotes the change in accuracy relative to the full system.

| Configuration | LoCoMo (%) | Delta (pp) |
|:-------------|:-----:|:------:|
| **Full system** | **60.4** | — |
| − Fisher metric | 49.6 | −10.8 |
| − Sheaf consistency | 58.7 | −1.7 |
| − All math layers | 52.8 | −7.6 |
| − BM25 channel | 53.9 | −6.5 |
| − Entity graph | 59.4 | −1.0 |
| − Temporal channel | 60.2 | −0.2 |
| − Cross-encoder | 29.7 | −30.7 |

**Key findings:**
- Cross-encoder reranking is the single largest contributor (−30.7pp when removed)
- Fisher metric removal causes −10.8pp — the largest single mathematical layer effect
- All three mathematical layers contribute −7.6pp in aggregate on this conversation
- Across six conversations, mathematical layers average +12.7pp improvement over the engineering baseline, reaching +19.9pp on the most challenging dialogues

---

## Why These Specific Methods?

| Method | Why We Chose It | Alternative We Rejected |
|--------|----------------|------------------------|
| Fisher-Rao | Natural metric for probability distributions; captures uncertainty | Cosine similarity (ignores confidence) |
| Sheaf Cohomology | Detects global inconsistencies from local data; scales algebraically | Pairwise contradiction checking (O(n²), misses transitive) |
| Riemannian Langevin | Provable convergence; couples naturally with Fisher metric | Hardcoded thresholds (doesn't adapt) |

---

## Research Paper

For the full mathematical treatment including proofs, theorems, and detailed experimental methodology:

**SuperLocalMemory V3: Information-Geometric Foundations for Zero-LLM Enterprise Agent Memory**

*Varun Pratap Bhardwaj, Independent Researcher, 2026*

[Zenodo DOI: 10.5281/zenodo.19038659](https://zenodo.org/records/19038659)

---

*Part of [Qualixar](https://qualixar.com) · Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
