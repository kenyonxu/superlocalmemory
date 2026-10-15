"""The keyword fallback's source check gets its own half second.

On the author's store (27k facts) the keyword query alone took 0.59 s. The
0.5 s source-check budget was counted from before that query, so it was
already spent, the check refused every row, and the fallback returned nothing
for a question with ten supported matches (4.2.2, 13 Oct 2026).
"""

import time
from types import SimpleNamespace

from superlocalmemory.retrieval import fallback_fidelity
from superlocalmemory.server import recall_fallback

ROW = {"fact_id": "f1", "content": "SLM relaunch schedule decided by Varun", "confidence": 0.9}


def test_a_slow_keyword_query_does_not_spend_the_source_check(monkeypatch):
    budgets = []

    def slow_fetch(db, pid, query, limit):
        time.sleep(0.6)
        return [dict(ROW)]

    def admit(candidates, db, pid, budget_s):
        budgets.append(budget_s)
        return (candidates if budget_s > 0 else []), SimpleNamespace(
            source_fidelity_status="checked", incomplete_channels=(), channel_status={},
            project_scope=None, tag_scope=None)

    monkeypatch.setattr(recall_fallback, "_fetch_candidates", slow_fetch)
    monkeypatch.setattr(fallback_fidelity, "admit_fallback", admit)
    monkeypatch.setattr(recall_fallback, "recall_response_metadata", lambda r: {}, raising=False)
    engine = SimpleNamespace(_db=SimpleNamespace(execute=lambda *a: None), profile_id="default")
    out = recall_fallback.recall_keyword_fallback(engine, "SLM relaunch schedule", 5)
    assert budgets and budgets[0] > 0.4
    assert [r["fact_id"] for r in out["results"]] == ["f1"]
