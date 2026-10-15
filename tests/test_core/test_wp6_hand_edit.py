from types import SimpleNamespace

from superlocalmemory.core.answer_check_reload import changed_on_disk, remember_settings
from superlocalmemory.core.answer_check_state import update
from superlocalmemory.core.config import RetrievalConfig


def test_hand_edit_is_refused_until_the_whole_choice_is_applied(tmp_path):
    engine = SimpleNamespace()
    retrieval = RetrievalConfig()
    remember_settings(engine, retrieval, tmp_path)
    assert not changed_on_disk(engine)
    update(tmp_path, lambda state: {**state, "sufficiency_timeout_s": 2.0}, seed=lambda: {})
    assert changed_on_disk(engine)
    retrieval.sufficiency_timeout_s = 2.0
    remember_settings(engine, retrieval, tmp_path)
    assert not changed_on_disk(engine)


def test_a_cold_judge_reports_warming_even_after_retrieval_spent_the_budget(monkeypatch):
    import time
    from types import SimpleNamespace

    from superlocalmemory.core.answer_check_stage import run_answer_check
    from superlocalmemory.retrieval import laya_set_recipe
    from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult

    monkeypatch.setattr(laya_set_recipe, "refresh", lambda judge: None)
    judge = SimpleNamespace(backend="laya", ready=False, loading=True)
    engine = SimpleNamespace(_sufficiency_judge=judge)
    response = RecallResponse(
        results=[RetrievalResult(AtomicFact(content="Alice approved release."))]
    )
    outcome = run_answer_check(
        engine, "Who approved the release?", response, recall_started=time.monotonic() - 4
    )
    assert outcome.status == "warming"


def test_a_damaged_settings_file_is_refused_even_when_consent_was_already_off(tmp_path):
    from types import SimpleNamespace

    from superlocalmemory.core.answer_check_reload import changed_on_disk, remember_settings
    from superlocalmemory.core.config import RetrievalConfig

    engine = SimpleNamespace(_sufficiency_judge=None)
    remember_settings(engine, RetrievalConfig(sufficiency_judge="laya"), tmp_path)
    (tmp_path / "answer_check.json").write_text("{broken")
    assert changed_on_disk(engine)
