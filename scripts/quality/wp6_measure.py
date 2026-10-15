#!/usr/bin/env python3
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Small offline Nomic ablation. Never opens an existing SLM store.

Builds a fresh SQLite corpus at --output/synthetic.db. Model is local/cache
only; two CPU threads, bounded corpus. Judge rates are explicitly recorded
probability replay, not new Laya inference. --model allows EG2 with the same
floor procedure when its local sentence-transformers weights are available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

PAIRS = [
    ("Postgres connections are pooled through pgbouncer.", "How are database sessions reused?"),
    (
        "My daughter prefers oatmeal with strawberries for breakfast.",
        "Which morning meal does my child enjoy?",
    ),
    ("The migration runs overnight on Sunday.", "When will we move the data?"),
    (
        "Alice Chen approved the Beacon recovery checklist.",
        "Who signed off the rollback procedure?",
    ),
    (
        "Production uses blue green deployment to avoid downtime.",
        "How do we ship without an outage?",
    ),
    (
        "The retry delay doubles after each failed request.",
        "What happens to the wait after repeated errors?",
    ),
    (
        "Keep all customer information on the local machine.",
        "Where should private client records reside?",
    ),
    (
        "The release is blocked until all regression tests pass.",
        "What must succeed before shipping?",
    ),
    ("The vault backup is encrypted with an offline key.", "How is the saved archive protected?"),
    ("Redis caches expire after fifteen minutes.", "When are temporary lookup entries removed?"),
    ("Our DNS provider is Cloudflare.", "Who resolves our domain names?"),
    ("Service logs must be retained for thirty days.", "How long should diagnostics be kept?"),
]
ABSENT = [
    "zxqv plorth wimble",
    "asdf qwer zxcv uiop",
    "Who is the Atlas CEO?",
    "What is the office wifi password?",
    "Which lawyer approved the merger?",
    "What is my passport number?",
    "Who invented quantum bananas?",
    "What is the customer's bank balance?",
    "Where is my cousin's wedding?",
    "What is the travel reimbursement limit?",
    "When does the lease expire?",
    "How much is the dental insurance premium?",
]


def corpus():
    docs = [d for d, _ in PAIRS]
    for i in range(24):
        ids = " ".join(hashlib.sha256(f"deploy-{i}-{j}".encode()).hexdigest() for j in range(12))
        docs.append(f"Deployment receipt {i}. Environment identifiers and trace hashes: {ids}")
    return docs


def synthetic_store(folder, docs):
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "synthetic.db"
    if path.exists():
        raise ValueError("Use a fresh output directory; an existing store is never opened")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, content TEXT)")
        db.executemany("INSERT INTO facts VALUES (?, ?)", enumerate(docs))


def measure(model, docs, queries, mode, model_name, folder):
    import numpy as np

    from superlocalmemory.core.embedding_recipe import dense_text
    from superlocalmemory.retrieval.calibration_metrics import calibrate, rates

    clean, prefix_d, prefix_q, gemma = recipe_inputs(mode, model_name, dense_text)
    dvec = model.encode(
        [prefix_d + clean(d) for d in docs],
        normalize_embeddings=True,
        batch_size=8,
        show_progress_bar=False,
        **({"prompt_name": "Document"} if gemma else {}),
    )
    qvec = model.encode(
        [prefix_q + clean(q) for q in queries],
        normalize_embeddings=True,
        batch_size=8,
        show_progress_bar=False,
        **({"prompt_name": "SearchQuery"} if gemma else {}),
    )
    cosine = true_cosines(qvec, dvec)
    ordered = np.argsort(-cosine, axis=1)
    # Positive labels use the true source; absent labels use the best noise match.
    positive = [float(cosine[i, i]) for i in range(len(PAIRS))]
    negative = [float(row.max()) for row in cosine[len(PAIRS) :]]
    fit = calibrate([(p + 1) / 2 for p in positive], [(p + 1) / 2 for p in negative])
    floor = 2 * fit["threshold"] - 1
    from wp6_synthetic_store import recall_measure
    actual = recall_measure(folder / mode, docs, queries, dvec, qvec, floor)
    actual_p, actual_n = actual_rates(actual)
    return {
        "slm_recall_keep": sum(actual_p) / len(actual_p),
        "slm_recall_reject": sum(actual_n) / len(actual_n),
        "id_gibberish_raw_max": float(cosine[len(PAIRS) : len(PAIRS) + 2, len(PAIRS) :].max()),
        "hit_at_1": sum(i == row[0] for i, row in enumerate(ordered[: len(PAIRS)])) / len(PAIRS),
        "hit_at_5": sum(i in row[:5] for i, row in enumerate(ordered[: len(PAIRS)])) / len(PAIRS),
        "true_raw_mean": sum(positive) / len(positive),
        "gibberish_raw_max": max(negative[:2]),
        "raw_floor": floor,
        "at_previous_floor": rates(positive, negative, 0.60),
        "at_measured_floor": rates(positive, negative, floor),
        "fit": fit,
        "positive_raw": positive,
        "negative_raw": negative,
    }


def true_cosines(questions, documents):
    import numpy as np

    q, d = np.asarray(questions, dtype=np.float64), np.asarray(documents, dtype=np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    d = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
    return q @ d.T


def recipe_inputs(mode, model_name, dense_text):
    clean = dense_text if mode == "normalized" else lambda x: x
    prefix_d, prefix_q = ("", "") if mode == "legacy" else ("search_document: ", "search_query: ")
    gemma = "nomic" not in model_name
    if gemma:
        prefix_d = prefix_q = ""  # EG2's own encode prompts, no Nomic instructions
    return clean, prefix_d, prefix_q, gemma


def actual_rates(actual):
    return ([f"f{i}" in r["ids"] for i, r in enumerate(actual[:len(PAIRS)])],
            [not r["ids"] for r in actual[len(PAIRS):]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True, help="Cached model directory")
    args = parser.parse_args()
    docs = corpus()
    synthetic_store(args.output, docs)
    import torch
    from sentence_transformers import SentenceTransformer

    from superlocalmemory.retrieval.calibration_metrics import rates

    torch.set_num_threads(2)
    model = SentenceTransformer(
        args.model, trust_remote_code=True, local_files_only=True, device="cpu"
    )
    queries = [q for _, q in PAIRS] + ABSENT
    report = {
        mode: measure(model, docs, queries, mode, args.model, args.output)
        for mode in ("legacy", "prefixed", "normalized")
    }
    # Explicit audit recordings: keep/reject logic and attribution exception.
    report["judge_replay"] = {
        "source": "OPS-32 0.1724 / 0.6714; N-12 0.613",
        "live_inference": False,
        "before": rates([0.1724, 0.6714], [0.613], 0.60),
        "after_explicit_attribution": rates([1.0, 1.0], [0.613], 0.60),
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: {
                    f: v[f]
                    for f in (
                        "hit_at_1",
                        "hit_at_5",
                        "true_raw_mean",
                        "gibberish_raw_max",
                        "raw_floor",
                        "at_measured_floor",
                    )
                }
                for k, v in report.items()
                if k != "judge_replay"
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
