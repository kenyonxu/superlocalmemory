"""The emergency FTS response must withhold live damaged derivatives too."""

import pytest

from superlocalmemory.mcp import tools_active
from tests.test_core.test_wp5b_fidelity_failure import SOURCE, _candidates


@pytest.mark.parametrize("broken_lineage", [False, True])
def test_emergency_fts_admits_only_verified_sources(tmp_path, monkeypatch, broken_lineage):
    db, _ = _candidates(tmp_path)
    if broken_lineage:
        db.execute("DROP TABLE derivation_lineage")
    monkeypatch.setattr(tools_active, "state_path", lambda name: db.db_path)
    response = tools_active._sqlite_emergency_recall("publish", 10, max_age_days=0)
    assert [r.fact.content for r in response.results] == [SOURCE]
    assert response.metadata["source_fidelity_status"] == (
        "degraded" if broken_lineage else "checked")
    assert bool(response.metadata["source_fidelity_note"]) == broken_lineage
    db.close()
