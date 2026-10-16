# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Every migration after M051 must say how far back the store can still go.

``prepare_downgrade`` refuses any applied migration newer than the target that
declares neither ``DOWNGRADE_FLOOR`` nor ``BREAKING_VERSION``. Three recall
migrations shipped without one and silently made 4.1.24 unreachable; this
test names the next one that forgets.
"""

from __future__ import annotations

import re

from superlocalmemory.storage._migration_internals import _MODULES
from superlocalmemory.storage.migration_runner import DEFERRED_MIGRATIONS, MIGRATIONS

_SERIAL = re.compile(r"^M(\d{3})_")


def test_every_migration_after_51_declares_a_floor_or_a_break() -> None:
    missing = []
    for migration in (*MIGRATIONS, *DEFERRED_MIGRATIONS):
        match = _SERIAL.match(migration.name)
        if not match or int(match.group(1)) <= 51:
            continue
        module = _MODULES.get(migration.name)
        if getattr(module, "DOWNGRADE_FLOOR", None) is None and not getattr(
                module, "BREAKING_VERSION", 0):
            missing.append(migration.name)
    assert not missing, f"declare DOWNGRADE_FLOOR or BREAKING_VERSION in: {missing}"


def test_recall_migrations_keep_the_way_back_to_4_1_18_open() -> None:
    for name in ("M056_recall_channel_indexes", "M057_temporal_retrieval_version",
                 "M058_recall_fact_cover", "M061_fact_hub_baseline"):
        assert getattr(_MODULES[name], "DOWNGRADE_FLOOR", None) == 51, name
