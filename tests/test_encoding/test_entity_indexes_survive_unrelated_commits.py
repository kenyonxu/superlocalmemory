"""Recall's entity indexes are kept while the entity rows are (4.2.4).

Keyed on the whole store file, any commit -- a hook note, an access count --
rebuilt them: on the author's 16,800-entity store the lookup index cost ~0.35 s
and the spelling index 2.7-5.4 s, nearly every recall. They now follow the
entity tables: an unrelated write keeps them, an added entity is folded in, a
removal or an in-place repair rebuilds.
"""

import sqlite3

import pytest

from superlocalmemory.encoding import entity_lookup_index as eli
from superlocalmemory.encoding.entity_name_cache import CanonicalNames


class _Db:
    def __init__(self, path):
        self.db_path = str(path)
        self._txn_state = None
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.reads = 0

    def execute(self, sql, params=()):
        if "canonical_name, entity_type FROM canonical_entities WHERE profile_id = ?" == sql[-70:] \
                or sql.startswith("SELECT entity_id, canonical_name, entity_type"):
            self.reads += 1
        rows = self.conn.execute(sql, params).fetchall()
        self.conn.commit()
        return rows


@pytest.fixture
def db(tmp_path):
    d = _Db(tmp_path / "memory.db")
    d.conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE canonical_entities (entity_id TEXT PRIMARY KEY, profile_id TEXT,
            canonical_name TEXT, entity_type TEXT, last_seen TEXT, fact_count INTEGER);
        CREATE TABLE entity_aliases (alias_id TEXT PRIMARY KEY, entity_id TEXT, alias TEXT,
            confidence REAL, source TEXT, profile_id TEXT);
        CREATE TABLE notes (x TEXT);
        INSERT INTO canonical_entities VALUES ('e1','default','Product Hunt','org','',1);
        INSERT INTO canonical_entities VALUES ('e2','default','Hacker News','org','',1);
    """)
    d.conn.commit()
    return d


def _builds(monkeypatch, index):
    calls = []
    real = index._build

    def counting(pid):
        calls.append(pid)
        return real(pid)

    monkeypatch.setattr(index, "_build", counting)
    return calls


def test_an_unrelated_commit_keeps_the_lookup_index(db, monkeypatch):
    index = eli.EntityLookupIndex(db)
    builds = _builds(monkeypatch, index)
    first = index.get("default")
    db.execute("INSERT INTO notes VALUES ('a hook wrote this')")
    db.execute("UPDATE canonical_entities SET last_seen='now', fact_count=2 WHERE entity_id='e1'")
    assert index.get("default") is first and builds == ["default"]


def test_an_added_entity_is_folded_in_without_a_rebuild(db, monkeypatch):
    index = eli.EntityLookupIndex(db)
    builds = _builds(monkeypatch, index)
    index.get("default")
    db.execute("INSERT INTO canonical_entities VALUES ('e3','default','Mastodon','org','',1)")
    grown = index.get("default")
    assert grown.by_name["mastodon"] == "e3" and grown.by_name["product hunt"] == "e1"
    assert builds == ["default"]


def test_a_removal_or_an_in_place_repair_rebuilds(db, monkeypatch):
    index = eli.EntityLookupIndex(db)
    builds = _builds(monkeypatch, index)
    index.get("default")
    db.execute("DELETE FROM canonical_entities WHERE entity_id='e2'")
    assert "hacker news" not in index.get("default").by_name
    eli.invalidate()
    index.get("default")
    assert builds == ["default"] * 3


def test_the_lookup_index_is_rebuilt_after_its_age_limit(db, monkeypatch):
    now = [1000.0]
    index = eli.EntityLookupIndex(db, clock=lambda: now[0])
    builds = _builds(monkeypatch, index)
    index.get("default")
    now[0] += eli.MAX_AGE_S + 1
    index.get("default")
    assert builds == ["default", "default"]


def test_the_spelling_index_grows_instead_of_rebuilding(db):
    names = CanonicalNames(db)
    first = names.index("default")
    db.execute("INSERT INTO canonical_entities VALUES ('e3','default','Bluesky','org','',1)")
    assert names.index("default") is first  # same object, grown in place
    assert first.best("Bluesky")[1] == "e3"
    db.execute("DELETE FROM canonical_entities WHERE entity_id='e3'")
    assert names.index("default") is not first  # a removal rebuilds


def test_the_daemon_warm_up_builds_both_name_indexes():
    from types import SimpleNamespace

    from superlocalmemory.retrieval.entity_graph_warmup import warm_names

    seen = []
    resolver = SimpleNamespace(_lookup_rows=lambda pid: seen.append(("rows", pid)),
                               _names=SimpleNamespace(index=lambda pid: seen.append(("names", pid))))
    warm_names(SimpleNamespace(_resolver=resolver), "default")
    assert seen == [("rows", "default"), ("names", "default")]
    warm_names(SimpleNamespace(), "default")  # no resolver: nothing, no error
