#!/usr/bin/env python3
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Does recall know when it does not know? Measure it on a synthetic store.

Builds a ~2,000-memory synthetic store (scripts/quality/answerability_corpus.py)
with the real embedding model, then asks it three question sets from
``tests/fixtures/answerability``:

  nonsense      every one must abstain (an empty result)
  real          every one must have its memory in the top 5
  unanswerable  plausible questions with no stored answer (reported, not gated)

Two passes per settings profile:

  gate      the product's recall exactly as shipped: abstain rate, top-5 hit
            rate, latency p50/p95.
  features  the same questions with the evidence floor switched off, so every
            question has candidates, and records each candidate's evidence
            features. The separation (ROC AUC, real vs nonsense) of each
            question-level feature is what an answer-or-abstain model can use.

Profiles: ``a`` (Mode A as shipped) and ``b`` (Mode B's retrieval settings with
no LLM attached, so nothing leaves the machine and no model server is called).
Both use the cross-encoder, as both modes do.

    python scripts/quality/answerability_eval.py --work /tmp/ans-eval [--profiles a,b]
        [--cache DIR] [--corpus {en,cjk}] [--out report.json] [--rows rows.jsonl]

The store is built once per corpus digest into ``--cache`` (default: under
``--work``) and copied for each profile, so a measurement never changes it.
``--corpus cjk`` measures the Chinese twin corpus (answerability_corpus_cjk.py)
against its own fixtures; the default ``en`` path is the recorded baseline's,
byte for byte. Refuses to run against this account's own memory folder.

Output: counts, rates, ids, scores and timings. Synthetic text only.
Exit codes: 0 measured · 2 bad input · 3 refused.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENV_ROOTS = ("SLM_DATA_DIR", "SL_MEMORY_PATH", "SLM_HOME")
TOP_K = 5
SLICES = ("legacy", "described")
RECALL_LIMIT = 10


def _bootstrap() -> None:
    source = _HERE.parents[1] / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


#: Corpus selector: the EN store the recorded baselines name, and its Chinese
#: twin (answerability_corpus_cjk.py) for CJK calibration. The no-argument
#: call stays the EN module, so the default path is unchanged.
_CORPUS_MODULES = {"en": "answerability_corpus", "cjk": "answerability_corpus_cjk"}


def _load_corpus_module(corpus: str = "en"):
    name = _CORPUS_MODULES[corpus]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Refused(Exception):
    pass


def _refuse_own_store(path: Path) -> None:
    home = Path(os.path.expanduser("~"))
    try:
        import pwd

        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):
        pass
    own = (home / ".superlocalmemory").resolve()
    resolved = path.resolve()
    if resolved == own or own in resolved.parents:
        raise Refused(f"refusing to build or measure inside {own}; pass a scratch --work folder")


def _use_data_dir(path: Path) -> None:
    for key in _ENV_ROOTS:
        os.environ.pop(key, None)
    os.environ["SLM_DATA_DIR"] = str(path)


# -- engine ------------------------------------------------------------------------

#: The text models this harness can measure. nomic is what SLM ships for text;
#: EmbeddingGemma 2 runs in the managed model environment (``slm media enable``).
#: The answer-support recipe is fitted per model, so each needs its own store.
EMBEDDERS = ("nomic", "gemma")
GEMMA_MODEL = "google/embeddinggemma-2"


def _embedding_for(embedder: str, base):
    """``base`` (the shipped nomic settings) re-pointed at ``embedder``."""
    from dataclasses import replace

    if embedder not in EMBEDDERS:
        raise ValueError(f"unknown embedder {embedder!r}; choose from {', '.join(EMBEDDERS)}")
    if embedder == "nomic":
        return base
    return replace(base, model_name=GEMMA_MODEL, dimension=768, provider="slm-media")


def store_dir(cache: Path, digest: str, embedder: str) -> Path:
    """One built store per corpus and model; nomic keeps the original path."""
    return cache / digest if embedder == "nomic" else cache / f"{digest}-{embedder}"


