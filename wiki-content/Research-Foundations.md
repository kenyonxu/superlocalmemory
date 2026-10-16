# Research Foundations

SuperLocalMemory is built on four arXiv papers and a set of mathematical layers that sit under recall, consolidation and lifecycle. This page explains what each paper contributed, what each mathematical layer does and whether it is switched on in the shipped configuration, and which benchmark numbers have been published, with the protocol each one used. The published numbers come from the architecture paper. They are not a fresh run of the current package, and they should not be compared with another system unless the subset, answer model and judge match.

## The four papers

All four are by Varun Pratap Bhardwaj; the governed-memory paper has two further authors. They are listed newest first.

| arXiv | Title | What it covers | Where it shows up in the product |
|---|---|---|---|
| [2608.08253](https://arxiv.org/abs/2608.08253) | SuperLocalMemory: The Governed Memory Operating System for AI Agents (with Garima Singh and Arun Pratap Bhardwaj) | Multi-scope isolation, role-based access, verified erasure, hash-chained audit and bi-temporal recall, with the measured cost of the governed write path | [Governance](Governance), [Security, Company Mode and Teams](Security-Company-Mode-and-Teams), [Profiles](Profiles) |
| [2604.04514](https://arxiv.org/abs/2604.04514) | SuperLocalMemory: The Living Brain | Biologically inspired forgetting, cognitive quantization, multi-channel retrieval without a language model | [Living Brain](Living-Brain) |
| [2603.14588](https://arxiv.org/abs/2603.14588) | SuperLocalMemory: Information-Geometric Foundations for Zero-LLM Enterprise Agent Memory | Fisher-information retrieval, Langevin lifecycle, sheaf contradiction detection, and the LoCoMo results below | [How Recall Works](How-Recall-Works), this page |
| [2603.02240](https://arxiv.org/abs/2603.02240) | SuperLocalMemory: Privacy-Preserving Multi-Agent Memory with Bayesian Trust Defense Against Memory Poisoning | Per-agent Bayesian trust scores and defence against poisoned writes | [Governance](Governance), [Multi-Agent Memory](Multi-Agent-Memory) |

The titles on arXiv carry the release labels that were current when each paper was written. The package has moved on since; the papers describe the ideas, and the rest of this wiki describes what the code does today.

## The mathematical layers

Each layer lives in `src/superlocalmemory/math/` (the Langevin coupling is in `dynamics/`). The table says what the layer does and, more usefully, whether it runs by default, because three of them are optional or off.

| Layer | Job | Default in the shipped config |
|---|---|---|
| Fisher-Rao similarity | Scores how close a query is to a memory while accounting for how sure SLM is about that memory | On, in the `simplified` form (`math.fisher_mode`) |
| Sheaf consistency | Flags a new fact that contradicts facts it is linked to | Off (`math.sheaf_at_encoding` is `false`) |
| Langevin lifecycle | Moves each memory between active, warm, cold and archived | Positions are persisted (`math.langevin_persist_positions` is `true`) |
| Ebbinghaus decay | Computes retention from time and use | On (`ForgettingConfig.enabled` is `true`) |
| Hopfield associative memory | One of the five candidate producers in recall | On, with size limits |
| TurboQuant and relatives | Compresses the vectors of memories that have gone cold | On, with a float32 backup kept |

### Fisher-Rao similarity

Cosine similarity treats an embedding as a direction. Fisher-Rao treats it as a diagonal Gaussian with a mean and a variance, and measures distance along the geodesic of that statistical manifold. Two memories with the same mean but different uncertainty have the same cosine distance and different Fisher-Rao distance, which is the point of using it. The variance is not fixed: each fact keeps its own variance vector, and every consistent observation narrows it with the update `new_var = 1 / (1/old_var + 1/obs_var)`, so a memory that has been confirmed many times matches a query more tightly than one seen once. The code notes that an earlier version gave every query the same uniform variance, which made Fisher-Rao a monotonic transform of cosine; keeping a variance per fact is what gives it something different to say. The Bayesian update is on by default (`math.fisher_bayesian_update`), and `math.fisher_temperature` defaults to 15.0.

The shipped default is `math.fisher_mode = "simplified"`, a fast local approximation that is variance-weighted. Setting it to `"full"` switches the semantic channel to the full Atkinson-Mitchell geodesic, `d_i = sqrt(2) * arccosh(1 + delta_i)`, combined over dimensions as the square root of the sum of squares. If the full computation cannot be applied it falls back to the simplified one. A variant called FRQAD handles mixed-precision pairs: it inflates the variance of a quantized embedding by `(32 / bit_width) ^ kappa`, so compressed memories rank slightly lower, and it reduces exactly to ordinary Fisher-Rao when both sides are float32. Code: `math/fisher.py`, `math/fisher_quantized.py`, `retrieval/semantic_channel.py`.

### Sheaf consistency

A knowledge graph can be read as a cellular sheaf: each edge carries a restriction map between the two memories it joins, and the coboundary of those maps measures how far adjacent memories are from agreeing. The idea is that contradictions can be found by that local disagreement instead of by comparing every pair. In SLM the severity of an edge is the coboundary norm divided by the sum of the two restricted norms, the restriction map depends on the edge type (identity for entity and semantic edges, a damped one for temporal edges), and temporal edges below a 0.15 tolerance are not flagged because a changed state is not a contradiction. The default severity threshold is 0.45 (`math.sheaf_contradiction_threshold`), and a fact with more than 200 edges is skipped (`math.sheaf_max_edges_per_check`).

Two things about this layer are easy to get wrong. It runs at encoding time, never at recall; an earlier version applied it at recall, which penalised diverse multi-hop results, and the code records that as the reason it moved. And it is off by default. The comment in `core/config.py` gives the reason: a measured A/B comparison on a real store showed it changed no recall answer while it ran on every store and every maintenance pass. One flag, `math.sheaf_at_encoding`, gates all three places it is used: the store-time check, the maintenance pass, and contradiction detection in the temporal validator in Mode A, which finds none without it. Setting it to `true` turns all three back on. Contradictions are still handled when it is off, through the consolidator's update and supersede path. `slm health` and the MCP `consistency_check` tool run the check on demand. Code: `math/sheaf.py`.

### Langevin lifecycle

Memory positions evolve by a discretised Langevin stochastic differential equation on a Riemannian manifold, driven by a potential that rewards access count and importance and penalises age. Frequently used and important memories settle near the origin (active, high retrieval weight) and neglected ones drift toward the boundary (archived, weight near zero). The weight range is 0.0 to 1.0, wide enough for an archived memory to be suppressed in ranking; an earlier narrower range could not change a ranking. In today's code the position is seeded and kept aligned with the memory's Ebbinghaus retention, with radius `1 - R(t)`, so the tier follows how a memory is used and nothing else (`core/langevin_tier.py`). The Fisher-Langevin coupling in `dynamics/fisher_langevin_coupling.py` makes the temperature depend on confidence, `T_eff = T_0 / (fisher_confidence + epsilon)`: a confident memory is less noisy and stays put, an uncertain one drifts. The lifecycle states and thresholds are described in [Living Brain](Living-Brain) and [Governance](Governance). Code: `math/langevin.py`, `dynamics/fisher_langevin_coupling.py`, `dynamics/ebbinghaus_langevin_coupling.py`.

### Ebbinghaus decay

Retention is `R(t) = e^(-t/S)`, with `t` the hours since last access and `S` a strength that grows with the logarithm of access count, importance, confirmations and emotional salience. The zones are active above 0.8, warm above 0.5, cold above 0.2, archive above 0.05 and forgotten at or below it. Forgetting runs on a scheduler every 30 minutes, memories in core blocks are immune, and the effective decay rate is raised for low-trust memories. The combined Ebbinghaus-Langevin coupling in the maintenance cycle is a separate switch (`math.ebbinghaus_langevin_coupling_enabled`) that is off by default. Code: `math/ebbinghaus.py`, `learning/forgetting_scheduler.py`.

### Hopfield associative memory

The Hopfield channel uses the modern continuous Hopfield network (arXiv 2008.02217), whose update rule `xi_new = X' softmax(beta X xi)` is the same operation as single-head attention, with `beta = 1 / sqrt(d)`. In recall it is one of the five candidate producers, and its fusion weight is 0.8 (1.0 in Mode C). It is built for pattern completion on vague or noisy queries, and it finds related material rather than matching material, so on its own it does not count as evidence for the evidence floor ([How Recall Works](How-Recall-Works)). It has size limits: a full matrix path for under 10,000 facts, a nearest-neighbour prefilter of 500 candidates between 10,000 and 100,000, and it returns nothing above 100,000, where the other channels are expected to carry the query. Code: `math/hopfield.py`, `retrieval/hopfield_channel.py`.

### TurboQuant and the quantizers

Cold memories do not need full-precision vectors. TurboQuant rotates each embedding by a fixed random orthogonal matrix and quantizes every coordinate with a Lloyd-Max codebook at 2, 4 or 8 bits; 3-bit is deliberately not supported. Stored vectors carry a two-byte `TQ` prefix. The default codebook method is `turbo`, with the older PolarQuant method kept as `polar_legacy`, and an optional QJL stage adds 1-bit sign projections of the residual to correct approximate similarity. The rotation matrix is built with Householder reflections in plain numpy, because the LAPACK QR call on macOS was found to write outside its buffers for square inputs near the default 768 dimensions. Quantization is on by default, keeps a float32 backup, runs its compaction every 6 hours, and compressed vectors are searched with a 0.97 penalty. The references in the code are TurboQuant (arXiv 2504.19874), PolarQuant (arXiv 2502.02617) and QJL (arXiv 2406.03482). Code: `math/turbo_quant.py`, `math/polar_quant.py`, `math/qjl.py`, `math/orthogonal.py`.

## Published benchmark numbers

These are the historical LoCoMo results reported in the architecture paper (arXiv:2603.14588), and the project README states plainly that they are not a fresh run of the current package. They are kept here with the protocol each used, because a LoCoMo percentage means nothing without it.

| Configuration | LoCoMo | Protocol |
|---|---:|---|
| Mode A, raw | 60.4% | 10 conversations, 1,276 scored questions; local embeddings, local retrieval, no language model anywhere in answer construction |
| Mode A, retrieval | 74.8% | Same 10 conversations and 1,276 questions; local retrieval, then GPT-4.1-mini writes the answer |
| Mode C | 87.7% | Conversation 30 only (one conversation), 81 questions; cloud embeddings (text-embedding-3-large) with GPT-4.1-mini for the answer and the judge |

The Mode A retrieval run reported 72.0% on single-hop, 70.3% on multi-hop, 80.0% on temporal and 85.0% on open-domain questions. The Mode C figure covers a single conversation and is not a full-dataset result. The three rows use different answer-construction models, so they are not three scores of one system and cannot be ranked against each other.

The paper also reports an ablation across six conversations: 71.7% with the information-geometric layers and 58.9% without them, a gain of 12.7 points. That average is what the project's benchmark note publishes. Per-conversation scores, a question count and the answer model for that ablation are not in the note, so they are not repeated here.

Read the ablation with the defaults above in mind. It measures the paper's architecture with its information-geometric layers on or off. The package today ships the simplified Fisher mode and has the sheaf layer off, so the 12.7 points should not be quoted as the effect of the shipped defaults. Nothing on this page claims that a rerun of the current package would reproduce them.

Comparing a LoCoMo figure responsibly needs the conversation subset, the question count, the retrieval stack, the answer model, the judge and the release artifact to be declared and compatible. A score reported under another protocol is not evidence that SLM does better or worse. The command `slm benchmark` runs an evo-memory benchmark against a temporary database and never touches your data; it is a different test from LoCoMo and does not reproduce these numbers.

Code: `math/` (`fisher.py`, `fisher_quantized.py`, `sheaf.py`, `langevin.py`, `ebbinghaus.py`, `hopfield.py`, `turbo_quant.py`, `polar_quant.py`, `qjl.py`, `orthogonal.py`), `dynamics/`, `core/config.py` (`MathConfig`, `ForgettingConfig`, `QuantizationConfig`), `core/langevin_tier.py`, `retrieval/semantic_channel.py`, `retrieval/hopfield_channel.py`; published figures in `docs/benchmarks.md`.
