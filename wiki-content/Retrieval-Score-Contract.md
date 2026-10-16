# Retrieval Score Contract

Every recall result carries several numbers, and they answer different questions. This page defines each field, explains why none of them is an answer probability, and lists the extra fields a recall response uses to say whether the answer check ran and whether SLM abstained. The contract is on version 2 (`score_contract_version` is `"2"`). If you build a client, read the explicit field names and ignore the legacy aliases. This page matches `docs/retrieval-score-contract.md` and the code.

## Result fields

Retrieval ordering is not answer confidence. A retrieval score answers "how relevant is this stored fact to this query?" It does not answer "how likely is a generated answer to be correct?"

| Field | Meaning | How to read it |
|---|---|---|
| `relevance_score` | Query-relative relevance after the configured retrieval and scoring pipeline | Bounded to 0.0 to 1.0; compare only within a compatible release and retrieval configuration |
| `ranking_score` | Internal ranking utility after optional adaptive or reranking adjustments | Diagnostic only; not a probability, and not guaranteed to be bounded or comparable across configurations |
| `memory_confidence` | Confidence stored with the underlying assertion | Memory metadata; it is neither query relevance nor answer correctness |
| `trust_score` | Trust signal attached to the stored evidence | An evidence-policy signal, not answer confidence |
| `rank_position` | One-based position in the returned list | The observable order after ranking |

For one compatibility release the legacy field `score` aliases `relevance_score` and the legacy field `confidence` aliases `memory_confidence`. The code sets both pairs to the same value when it finalises a response (`core/score_contract.py`). New integrations should use the explicit names.

## Response fields

Every canonical recall response declares the contract version and the state of the answer check. A response that never reached the pipeline starts with `tier` empty and `answer_check_status` set to `skipped`. A completed recall with no answer check sets `answer_check_status` to `off`. `tier` stays empty until the support check labels the recall, and it is `supported` only when that check finds support. The object below is that completed, supported case with the answer check off. It is not the unset default:

```json
{
  "score_contract_version": "2",
  "calibration_status": "uncalibrated",
  "calibration_id": null,
  "answer_confidence": null,
  "abstained": false,
  "abstention_reason": null,
  "abstention_note": "",
  "tier": "supported",
  "warming_up": false,
  "status_note": "",
  "answer_check_status": "off",
  "reranker_status": "not_configured",
  "local_reranker_status": ""
}
```

With no answer check running, nothing judges whether the results answer the question, so `calibration_status` is `uncalibrated`, `calibration_id` is `null` and `answer_confidence` is `null`. Do not derive an answer probability by doubling, thresholding, averaging or otherwise transforming retrieval scores. Measured on a real store, the ranking score was the same for right answers, wrong answers and questions with no answer in memory, which is why the contract refuses to read it as confidence (comment in `core/score_contract.py`).

### Answer check status

`answer_check_status` says what became of the [Answer Check](Answer-Check) on this one recall. The six values below are the closed set in the code (`retrieval/answer_check_status.py`).

| Value | Meaning |
|---|---|
| `judged` | A verdict was applied to this recall |
| `off` | The check is not turned on, so no check runs |
| `skipped` | The check is on but was not asked: context loading, a system recall, no results, too little of the recall's time budget left, or the online option would have had to read another profile's memory |
| `busy` | The on-device check was already answering another recall |
| `warming` | The on-device check is still loading its model |
| `unavailable` | The check timed out, errored, has no key, or cannot run on this machine |

Skipping costs the verdict only. The memories, their order and their scores are the same either way. A verdict that is missing because the check was busy or warming is not the same as "nothing answers", which is why the status travels with the answer.

Alongside it, the response carries `answer_check_ran` (true only when the status is `judged`), `answer_check_reason` (a closed-set reason, or `reused` when an identical earlier check is repeated), and `answer_check_note`, a plain sentence for a person, empty when there is nothing to explain. Two further fields, `answerability` and `answerability_reason`, report supported, unsupported or unjudged and why; `abstained: false` never means "checked", and these fields do.

`reranker_status` names whichever step produced the final order. The value `jev_listwise` means the online answer check's optional reorder replaced what the local reranker had produced, and `local_reranker_status` then keeps the local status for that recall; it is empty when nothing replaced it.

### Support tier and notes

Whether a memory supports the question is decided by the support check described in [How Recall Works](How-Recall-Works), and the response reports it in these fields. The example above is a completed recall the support check labelled `supported`, with the answer check off.