def _media_env_root() -> Path:
    """The installed managed model environment: model files and worker only, read-only
    use. The memory store under test is always the scratch folder, never this account's."""
    return Path(os.environ.get("SLM_EVAL_MEDIA_ENV",
                               Path.home() / ".superlocalmemory" / "runtimes" / "media"))


def _offer_gemma(config, data_dir: Path) -> None:
    """Hand the engine a Gemma embedder that runs the installed worker directly.

    Outside the daemon the slm-media provider asks the running daemon for vectors,
    and that daemon serves whatever model the account uses. A measurement must
    never depend on it, so the harness drives the worker itself.
    """
    from superlocalmemory.core.embedding_live import offer_embedder
    from superlocalmemory.runtimes.managed_text import ManagedTextEmbedder
    from superlocalmemory.runtimes.media_env import media_env
    from superlocalmemory.storage.embedding_spaces import signature_of

    embedder = ManagedTextEmbedder(config.embedding, data_root=data_dir,
                                   env=media_env(root=_media_env_root()))
    if not embedder.is_available:
        raise Refused(f"the managed model environment at {_media_env_root()} is not ready; "
                      "run: slm media enable")
    offer_embedder(config.db_path, signature_of(config.embedding), embedder)


def _config(profile: str, data_dir: Path, embedder: str = "nomic"):
    from dataclasses import replace

    from superlocalmemory.core.config import LLMConfig, SLMConfig
    from superlocalmemory.storage.models import Mode

    mode = Mode.A if profile == "a" else Mode.B
    config = SLMConfig.for_mode(mode, base_dir=data_dir,
                                embedding_provider="sentence-transformers")
    config.embedding = _embedding_for(embedder, config.embedding)
    config.llm = LLMConfig()  # no model server, ever
    config.retrieval = replace(config.retrieval, sufficiency_judge="off")
    return config


def _engine(profile: str, data_dir: Path, warm_timeout_s: float = 300.0,
            embedder: str = "nomic"):
    from superlocalmemory.core.engine import MemoryEngine

    _use_data_dir(data_dir)
    config = _config(profile, data_dir, embedder)
    if embedder == "gemma":
        _offer_gemma(config, data_dir)
    engine = MemoryEngine(config)
    engine.initialize()
    return engine


def _warm(engine, timeout_s: float) -> dict:
    """Measure only warm, complete recall: model loaded, reranker ready."""
    reranker = getattr(getattr(engine, "_retrieval_engine", None), "_reranker", None)
    ready = bool(reranker is not None and reranker.warmup_sync(timeout=timeout_s))
    deadline = time.monotonic() + timeout_s
    missing: tuple = ()
    while time.monotonic() < deadline:
        missing = tuple(getattr(engine.recall("warm up", limit=1, fast=True),
                                "incomplete_channels", ()) or ())
        if not missing:
            break
        time.sleep(0.5)
    return {"reranker_ready": ready, "incomplete_after_warmup": list(missing)}


def _backfill(engine) -> dict:
    """Give every stored memory its hub baseline before measuring (foreground,
    timed), when this build has them, rather than wait for maintenance."""
    try:
        from superlocalmemory.retrieval.hub_baseline import Backfill
    except ImportError:
        return {"available": False}
    t0 = time.monotonic()
    written = Backfill(engine._db, engine._embedder).run_until_done()
    try:  # a build from before the probes were warmed at boot measures without
        from superlocalmemory.retrieval.hub_baseline import warm_probes
        probes = warm_probes(engine._embedder)
    except ImportError:
        probes = None
    return {"available": True, "written": written, "probes": probes,
            "seconds": round(time.monotonic() - t0, 2)}


# -- build ---------------------------------------------------------------------------

