# Active Memory

Active memory is the part of SLM that learns from how recalled memories are used. Recall itself is read-only: searching never changes anything. What changes SLM's behaviour over time is feedback that you or your assistant report afterwards, and that feedback is recorded in a separate learning database. This page explains what gets recorded, what it can change, and what it deliberately does not.

## The loop

```
Recall  ->  the assistant uses (or ignores) the memories  ->  feedback or an outcome is reported  ->  learning store  ->  optional re-ranking
```

A recall response carries a `query_id`. When the assistant reports back, it can pass that id, which ties the report to that exact recall rather than to a guess about which memories overlap in time. Being returned is not treated as being useful: a memory that was merely shown is an exposure, and only an explicit report turns it into a signal.

## The two reports

`report_feedback` is a quick verdict on one memory. The value is `relevant` (the default), `irrelevant` or `partial`. `report_outcome` is a verdict on how a piece of work went after using one or more memories, given as a comma-separated list of memory ids plus `success`, `failure` or `partial`, with an optional note. SLM turns an outcome into a reward of 1.0 for success, 0.5 for partial and 0.0 for failure. Sending the same `report_id` twice records nothing new, so a retry is safe. Both tools respect profiles and the visibility rules of [Governance](Governance): a web app can report only on memories it is allowed to see, and an id it cannot see is answered like an id that does not exist.

Feedback is written to the learning database (`learning.db` in your data folder). If that write fails, the reply says so plainly (`durable: false`) and points to `slm doctor`, because a signal that was not saved cannot influence anything. On success the reply includes the running `total_signals` and the current `phase`.

## What the signals can change

The signals feed three phases of an adaptive ranker, counted per profile.

| Phase | When | What orders the results |
|---|---|---|
| 1 | Fewer than 50 signals | The normal pipeline: fusion and the reranker |
| 2 | 50 signals or more | A rule-based adjustment using the learned signals |
| 3 | 200 signals or more, and a verified learned model is present | A learned model (LightGBM) trained from your own signals |

The decisive point is that none of this reorders recall unless you switch it on. Adaptive ranking is off by default. It runs only when the environment variable `SLM_RANKING` is set to `v1`, `v2` or `v2-ensemble`; any other value, or no value, means off. An upgrade never turns it on, because the project's position is that a memory system's answers should change when the evidence changes, not because a learned weight drifted. Until you enable it, feedback is still recorded, but results come back in the usual order. The score fields that recall returns are explained in [Retrieval Score Contract](Retrieval-Score-Contract); learning never turns a relevance score into a probability that an answer is right.

## What it does not promise

Adaptive ranking needs representative feedback to help, and the signal counts depend entirely on how much feedback you give. SLM publishes no time-to-benefit figure and no guarantee that ranking improves on every store. If you want to judge it on your own data, enable it for a while, keep reporting, and compare with `slm trace`, which shows how each channel scored a result ([How Recall Works](How-Recall-Works)).

## Related

[Auto-Memory](Auto-Memory) covers what gets captured and loaded automatically. [Living Brain](Living-Brain) covers decay, consolidation and the other background learning. [MCP Tools](MCP-Tools) lists `report_feedback` and `report_outcome` with their arguments, and [Multi-Agent Memory](Multi-Agent-Memory) explains how several agents share one store.

Code: `mcp/tools_active.py` (`report_feedback`), `mcp/tools_v28.py` (`report_outcome`), `learning/ranker.py` (`PHASE_2_THRESHOLD`, `PHASE_3_THRESHOLD`), `core/recall_pipeline.py` (`_resolve_ranking_mode`), `learning/outcome_events.py`.
