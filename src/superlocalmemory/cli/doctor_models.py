# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Guidance on which extraction model to use. It only reads; it recommends and never changes models on its own."""

from __future__ import annotations

from superlocalmemory.core.model_catalog import best_local_llm


def local_model_finding(config, installed: list[str], ram_gb: float | None = None):
    chosen = config.llm.model
    have = {name.removesuffix(":latest") for name in installed if ":cloud" not in name}
    base = chosen.removesuffix(":latest")
    if base not in have:
        return (
            "WARN",
            f"{chosen} is not installed at the configured endpoint",
            "Choose an installed model with slm setup; no model was changed.",
        )
    best = best_local_llm(ram_gb, list(have))
    if best != base and best in have:
        return (
            "WARN",
            f"{chosen} is configured; {best} is the recommended installed model",
            f"Choose {best} explicitly with slm setup; no model was changed.",
        )
    return ("PASS", f"{chosen} is installed; no better installed recommendation", "")


def answer_check_fit_finding(db_path) -> tuple[str, str, str]:
    """Is recall's answer check fitted for the text model this store runs on?

    A store moved to the managed model by "Upgrade memory engine" (4.1.25 to
    4.2.1) runs a check fitted on another model and can refuse
    answers it has (core/support_fit_guard.py). Read-only; never raises.
    """
    import json
    import sqlite3
    from pathlib import Path
    from types import SimpleNamespace

    from superlocalmemory.core.support_fit_guard import live_advice

    path = Path(db_path)
    if not path.is_file():
        return "PASS", "no memory store yet", ""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT live_config FROM embedding_space WHERE id = 1").fetchone()
        finally:
            conn.close()
        live = json.loads(row[0]) if row and row[0] else {}
    except (sqlite3.Error, ValueError):
        return "PASS", "embedding space not recorded", ""
    advice = live_advice(SimpleNamespace(**live)) if live else None
    if advice:
        return "WARN", advice, "slm embedder rollback"
    return "PASS", f"fitted for {live.get('model_name') or 'the configured model'}", ""