def build_store(cache: Path, *, log=print, embedder: str = "nomic",
                corpus: str = "en") -> Path:
    """Build the synthetic store once per corpus digest; return its data folder."""
    module = _load_corpus_module(corpus)
    digest = module.digest()
    root = store_dir(cache, digest, embedder)
    data = root / "data"
    keymap_path = root / "keymap.json"
    if keymap_path.is_file():
        return root
    if root.exists():
        shutil.rmtree(root)
    data.mkdir(parents=True)
    _refuse_own_store(data)
    engine = _engine("a", data, embedder=embedder)
    keymap: dict[str, list[str]] = {}
    rows = module.corpus()
    t0 = time.monotonic()
    try:
        for i, row in enumerate(rows):
            ids = engine.store(
                row.content,
                session_id=f"eval-{row.project}-{row.session_date[:7]}",
                session_date=row.stored_date,
                metadata=row.metadata,
            )
            keymap[row.key] = list(ids)
            if i % 250 == 0:
                log(f"stored {i + 1}/{len(rows)} in {time.monotonic() - t0:.0f} s")
    finally:
        engine.close()
    _set_lifecycles(data, rows, keymap)
    empty = [k for k, ids in keymap.items() if not ids]
    keymap_path.write_text(json.dumps({"digest": digest, "corpus_version": module.CORPUS_VERSION,
                                       "keys": keymap, "no_fact": empty}, indent=1),
                           encoding="utf-8")
    log(f"built {len(rows)} memories in {time.monotonic() - t0:.0f} s "
        f"({len(empty)} without a fact)")
    return root


def _set_lifecycles(data: Path, rows, keymap: dict[str, list[str]]) -> None:
    """Spread lifecycle states like a real store's (the save makes every row active)."""
    import sqlite3

    conn = sqlite3.connect(data / "memory.db")
    try:
        for row in rows:
            for fact_id in keymap.get(row.key, ()):
                conn.execute("UPDATE atomic_facts SET lifecycle = ? WHERE fact_id = ?",
                             (row.lifecycle, fact_id))
        conn.commit()
    finally:
        conn.close()


def _memory_ids(data: Path, keymap: dict[str, list[str]]) -> dict[str, str]:
    """memory_id -> corpus key, read from the built store."""
    import sqlite3

    fact_to_key = {fid: key for key, ids in keymap.items() for fid in ids}
    out: dict[str, str] = {}
    conn = sqlite3.connect(f"file:{data / 'memory.db'}?mode=ro", uri=True)
    try:
        for fact_id, memory_id in conn.execute("SELECT fact_id, memory_id FROM atomic_facts"):
            key = fact_to_key.get(fact_id)
            if key is not None and memory_id:
                out[memory_id] = key
    finally:
        conn.close()
    return out


# -- features ----------------------------------------------------------------------

def result_features(result) -> dict:
    """Evidence features of one result: the recorded ones when the engine
    exposes them, otherwise what today's result already carries."""
    cs = dict(getattr(result, "channel_scores", None) or {})
    semantic = cs.get("semantic")
    feats = {
        "display_score": float(getattr(result, "score", 0.0) or 0.0),
        "cosine_from_channel": (2.0 * semantic - 1.0) if semantic else None,
        "bm25_channel": cs.get("bm25", 0.0),
        "entity_graph_channel": cs.get("entity_graph", 0.0),
        "rerank_blend": getattr(result, "rerank_score", None),
    }
    evidence = getattr(result, "evidence", None)
    if isinstance(evidence, dict):
        for key, value in evidence.items():
            if isinstance(value, bool):
                feats[key] = 1.0 if value else 0.0
            elif isinstance(value, (int, float)) or value is None:
                feats[key] = value
    return feats


def _question_level(rows: list[dict]) -> dict[str, float]:
    """Max of each feature over a question's candidates (what a gate sees)."""
    out: dict[str, float] = {}
    for feats in rows:
        for key, value in feats.items():
            if value is None:
                continue
            out[key] = max(out.get(key, -math.inf), float(value))
    return out


def auc(positives: list[float], negatives: list[float]) -> float | None:
    """P(positive > negative) with ties at one half (Mann-Whitney)."""
    if not positives or not negatives:
        return None
    wins = 0.0
    for p in positives:
        for n in negatives:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(positives) * len(negatives))


