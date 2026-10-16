# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Bounded MCP watcher; completion has an independent execution authority."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from superlocalmemory.loops import (
    Bounds,
    LapResult,
    LedgerEntry,
    Verdict,
    engine_backed_ledger,
    pool_backed_ledger,
    run_bounded_loop,
)
from superlocalmemory.loops.receipt_gate import receipt_status
from superlocalmemory.loops.stop_reasons import last_gate_reason
from superlocalmemory.mcp import tools_loops as shared
from superlocalmemory.mcp.agent_context import get_current_agent_id
from superlocalmemory.retrieval.answer_check_status import REQUEST_NO_REORDER
from superlocalmemory.retrieval.answerability import SUPPORTED, of_envelope, of_response


def _score(result):
    if isinstance(result, dict):
        value = result.get("score", result.get("relevance_score", 0.0))
    else:
        value = getattr(result, "score", None)
        if value is None:
            value = getattr(result, "relevance_score", 0.0) or 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _external_locator(result):
    if isinstance(result, dict):
        fact = result.get("fact") or result
        content = fact.get("content", "") if isinstance(fact, dict) else ""
    else:
        content = getattr(getattr(result, "fact", None), "content", "")
    return LedgerEntry.from_json(str(content or "")) is None


class Watcher:
    def __init__(self, engine, pool, query, minimum, require_support, contract, agent, name=""):
        self.engine, self.pool, self.query = engine, pool, query
        self.minimum, self.require_support, self.contract, self.agent = (
            minimum,
            require_support,
            contract,
            agent,
        )
        self.name, self.started_at = name, datetime.now(UTC).isoformat()

    def _read(self):
        if self.pool is not None:
            response = self.pool.recall(
                self.query,
                limit=shared._GATE_LIMIT,
                fast=True,
                **shared._gate_recall_kwargs(self.pool.recall),
            )
            if not isinstance(response, dict) or response.get("ok") is False:
                raise RuntimeError("owned SLM reader rejected recall")
            return (
                response.get("results", []) or [],
                bool(response.get("no_confident_match", False)),
                response.get("abstention_reason"),
                of_envelope(response),
            )
        response = self.engine.recall(
            self.query, limit=shared._GATE_LIMIT, fast=True, answer_check=REQUEST_NO_REORDER
        )
        return (
            getattr(response, "results", None) or [],
            bool(getattr(response, "no_confident_match", False)),
            getattr(response, "abstention_reason", None),
            of_response(response),
        )

    def gate(self, lap):
        found, floor, reason, answerable = self._read()
        results = [result for result in found if _external_locator(result)]
        best = max((_score(result) for result in results), default=0.0)
        independent, self.receipt_reason = receipt_status(
            self.engine, self.contract, self.agent,
            watcher_name=self.name, started_at=self.started_at,
        )
        passed = (
            bool(results)
            and not floor
            and reason != "judged_insufficient"
            and best >= self.minimum
            and (not self.require_support or answerable[0] == SUPPORTED)
            and independent
        )
        detail = (
            f"recall '{self.query[:48]}': hits={len(results)} top={best:.3f} floor={floor} "
            f"judged_insufficient={reason == 'judged_insufficient'} "
            f"answerability={answerable[0]}:{answerable[1]} independent_receipt={independent} "
            f"receipt_reason={self.receipt_reason}"
        )
        return Verdict(passed, detail)


def _limits(iterations, wall, poll, tokens, no_progress):
    iterations = max(1, min(int(iterations), shared._MAX_ITERATIONS))
    wall = min(float(wall), shared._MAX_WALLCLOCK_S) if wall and float(wall) > 0 else None
    poll = max(shared._MIN_POLL_S, min(float(poll), wall or shared._MAX_WALLCLOCK_S))
    tokens = int(tokens) if tokens and int(tokens) > 0 else None
    return Bounds(
        max_iterations=iterations,
        max_tokens=tokens,
        max_wallclock_s=wall,
        no_progress_window=int(no_progress) if no_progress and int(no_progress) > 0 else 0,
    ), poll


async def run_loop(
    get_engine,
    get_pool,
    name,
    gate_query,
    gate_min_score,
    max_iterations,
    max_wallclock_s,
    poll_interval_s,
    max_tokens,
    no_progress_window,
    require_support,
    gate_contract,
):
    try:
        name, query = (name or "").strip(), (gate_query or "").strip()
        if not name or len(name) > shared._MAX_NAME_CHARS:
            return {"ok": False, "error": f"name must be 1–{shared._MAX_NAME_CHARS} chars"}
        if not query or len(query) > shared._MAX_QUERY_CHARS:
            return {"ok": False, "error": f"gate_query must be 1–{shared._MAX_QUERY_CHARS} chars"}
        engine = get_engine()
        pool = get_pool() if get_pool is not None else None
        watcher = Watcher(
            engine,
            pool,
            query,
            float(gate_min_score),
            require_support,
            gate_contract,
            get_current_agent_id(),
            name,
        )
        bounds, poll = _limits(
            max_iterations, max_wallclock_s, poll_interval_s, max_tokens, no_progress_window
        )

        return await _watch(name, engine, pool, watcher, bounds, poll)
    except Exception as exc:
        shared.logger.exception("slm_loop_run failed")
        return {"ok": False, "error": str(exc)}


async def _watch(name, engine, pool, watcher, bounds, poll):
    def runner(lap):
        if lap > 1:
            time.sleep(poll)
        return LapResult(changed=False, tokens=0)

    ledger = pool_backed_ledger(pool, engine) if pool is not None else engine_backed_ledger(engine)
    outcome = await asyncio.to_thread(
        run_bounded_loop, name, bounds=bounds, runner=runner, gate=watcher.gate, ledger=ledger
    )
    laps = await asyncio.to_thread(ledger.laps, outcome.run_id)
    result = _outcome(outcome, laps)
    result["receipt_reason"] = getattr(watcher, "receipt_reason", "no receipt examined")
    result["bound_reason"] = outcome.reason
    if result['status'] == 'HALT':
        reason = result['receipt_reason']
        if reason in ('authenticated PASS for current copied revision', 'no receipt examined'):
            reason = last_gate_reason(laps)
        result['reason'] = reason or result['reason']
    return result


def _outcome(outcome, laps):
    return {
        "ok": True,
        "status": outcome.status.value,
        "reason": outcome.reason,
        "passed": bool(outcome.ok),
        "laps": outcome.laps,
        "run_id": outcome.run_id,
        "ledger": [
            {"lap": e.lap, "decision": e.decision, "passed": e.passed, "detail": e.detail}
            for e in laps
        ],
        "note": "Independent execution receipts decide completion; memory claims cannot pass.",
    }