| Field | Meaning |
|---|---|
| `tier` | `supported`, `related` or `none`. Also on each result: `supported`, `related` or `weak` |
| `abstention_note` | A plain sentence for a person when there is something to say: "That isn't in SuperLocalMemory.", "Nothing in memory matched this question.", "I don't have this exactly — here are the closest memories.", or the longer note that closer memories exist and `include_related` shows them |
| `warming_up` | `true` when the recall ran before the models and inputs were ready; then results are labelled related and are not a verdict that memory lacks the answer |
| `status_note` | The warming-up sentence: "SuperLocalMemory is still warming up — results may be incomplete. Try again in a few minutes." |
| `support` | The support check's verdict as an object: `status` (`supported` or `unsupported`), the `best` score among the results, the `threshold` it had to reach, the recipe `version`, `weak_shown` and `tier`. It is `null` when the check did not run |
| `layers` | Present when `depth` is not 1 or `max_tokens` is set: the depth, the token cap, the tokens used, whether anything was trimmed and how many results were left out |

The `support` numbers are a diagnostic of one recipe version, not a probability that an answer is correct, and `answer_confidence` stays `null` unless a calibrated verdict exists.

### Abstention

`abstained` is true when the returned list is empty, when a judged verdict says the results do not answer, or when a support check did not label the recall `supported`. The support check overrides the other two: a supported recall is not abstaining, even if an earlier verdict was insufficient. The reason is then chosen in order. A judged-insufficient verdict is `judged_insufficient`. An unsupported support check that recorded probabilities is `unsupported`. If results are still in the list, the reason is `judged_insufficient`. If the evidence floor removed every result, the reason is `evidence_floor`. If candidate generation returned nothing, the reason is `no_candidates`.

| `abstention_reason` | When |
|---|---|
| `evidence_floor` | The evidence floor removed every result. The returned list is empty |
| `no_candidates` | Candidate generation returned nothing, and the floor did not record that it removed every result |
| `unsupported` | Results were found but the support check found none that supports the question; recall returns no results, or only labelled related ones |
| `judged_insufficient` | The answer check ran and judged that none of the results answer the question |
| `null` | Not abstaining |

## Answer check

When the [Answer Check](Answer-Check) is running (on-device and installed by default on Apple Silicon, otherwise off), a separate decision runs on the top results. It asks not "is this relevant" but "does this answer the question". Its verdict fills `calibration_status`, `calibration_id` and `answer_confidence` with real values instead of the uncalibrated defaults, and may set `abstention_reason` to `judged_insufficient`. `calibration_status` distinguishes a measured calibration from one that has not yet been measured for the current backend and settings, so treat an unmeasured verdict as a signal, not a guarantee. The verdict alone removes nothing and is reported next to the results. It does feed the support check, which can leave a recall with no results or only labelled related ones.

The daemon's `GET /recall` takes an `answer_check` query parameter. An empty value and `full` run the complete check, including the optional reorder when that is on. `no_reorder` runs the verdict only and never the reorder, which is what the bounded-loop gate asks for. `skip` runs no check. `context` also skips the check, and it skips the answer-support gate, which is the form used for context loading. Any other value is refused with HTTP 400. That error names `skip`, `no_reorder` and `full`; it does not name `context`, which is accepted before the check.

## How the ranking is built

The engine runs five candidate producers when their dependencies are healthy: dense semantic, BM25 lexical, temporal, Hopfield associative and spreading activation. Weighted reciprocal-rank fusion combines their output. Entity-graph information can raise a score after fusion, but it does not create a candidate of its own. Optional reranking and adaptive learning can change `ranking_score`; they do not turn it into a probability. A missing optional dependency can change the active channel set, so an application that needs a fixed retrieval topology should check health and trace metadata at startup. The channels that contributed to a result, and any channel abandoned at the hang guard (`incomplete_channels`), are in `channel_status` and in `slm trace`. See [How Recall Works](How-Recall-Works).

## Calibration release gate

Calibrated confidence needs a frozen release candidate, held-out relevance labels, a declared corpus and query distribution, a calibration identity, and reported calibration and selective-risk metrics. Until that evidence exists, the honest contract stays uncalibrated with `answer_confidence: null`. Runtime fields are not benchmark accuracy; the published LoCoMo figures and their protocols are on [Research Foundations](Research-Foundations).

Code: `core/score_contract.py` (`finalize_score_contract`), `server/recall_serializer.py`, `retrieval/answer_check_status.py`, `core/answer_check_stage.py`, `retrieval/answerability.py`, `mcp/_recall_metadata.py`, `retrieval/support_gate.py`, `retrieval/support_tiers.py`, `core/recall_layers.py`; full developer contract in `docs/retrieval-score-contract.md`.