def separation(per_question: list[dict], field: str = "question_features",
               real_slice: str | None = None) -> dict:
    """AUC per question-level feature. With ``real_slice``, only that slice's
    real questions, and every question's features from that slice's candidates."""
    keys = sorted({k for row in per_question for k in row[field]})
    report = {}
    for key in keys:
        def values(sets):
            return [row[field].get(key, -1e9) for row in per_question
                    if row["set"] in sets
                    and (real_slice is None or row["set"] != "real"
                         or row.get("slice") == real_slice)]
        real, nonsense = values({"real"}), values({"nonsense"})
        unanswerable = values({"unanswerable"})
        report[key] = {
            "auc_real_vs_nonsense": _round(auc(real, nonsense)),
            "auc_real_vs_unanswerable": _round(auc(real, unanswerable)),
            "real_min": _round(min(real) if real else None),
            "nonsense_max": _round(max(nonsense) if nonsense else None),
            "real_p05": _round(_pct(real, 5)), "nonsense_p95": _round(_pct(nonsense, 95)),
        }
    return report


def _pct(values: list[float], p: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)]


def _round(value, digits: int = 4):
    return None if value is None else round(float(value), digits)


# -- measurement ---------------------------------------------------------------------

#: Question files per suite: (fixture name, kind). "hand" is the hand-written
#: sets; "dev" adds the generated development half; "fresh" is the frozen
#: final test half (scripts/quality/answerability_questions.py), never fitted.
_HAND = (("nonsense", "nonsense"), ("real", "real"), ("unanswerable", "unanswerable"))
_DEV = tuple((f"generated/dev_{k}", k) for k in ("nonsense", "real", "unanswerable"))
_FRESH = tuple((f"generated/fresh_{k}", k) for k in ("nonsense", "real", "unanswerable"))
SUITES = {"hand": _HAND, "dev": _HAND + _DEV, "fresh": _FRESH, "frozen": _HAND + _FRESH,
          "all": _HAND + _DEV + _FRESH}


def _questions(suite: str = "hand", corpus: str = "en") -> list[tuple[str, dict]]:
    """``(kind, question)`` pairs; each question carries its ``source`` file."""
    module = _load_corpus_module(corpus)
    return [(kind, {**q, "source": name.rsplit("/", 1)[-1]}) for name, kind in SUITES[suite]
            for q in module.load_questions(name)]


def _hit_rank(keys: list[str], expected: list[str]) -> int | None:
    for rank, key in enumerate(keys, start=1):
        if key in expected:
            return rank
    return None


def _load_conditions_module():
    name = "answerability_conditions"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "answerability_conditions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _ask_one(engine, set_name: str, q: dict, memory_to_key: dict) -> dict:
    response = engine.recall(q["question"], limit=RECALL_LIMIT, fast=True)
    keys = [memory_to_key.get(r.fact.memory_id or "", "?") for r in response.results]
    return {
        "qid": q["qid"], "set": set_name, "result_count": len(response.results),
        "abstained": len(response.results) == 0,
        "rank": None if q["expected"] == "abstain" else _hit_rank(keys, q["expected"]),
        "tier": getattr(response, "support_tier", ""),
        "warming_up": bool(getattr(response, "warming_up", False)),
    }


def measure_conditions(engine, suite: str, memory_to_key: dict, names: list[str],
                       log=print, corpus: str = "en") -> dict:
    """The merge rule: each condition gated against gate-off (conditions module).

    Each question is asked gate-off and then gated, back to back: in two
    whole-suite passes every recall in between reinforces what it returned,
    and a memory at rank five in one pass sat at six in the other.
    """
    cond = _load_conditions_module()
    out = {}
    for name in names:
        gated, before = [], []
        with cond.condition(engine, name):
            for set_name, q in _questions(suite, corpus):
                with cond.gate_off():
                    before.append(_ask_one(engine, set_name, q, memory_to_key))
                gated.append(_ask_one(engine, set_name, q, memory_to_key))
        out[name] = {**cond.compare(gated, before),
                     "warming_up": sum(r["warming_up"] for r in gated)}
        log(f"condition {name}: {json.dumps(out[name], sort_keys=True)}")
    return out


