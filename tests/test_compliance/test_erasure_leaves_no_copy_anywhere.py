# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""After an erasure no copy of the person's words is left in any file SLM controls.

GDPR Art. 17 asks for the data to be gone, not for the rows we know about to be
gone. This writes a unique marker through every write path (see
``erasure_universe``), erases the profile, and reads the bytes of every file
under the data root and the backup folder for the markers. A backup may still
hold them only if an erasure obligation names that backup and the result says the
erasure is not complete; nothing else may.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.compliance.gdpr import GDPRCompliance
from tests.conftest import force_sync_enrichment
from tests.helpers.env_capabilities import NO_VECTOR_SEARCH_REASON, vector_search_available
from tests.test_compliance.erasure_universe import (
    OTHER,
    PROFILE,
    SEEDERS,
    Universe,
    scan,
)

pytestmark = pytest.mark.skipif(not vector_search_available(), reason=NO_VECTOR_SEARCH_REASON)


@pytest.fixture
def universe(engine_with_mock_deps):
    engine = force_sync_enrichment(engine_with_mock_deps)
    for pid in (PROFILE, OTHER):
        engine._db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name, description) VALUES (?, ?, ?)",
            (pid, pid, f"workspace of {pid}"))
    root = engine._db.db_path.parent
    u = Universe(engine=engine, root=root)
    for seed in SEEDERS:
        seed(u)
    return u


def _covered_backups(root: Path, profile: str) -> set[str]:
    """Backup copies an erasure obligation names (relative to the data root)."""
    from superlocalmemory.infra.backup_obligations import BackupObligationStore

    named: set[str] = set()
    for row in BackupObligationStore(root).list_pending_for_profile(profile):
        path = Path(row["snapshot_path"])
        try:
            named.add(str(path.relative_to(root)))
        except ValueError:
            named.add(str(path))
    return named


def test_every_write_path_actually_wrote_the_marker(universe):
    """The scan is only worth anything if the seeders really persisted the words."""
    found = scan(universe.root, universe.needles)
    seen = {n for hits in found.values() for n in hits}
    # Encrypted or hashed stores cannot show the marker in their bytes; they are
    # checked through their API below.
    api_only = {universe.written[p] for p in ("remember_runtime", "mcp_cache", "ccr_original")
                if p in universe.written}
    missing = [p for p, n in universe.written.items() if n not in seen and n not in api_only]
    assert not missing, f"never persisted their marker, so the scan proves nothing: {missing}"
    assert universe.binary.get("vector"), "no vector was indexed"
    assert "vector" in {n for hits in scan(universe.root, [], universe.binary).values()
                        for n in hits}, "the vector bytes are not in any file: the scan is blind"


def test_erasing_a_profile_leaves_no_copy_of_its_words_anywhere(universe):
    gdpr = GDPRCompliance(universe.engine._db, engine=universe.engine, data_root=universe.root)

    result = gdpr.forget_profile(PROFILE)

    survivors = scan(universe.root, universe.needles, universe.binary)
    covered = _covered_backups(universe.root, PROFILE)
    leaks: dict[str, list[str]] = {}
    for file, needles in survivors.items():
        in_covered_backup = any(file == c or file.startswith(c + "/") for c in covered)
        if in_covered_backup and not result.get("erasure_complete"):
            continue  # an honest obligation: the backup is named and the result says so
        leaks[file] = [universe.path_of(n) if n in universe.written.values() else n
                       for n in needles]
    assert not leaks, f"copies of the erased words remain: {leaks}"


def test_the_result_says_a_backup_still_holds_the_words(universe):
    result = GDPRCompliance(
        universe.engine._db, engine=universe.engine, data_root=universe.root,
    ).forget_profile(PROFILE)

    assert result["backup_obligations_pending"] >= 1
    assert result["erasure_complete"] == 0, "a backup still holds the words"


def test_encrypted_stores_hold_nothing_readable_for_the_erased_profile(universe):
    GDPRCompliance(
        universe.engine._db, engine=universe.engine, data_root=universe.root,
    ).forget_profile(PROFILE)

    cache = universe.ids["llm_cache"]
    assert cache.get_value("k" * 64, "t" * 64) is None, "the cached value is still readable"
    assert cache.ccr_get("11111111-1111-4111-8111-111111111111", tenant_id="t" * 64) is None, \
        "the reversible original is still readable"
    journal = sqlite3.connect(universe.root / "admission_journal.db")
    try:
        left = journal.execute(
            "SELECT COUNT(*) FROM admission_journal WHERE profile_id = ?", (PROFILE,)).fetchone()[0]
    finally:
        journal.close()
    assert left == 0, "the admission journal still holds the person's saved request"


def test_no_physical_vector_survives_an_erasure_made_without_an_engine(universe):
    """The CLI erases with a database handle and no engine; the vectors must still go."""
    import sqlite_vec

    db_path = universe.engine._db.db_path
    fact_id = universe.ids["fact_ids"][0]
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    before = conn.execute("SELECT COUNT(*) FROM fact_embeddings WHERE profile_id = ?",
                          (PROFILE,)).fetchone()[0]
    conn.close()
    assert before >= 1, f"the fact {fact_id} was never indexed, so this proves nothing"

    GDPRCompliance(universe.engine._db, data_root=universe.root).forget_profile(PROFILE)

    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    try:
        after = conn.execute("SELECT COUNT(*) FROM fact_embeddings WHERE profile_id = ?",
                             (PROFILE,)).fetchone()[0]
    finally:
        conn.close()
    assert after == 0, "raw vectors of the erased profile are still in the vector index"
