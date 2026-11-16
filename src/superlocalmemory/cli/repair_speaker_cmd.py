# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""``slm repair-speaker``: re-derive facts an agent's "I" turned into "the user" (#265).

Before 4.2.7 every tool-call save was extracted as the user speaking, so an
agent writing "I prefer short answers" got the fact "the user prefers short
answers". The saved memory itself was always intact. This finds those memories
(saved by an agent, first person outside quotes, a derived fact naming "the
user") and, with ``--yes``, saves each again as its agent with ``replaces`` set
to the old memory: the new facts are derived correctly and the old ones are
retired through the correction ledger, so ``review_correction`` can undo it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FIRST_PERSON = re.compile(r"\b(?:I|I['’](?:m|ve|ll|d)|me|my|myself)\b")


@dataclass(frozen=True)
class Candidate:
    memory_id: str
    agent: str
    preview: str


def find_candidates(db_path: Path, profile_id: str) -> list[Candidate]:
    """Memories whose derived facts misattribute an agent's first person."""
    from superlocalmemory.encoding.speaker_spans import _TURN, outside_quotes

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT memory_id, content, role, metadata_json FROM memories "
            "WHERE profile_id = ?", (profile_id,),
        ).fetchall()
        found: list[Candidate] = []
        for memory_id, content, role, meta_json in rows:
            try:
                agent = str(json.loads(meta_json or "{}").get("agent_id") or "")
            except (ValueError, AttributeError):
                continue
            if not agent or role not in ("user", "") or _TURN.match(content.lstrip()):
                continue
            hits: list[str] = []
            outside_quotes(content, lambda part: (hits.extend(_FIRST_PERSON.findall(part)), part)[1])
            if not hits:
                continue
            wrong = conn.execute(
                "SELECT 1 FROM atomic_facts WHERE memory_id = ? AND profile_id = ? "
                "AND lower(content) LIKE '%the user%' LIMIT 1", (memory_id, profile_id),
            ).fetchone()
            if wrong:
                found.append(Candidate(memory_id, agent, " ".join(content.split())[:80]))
        return found
    finally:
        conn.close()


def _resave(memory_id: str, agent: str, db_path: Path, profile_id: str) -> dict[str, Any]:
    from superlocalmemory.cli.daemon import daemon_request

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        content, session_id, session_date, meta_json, scope = conn.execute(
            "SELECT content, session_id, session_date, metadata_json, scope "
            "FROM memories WHERE memory_id = ? AND profile_id = ?", (memory_id, profile_id),
        ).fetchone()
    finally:
        conn.close()
    meta = json.loads(meta_json or "{}")
    body = {
        "content": content,
        "tags": meta.get("tags", "") if isinstance(meta.get("tags"), str) else "",
        "metadata": {"agent_id": agent, **({"project": meta["project"]} if meta.get("project") else {})},
        "scope": scope or "personal",
        "session_id": session_id or "",
        "session_date": session_date or "",
        "speaker": "" if agent == "mcp_client" else agent,
        "replaces": memory_id,
        "idempotency_key": f"repair-speaker:{memory_id}",
    }
    return daemon_request("POST", "/remember", body, timeout_seconds=60) or {}


def cmd_repair_speaker(args: Any) -> None:
    from superlocalmemory.core.config import SLMConfig

    use_json = bool(getattr(args, "json", False))
    apply = bool(getattr(args, "yes", False))
    config = SLMConfig.load()
    profile_id = getattr(config, "active_profile", "") or "default"
    candidates = find_candidates(Path(config.db_path), profile_id)
    repaired: list[str] = []
    errors: dict[str, str] = {}
    if apply:
        for c in candidates:
            try:
                result = _resave(c.memory_id, c.agent, Path(config.db_path), profile_id)
                if result.get("status") in {"stored", "accepted", "queryable"} or result.get("fact_ids"):
                    repaired.append(c.memory_id)
                else:
                    errors[c.memory_id] = str(result.get("error") or result)[:200]
            except Exception as exc:  # noqa: BLE001 - report each failure, keep going
                errors[c.memory_id] = str(exc)[:200]
    if use_json:
        from superlocalmemory.cli.json_output import json_print
        json_print("repair-speaker", data={
            "applied": apply,
            "candidates": [c.__dict__ for c in candidates],
            "repaired": repaired, "errors": errors,
        })
        return
    if not candidates:
        print("No memories need repair: no agent's first person was saved as the user.")
        return
    verb = "Repaired" if apply else "Would repair"
    print(f"{verb} {len(candidates)} memories saved by an agent:")
    for c in candidates:
        mark = "  x " if c.memory_id in errors else "    "
        print(f"{mark}{c.memory_id}  ({c.agent})  {c.preview}")
    if not apply:
        print("Dry run. Re-run with --yes to re-derive their facts. "
              "Undo any one with: slm review-correction")
    elif errors:
        print(f"{len(errors)} failed; see above.", file=sys.stderr)
        sys.exit(1)