def measure_profile(profile: str, store_root: Path, work: Path, *,
                    warm_timeout_s: float = 300.0, log=print, suite: str = "hand",
                    conditions: list[str] | None = None, embedder: str = "nomic",
                    corpus: str = "en") -> dict:
    keymap = json.loads((store_root / "keymap.json").read_text(encoding="utf-8"))["keys"]
    data = work / f"data-{profile}"
    if data.exists():
        shutil.rmtree(data)
    shutil.copytree(store_root / "data", data)
    _refuse_own_store(data)
    memory_to_key = _memory_ids(data, keymap)
    slices = _load_corpus_module(corpus).slice_of_key()
    engine = _engine(profile, data, embedder=embedder)
    rows: list[dict] = []
    latencies: list[float] = []
    try:
        warm = _warm(engine, warm_timeout_s)
        warm["hub_baseline_backfill"] = _backfill(engine)
        log(f"profile {profile}: warm {warm}")
        os.environ.pop("SLM_RECALL_NO_FLOOR", None)
        os.environ.pop("SLM_RECALL_NO_SUPPORT_GATE", None)
        for set_name, q in _questions(suite, corpus):
            t0 = time.monotonic()
            response = engine.recall(q["question"], limit=RECALL_LIMIT, fast=True)
            latencies.append((time.monotonic() - t0) * 1000.0)
            keys = [memory_to_key.get(r.fact.memory_id or "", "?") for r in response.results]
            rows.append({
                "qid": q["qid"], "set": set_name, "category": q["category"],
                "source": q["source"],
                "expected": q["expected"], "result_count": len(response.results),
                "abstained": len(response.results) == 0,
                "no_confident_match": bool(response.no_confident_match),
                "rank": None if q["expected"] == "abstain" else _hit_rank(keys, q["expected"]),
                "top_keys": keys[:TOP_K], "result_keys": keys,
                "slice": (slices.get(q["expected"][0]) if q["expected"] != "abstain" else None),
                "reranker_status": response.reranker_status,
                "incomplete_channels": list(response.incomplete_channels or ()),
                "query_evidence": getattr(response, "query_evidence", None),
            })
        # Features: same questions, floor and answer-support gate off, so every
        # question has candidates (the gate would otherwise remove exactly the
        # ones a refit needs to see).
        os.environ["SLM_RECALL_NO_FLOOR"] = "1"
        os.environ["SLM_RECALL_NO_SUPPORT_GATE"] = "1"
        by_qid = {row["qid"]: row for row in rows}
        for _set_name, q in _questions(suite, corpus):
            response = engine.recall(q["question"], limit=RECALL_LIMIT, fast=True)
            feats = []
            for r in response.results:
                f = result_features(r)
                f["key"] = memory_to_key.get(r.fact.memory_id or "", "?")
                f["slice"] = slices.get(f["key"])
                feats.append(f)
            row = by_qid[q["qid"]]
            row["candidates"] = feats
            plain = [{k: v for k, v in f.items() if k not in ("key", "slice")} for f in feats]
            row["question_features"] = _question_level(plain)
            row["question_features_by_slice"] = {
                name: _question_level([pf for pf, f in zip(plain, feats) if f["slice"] == name])
                for name in SLICES}
            if q["expected"] != "abstain":
                hits = [f for f in feats if f["key"] in q["expected"]]
                row["expected_features"] = hits[0] if hits else None
        os.environ.pop("SLM_RECALL_NO_FLOOR", None)
        os.environ.pop("SLM_RECALL_NO_SUPPORT_GATE", None)
        by_condition = (measure_conditions(engine, suite, memory_to_key, conditions, log,
                                           corpus=corpus)
                        if conditions else None)
    finally:
        os.environ.pop("SLM_RECALL_NO_FLOOR", None)
        os.environ.pop("SLM_RECALL_NO_SUPPORT_GATE", None)
        engine.close()
    return {"profile": profile, "warm": warm, **summarise(rows, latencies, corpus=corpus),
            "rows": rows, **({"conditions": by_condition} if by_condition else {})}


