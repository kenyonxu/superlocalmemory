"""Repair must validate its canonical source as well as its derived base."""

import json
import sqlite3
from contextlib import closing
from unittest.mock import patch

import numpy as np
import pytest

from tests.test_dynamics.test_qjl_upgrade_repair import legacy_store  # noqa: F401


def _commit_writer(db_path, source, change_base):
    with closing(sqlite3.connect(db_path)) as other, other:
        other.execute("UPDATE atomic_facts SET embedding=? WHERE fact_id='repairable'",
                      (source,))
        if change_base:
            other.execute("UPDATE polar_embeddings SET radius=radius+1 "
                          "WHERE fact_id='repairable'")


@pytest.mark.parametrize("phase", ["after_source_read", "before_write"])
@pytest.mark.parametrize("source_format", ["blob", "json"])
@pytest.mark.parametrize("change_base", [False, True])
def test_canonical_writer_after_source_read_leaves_repair_retryable(
    legacy_store, source_format, change_base, phase,  # noqa: F811 - imported pytest fixture
):
    db, store, scheduler, _ = legacy_store
    if source_format == "json":
        db.execute("UPDATE atomic_facts SET embedding=? WHERE fact_id='repairable'",
                   (json.dumps(np.ones(16).tolist()),))
    before = store.load("repairable", "default").qjl_bits
    size_sql = ("SELECT compressed_size_bytes FROM embedding_quantization_metadata "
                "WHERE fact_id='repairable'")
    size_before = db.execute(size_sql)[0][0]
    current = np.arange(16, dtype=np.float32) + 1
    source = current.tobytes() if source_format == "blob" else json.dumps(current.tolist())
    execute = db.execute
    raced = False

    def race(sql, params=()):
        nonlocal raced
        after_read = (sql.startswith("SELECT embedding FROM atomic_facts")
                      and params[0] == "repairable" and phase == "after_source_read")
        before_write = (sql.startswith("UPDATE polar_embeddings SET qjl_bits")
                        and phase == "before_write")
        if before_write:
            _commit_writer(db.db_path, source, change_base)
            raced = True
        rows = execute(sql, params)
        if after_read:
            assert not raced
            _commit_writer(db.db_path, source, change_base)
            raced = True
        return rows

    with patch.object(db, "execute", side_effect=race):
        result = scheduler.run_eap_cycle("default")
    assert raced and result["repair_errors"] == 1 and result["repaired"] == 0
    assert store.load("repairable", "default").qjl_bits == before
    assert db.execute(size_sql)[0][0] == size_before
    source_sql = "SELECT embedding FROM atomic_facts WHERE fact_id='repairable'"
    assert db.execute(source_sql)[0][0] == source
    assert scheduler.run_eap_cycle("default")["repaired"] == 1
    qe = store.load("repairable", "default")
    assert qe.qjl_bits == store._qjl.encode_residual(current - store._polar.decode(qe))
    assert scheduler.run_eap_cycle("default")["repaired"] == 0
