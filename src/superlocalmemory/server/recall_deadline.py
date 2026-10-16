# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Bound executor joins, including database-backed serialization and fallback."""

import asyncio


async def wait_until(future, deadline):
    try:
        return await asyncio.wait_for(
            asyncio.shield(future), max(0, deadline - asyncio.get_running_loop().time())
        )
    except asyncio.TimeoutError:
        future.add_done_callback(lambda f: f.cancelled() or f.exception())
        return None


def unjudged(payload):
    if payload.get("degraded_reason") == "recall_assembly_budget_exceeded":
        note = ("Response assembly timed out. Retrieved matches are shown without source context; "
                "the response was not checked.")
    elif payload.get("results"):
        note = ("Full results were unavailable before the recall deadline. "
                "Only keyword fallback matches are shown; they were not checked.")
    else:
        note = ("Full results were unavailable before the recall deadline; "
                "no keyword fallback matches were ready. The request was not checked.")
    return {
        **payload,
        "answerability": "unjudged",
        "answer_check_status": "skipped",
        "answer_check_detail": "budget",
        "answer_check_note": note,
        "answer_check_reason": "budget",
        "answerability_reason": "budget_exhausted",
        "no_confident_match": False,
    }


def timed_out(call, snapshot):
    from superlocalmemory.server.recall_fallback import abandoned_channel_metadata

    return unjudged(
        {
            "ok": True,
            "query": call.query,
            "query_type": "text_search",
            "profile": call.profile_id or snapshot.profile_id,
            "profile_generation": snapshot.generation,
            "results": [],
            "result_count": 0,
            "count": 0,
            "retrieval_mode": "degraded_lexical",
            "degraded_reason": "recall_budget_exceeded",
            **abandoned_channel_metadata(),
        }
    )


def submit(loop, hold, runtime, state, engine, fn, *args):
    from contextlib import nullcontext

    from superlocalmemory.server.recall_admission import current_recall_lease
    from superlocalmemory.server.recall_dispatch import RetainedDispatch

    parent = current_recall_lease()
    retained = parent.fork() if parent is not None else None

    def guarded():
        legacy = getattr(runtime, "operation_nowait", None) if retained is None else None
        with legacy() if legacy else nullcontext(True) as admitted:
            if admitted is None or getattr(state, "engine", engine) is not engine:
                return None
            return fn(*args)

    dispatch = RetainedDispatch(retained, hold, guarded) if retained is not None else None
    if dispatch is not None:
        parent.track(dispatch)
    try:
        if dispatch is not None:
            return loop.run_in_executor(None, dispatch.run)
        return loop.run_in_executor(None, hold.run, guarded)
    except BaseException:
        if dispatch is not None:
            dispatch.abandon()
        raise