def summarise(rows: list[dict], latencies: list[float], corpus: str = "en") -> dict:
    nonsense = [r for r in rows if r["set"] == "nonsense"]
    real = [r for r in rows if r["set"] == "real"]
    unanswerable = [r for r in rows if r["set"] == "unanswerable"]
    by_category: dict[str, dict] = {}
    for r in rows:
        block = by_category.setdefault(f'{r["set"]}:{r["category"]}', {"n": 0, "ok": 0})
        block["n"] += 1
        ok = r["abstained"] if r["expected"] == "abstain" else _hit(r)
        block["ok"] += int(ok)
    from superlocalmemory.evaluation.answer_quality import nearest_rank_ms

    return {
        "nonsense_abstain_rate": _rate(sum(r["abstained"] for r in nonsense), len(nonsense)),
        "real_top5_hit_rate": _rate(sum(1 for r in real if _hit(r)), len(real)),
        "real_abstained": sum(r["abstained"] for r in real),
        "unanswerable_abstain_rate": _rate(sum(r["abstained"] for r in unanswerable),
                                           len(unanswerable)),
        "counts": {"nonsense": len(nonsense), "real": len(real), "unanswerable": len(unanswerable)},
        "latency_ms": {"p50": round(nearest_rank_ms(latencies, 50), 1),
                       "p95": round(nearest_rank_ms(latencies, 95), 1)} if latencies else {},
        "by_category": by_category,
        "by_source": _by_source(rows),
        "separation": separation([r for r in rows if "question_features" in r]),
        "slices": {name: _slice_block(rows, name, corpus=corpus) for name in SLICES},
        "failures": {
            "nonsense_answered": [r["qid"] for r in nonsense if not r["abstained"]],
            "real_missed": [r["qid"] for r in real if not _hit(r)],
        },
    }


def _by_source(rows: list[dict]) -> dict:
    """The gate per question file: abstained (nonsense, no-answer) or top-5 (real)."""
    out: dict[str, dict] = {}
    for r in rows:
        block = out.setdefault(r.get("source", r["set"]), {"n": 0, "ok": 0, "misses": []})
        ok = r["abstained"] if r["expected"] == "abstain" else _hit(r)
        block["n"] += 1
        block["ok"] += int(ok)
        if not ok:
            block["misses"].append(r["qid"])
    return out


def _slice_block(rows: list[dict], name: str, corpus: str = "en") -> dict:
    """The gate and the separation as seen from one slice of the store.

    Real questions: those whose memory is in the slice. Nonsense: answered
    "by this slice" when any returned memory belongs to it, so the abstain
    rate says whether this slice's memories let nonsense through.
    """
    slices = _load_corpus_module(corpus).slice_of_key()
    real = [r for r in rows if r["set"] == "real" and r["slice"] == name]
    nonsense = [r for r in rows if r["set"] == "nonsense"]
    answered = [r["qid"] for r in nonsense
                if any(slices.get(k) == name for k in r.get("result_keys", ()))]
    featured = [{**r, "slice_features": r["question_features_by_slice"][name]}
                for r in rows if "question_features_by_slice" in r]
    return {
        "real_n": len(real),
        "real_top5_hit_rate": _rate(sum(1 for r in real if _hit(r)), len(real)),
        "real_missed": [r["qid"] for r in real if not _hit(r)],
        "nonsense_abstain_rate": _rate(len(nonsense) - len(answered), len(nonsense)),
        "nonsense_answered_by_this_slice": answered,
        "separation": separation(featured, field="slice_features", real_slice=name),
    }


def _hit(row: dict) -> bool:
    return row["rank"] is not None and row["rank"] <= TOP_K


