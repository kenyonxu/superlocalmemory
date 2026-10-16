# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The cached audit check agrees with the full walk, and still finds damage."""

import sqlite3

import pytest

from superlocalmemory.compliance import audit_verify_cache as cache
from superlocalmemory.compliance.audit import AuditChain


@pytest.fixture(autouse=True)
def _fresh():
    cache.forget()
    yield
    cache.forget()


def _chain(tmp_path, n=20):
    chain = AuditChain(str(tmp_path / "audit_chain.db"))
    for i in range(n):
        chain.log("recall", agent_id="a", profile_id="default", metadata={"i": i})
    return chain


def test_cached_report_matches_the_full_walk_as_entries_are_added(tmp_path):
    chain = _chain(tmp_path)
    assert cache.cached_report(chain) == chain.verify_report()
    for i in range(5):
        chain.log("store", agent_id="a", profile_id="default", metadata={"n": i})
        assert cache.cached_report(chain) == chain.verify_report()
    assert cache.cached_report(chain)["entries"] == 25


def test_a_later_ask_reads_only_what_was_added(tmp_path, monkeypatch):
    chain = _chain(tmp_path)
    cache.cached_report(chain)
    chain.log("store", agent_id="a", profile_id="default")

    def refuse(self):
        raise AssertionError("the whole trail was read again")

    monkeypatch.setattr(AuditChain, "load_rows", refuse)
    assert cache.cached_report(chain)["entries"] == 21


def test_a_new_entry_that_does_not_link_is_found_at_once(tmp_path):
    chain = _chain(tmp_path)
    cache.cached_report(chain)
    conn = sqlite3.connect(chain._db_path)
    conn.execute("INSERT INTO audit_chain (timestamp, operation, agent_id, profile_id, content_hash, "
                 "prev_hash, event_hash, metadata, hash_version) VALUES "
                 "('2026-01-01T00:00:00+00:00','x','a','default','','wrong','abc','{}',2)")
    conn.commit()
    conn.close()
    report = cache.cached_report(chain)
    assert report["ok"] is False


def test_a_removed_tail_is_found(tmp_path):
    chain = _chain(tmp_path)
    cache.cached_report(chain)
    conn = sqlite3.connect(chain._db_path)
    conn.execute("DELETE FROM audit_chain WHERE id > 15")
    conn.commit()
    conn.close()
    assert cache.cached_report(chain)["ok"] is False


def test_an_edit_to_an_old_entry_is_found_by_the_full_recheck(tmp_path, monkeypatch):
    chain = _chain(tmp_path)
    cache.cached_report(chain)
    conn = sqlite3.connect(chain._db_path)
    conn.execute("UPDATE audit_chain SET operation = 'forged' WHERE id = 3")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cache, "FULL_RECHECK_S", 0.0)
    cache.cached_report(chain)  # starts the background walk
    for thread in __import__("threading").enumerate():
        if thread.name == "slm-audit-recheck":
            thread.join(10)
    assert cache.cached_report(chain)["ok"] is False
    assert chain.verify_report()["ok"] is False
