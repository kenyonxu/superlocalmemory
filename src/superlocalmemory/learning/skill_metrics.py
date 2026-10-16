# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Scoped execution windows shared by mining and health telemetry."""

from bisect import bisect_right
from collections import defaultdict


def execution_windows(conn, profile_id, invocations, window):
    grouped = defaultdict(list)
    for row in conn.execute(
        "SELECT id,session_id,project_path,tool_name,event_type,output_summary,created_at "
        "FROM tool_events WHERE profile_id=? ORDER BY id",
        (profile_id,),
    ):
        item = dict(row)
        grouped[(item["session_id"], item["project_path"])].append(item)
    indexes = {key: [event["id"] for event in events] for key, events in grouped.items()}
    result = {}
    for invocation in invocations:
        key = (invocation["session_id"], invocation["project_path"])
        start = bisect_right(indexes.get(key, []), invocation["event_id"])
        trace = []
        for event in grouped.get(key, [])[start : start + window]:
            if event["tool_name"] == "Skill":
                break
            trace.append(event)
        result[invocation["event_id"]] = trace
    return result


def health_entry(name, metrics):
    positives, negatives = metrics["positive_signals"], metrics["negative_signals"]
    samples = positives + negatives
    rate = positives / samples if samples else 0.0
    status = "insufficient_evidence"
    if samples:
        status = "healthy" if rate >= 0.7 else "degraded" if rate >= 0.4 else "critical"
    return dict(
        metrics,
        name=name,
        invocations=metrics["total_invocations"],
        errors=negatives,
        sample_count=samples,
        effective_rate=rate,
        confidence=min(0.95, samples / 20.0),
        status=status,
        approximate=True,
    )