def _rate(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def gate_failures(report: dict) -> list[str]:
    """The plan's gate: every nonsense question abstains, every real one hits top 5."""
    problems = []
    for profile in report["profiles"]:
        if profile["nonsense_abstain_rate"] != 1.0:
            problems.append(
                f'{profile["profile"]}: nonsense abstain {profile["nonsense_abstain_rate"]}')
        if profile["real_top5_hit_rate"] != 1.0:
            problems.append(f'{profile["profile"]}: real top-5 {profile["real_top5_hit_rate"]}')
        for name, block in (profile.get("slices") or {}).items():
            for metric in ("nonsense_abstain_rate", "real_top5_hit_rate"):
                if block[metric] not in (None, 1.0):
                    problems.append(f'{profile["profile"]}/{name}: {metric} {block[metric]}')
    return problems


def run(work: Path, profiles: list[str], cache: Path | None = None, *,
        warm_timeout_s: float = 300.0, log=print, suite: str = "hand",
        conditions: list[str] | None = None, embedder: str = "nomic",
        corpus: str = "en") -> dict:
    _bootstrap()
    work.mkdir(parents=True, exist_ok=True)
    _refuse_own_store(work)
    module = _load_corpus_module(corpus)
    store_root = build_store(cache or (work / "cache"), log=log, embedder=embedder,
                             corpus=corpus)
    report = {"corpus_version": module.CORPUS_VERSION, "corpus_digest": module.digest(),
              "top_k": TOP_K, "suite": suite, "embedder": embedder, "profiles": []}
    for profile in profiles:
        report["profiles"].append(measure_profile(profile, store_root, work,
                                                  warm_timeout_s=warm_timeout_s, log=log,
                                                  suite=suite, conditions=conditions,
                                                  embedder=embedder, corpus=corpus))
    report["gate_failures"] = gate_failures(report)
    return report


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--work", required=True, help="scratch folder (stores are copied here)")
    p.add_argument("--cache", help="where the built store is kept (default: WORK/cache)")
    p.add_argument("--profiles", default="a,b")
    p.add_argument("--suite", default="hand", choices=sorted(SUITES),
                   help="question sets: hand-written, + generated dev half, the frozen "
                        "fresh half, or all")
    p.add_argument("--warm-timeout", type=float, default=300.0)
    p.add_argument("--conditions", default="",
                   help="also ask each condition gated and gate-off (merge rule): "
                        "warm,probes_cold,ce_absent,keyword_only")
    p.add_argument("--embedder", default="nomic",
                   help="text model to measure: nomic (shipped) or gemma (EmbeddingGemma 2, "
                        "needs the managed model environment)")
    p.add_argument("--corpus", default="en", choices=["en", "cjk"],
                   help="corpus to measure: en (the recorded baselines) or cjk (the Chinese "
                        "twin, answerability_corpus_cjk)")
    p.add_argument("--out", help="full report (JSON)")
    p.add_argument("--rows", help="per-question rows (JSON Lines)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    if not profiles or any(p not in ("a", "b") for p in profiles):
        print("answerability-eval: --profiles takes a, b or a,b", file=sys.stderr)
        return 2
    if args.embedder not in EMBEDDERS:
        print(f"answerability-eval: --embedder takes {' or '.join(EMBEDDERS)}", file=sys.stderr)
        return 2
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    unknown = sorted(set(conditions) - set(_load_conditions_module().CONDITIONS))
    if unknown:
        print(f"answerability-eval: unknown condition(s) {', '.join(unknown)}", file=sys.stderr)
        return 2
    try:
        report = run(Path(args.work), profiles, Path(args.cache) if args.cache else None,
                     warm_timeout_s=args.warm_timeout, log=lambda m: print(m, file=sys.stderr),
                     suite=args.suite, conditions=conditions, embedder=args.embedder,
                     corpus=args.corpus)
    except Refused as exc:
        print(f"answerability-eval: {exc}", file=sys.stderr)
        return 3
    rows = [row for profile in report["profiles"] for row in
            ({"profile": profile["profile"], **r} for r in profile.pop("rows"))]
    if args.rows:
        Path(args.rows).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                                   encoding="utf-8")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    summary = [{k: v for k, v in p.items() if k not in ("by_category", "separation")}
               for p in report["profiles"]]
    print(json.dumps({"corpus_digest": report["corpus_digest"], "profiles": summary,
                      "conditions": {p["profile"]: p.get("conditions") for p in report["profiles"]},
                      "separation": {p["profile"]: p["separation"] for p in report["profiles"]},
                      "gate_failures": report["gate_failures"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
