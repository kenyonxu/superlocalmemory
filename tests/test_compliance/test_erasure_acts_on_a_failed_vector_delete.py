# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A vector delete that failed is a failure of the erasure, not "nothing to delete" (N-05).

``VectorStore.delete`` answered ``False`` for both, logged the failure at debug
level, and the erasure callers ignored the answer, so an erasure could leave a
person's vectors behind without saying so.
"""

from __future__ import annotations

import logging

from superlocalmemory.compliance.gdpr import GDPRCompliance
from superlocalmemory.core.transactions.concrete_owners import VectorOwner
from superlocalmemory.core.transactions.owners import OperationContext
from superlocalmemory.retrieval.vector_store import VectorDelete, classify_delete


class _Store:
    def __init__(self, answers: dict[str, object]) -> None:
        self.available = True
        self._answers = answers

    def indexed_fact_ids(self, profile_id):
        return set(self._answers)

    def delete(self, fact_id):
        answer = self._answers[fact_id]
        if isinstance(answer, Exception):
            raise answer
        return answer


class _Engine:
    def __init__(self, store) -> None:
        self._vector_store = store
        self._ann_index = None


def _purge(answers):
    return GDPRCompliance(db=None, engine=_Engine(_Store(answers)))._purge_vector_and_ann("p")


def test_deleted_absent_and_failed_are_counted_apart() -> None:
    purged, failures = _purge({
        "f1": VectorDelete.DELETED, "f2": VectorDelete.ABSENT, "f3": VectorDelete.FAILED})

    assert (purged, failures) == (1, 1)


def test_an_absent_vector_is_not_a_failure() -> None:
    assert _purge({"f1": VectorDelete.ABSENT}) == (0, 0)


def test_a_store_that_still_answers_a_bare_false_is_read_as_a_failure() -> None:
    """A bare False cannot be told from a failure, so it never counts as success."""
    assert classify_delete(False) is VectorDelete.FAILED
    assert classify_delete(True) is VectorDelete.DELETED
    assert _purge({"f1": False}) == (0, 1)


def test_a_raising_delete_is_a_failure() -> None:
    assert _purge({"f1": RuntimeError("locked")}) == (0, 1)


def test_the_vector_owner_reports_a_failed_delete_instead_of_swallowing_it() -> None:
    store = _Store({"f1": VectorDelete.FAILED})
    owner = VectorOwner(db=None, vector_store=store, ann_index=None)

    result = owner._delete_all(
        OperationContext(operation_id="o", profile_id="p", subject_id="f1", fact_ids=("f1",)),
        phase="erase")

    assert result.ok is False and "f1" in str(result.detail)


def test_the_vector_owner_does_not_fail_on_an_absent_vector() -> None:
    owner = VectorOwner(db=None, vector_store=_Store({"f1": VectorDelete.ABSENT}), ann_index=None)

    result = owner._delete_all(
        OperationContext(operation_id="o", profile_id="p", subject_id="f1", fact_ids=("f1",)),
        phase="erase")

    assert result.ok is True


def test_a_failed_memory_delete_warns(caplog) -> None:
    from types import SimpleNamespace

    from superlocalmemory.core.mutations import _sync_vector_ann

    retrieval = SimpleNamespace(_vector_store=_Store({"f1": VectorDelete.FAILED}), _ann_index=None)
    with caplog.at_level(logging.WARNING):
        _sync_vector_ann(retrieval, "f1", "p", None, operation="delete")

    assert any("a vector may remain" in r.message for r in caplog.records)


def test_the_ann_index_loses_the_fact_in_both_paths() -> None:
    """With a store and without one, the in-memory index must not keep the erased fact."""
    class Ann:
        def __init__(self) -> None:
            self.gone: list[str] = []

        def remove(self, fact_id: str) -> None:
            self.gone.append(fact_id)

    ctx = OperationContext(operation_id="o", profile_id="p", subject_id="f1", fact_ids=("f1",))
    for store in (_Store({"f1": VectorDelete.DELETED}), None):
        ann = Ann()
        owner = VectorOwner(db=type("D", (), {"db_path": None, "execute": lambda *a, **k: []})(),
                            vector_store=store, ann_index=ann)
        assert owner._delete_all(ctx, phase="erase").ok
        assert ann.gone == ["f1"], store
