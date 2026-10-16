"""Workspace admins approve their own candidates without changing global skills."""

# ruff: noqa: F811
import hashlib
from pathlib import Path

from tests.test_governance.test_role_surface_matrix_rest import _company, matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


def _candidate(root, profile):
    from superlocalmemory.evolution.evolution_store import EvolutionStore
    from superlocalmemory.evolution.types import (
        EvolutionRecord,
        EvolutionStatus,
        EvolutionType,
        TriggerType,
    )

    store = EvolutionStore(root / "memory.db")
    record = EvolutionRecord(
        id=f"{profile}-candidate",
        skill_name="shared-name",
        parent_skill_id=None,
        evolution_type=EvolutionType.FIX,
        trigger=TriggerType.HEALTH_CHECK,
        status=EvolutionStatus.VERIFIED_QUARANTINED,
    )
    store.insert_record(record, profile)
    directory = root / "quarantine" / "skills" / f"{profile}-artifact"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"{profile} instructions")
    store.append_transition(
        record.id,
        profile,
        EvolutionStatus.CANDIDATE,
        EvolutionStatus.VERIFIED_QUARANTINED,
        metadata={"quarantine_dir_name": directory.name},
    )
    return record.id


def _reviewed(profile):
    """The hash of the bytes the reviewer read; approval must name it."""
    return hashlib.sha256(f"{profile} instructions".encode()).hexdigest()


def test_admin_approves_and_rolls_back_only_own_workspace(matrix):
    from superlocalmemory.server.routes import evolution

    app, client, who, _ = matrix
    _company(app)
    own = _candidate(evolution.MEMORY_DIR, "sales")
    foreign = _candidate(evolution.MEMORY_DIR, "default")
    headers = who["admin_b"]
    approved = client.post("/api/evolution/approve", json={"record_id": own, "reviewed_hash": _reviewed("sales")},
                           headers=headers)
    assert approved.status_code == 200 and approved.json()["success"] is True, approved.text
    live = Path(approved.json()["live_path"])
    assert live.is_relative_to(evolution.MEMORY_DIR)
    assert live.read_text() == "sales instructions"
    denied = client.post("/api/evolution/approve", json={"record_id": foreign, "reviewed_hash": _reviewed("default")},
                         headers=headers)
    assert denied.json()["success"] is False
    rolled = client.post(
        "/api/evolution/rollback",
        headers=headers,
        json={"skill_name": "shared-name", "record_id": own},
    )
    assert rolled.status_code == 200 and rolled.json()["rolled_back"] is True, rolled.text
    assert not live.exists()


def test_scoped_admin_cannot_approve_installation_skills(matrix):
    app, client, who, _ = matrix
    _company(app)
    response = client.post(
        "/api/evolution/approve",
        headers=who["admin_b"],
        json={"record_id": "anything", "installation_wide": True},
    )
    assert response.status_code == 403


def test_scoped_admin_can_run_but_cannot_configure_evolution(matrix):
    app, client, who, _ = matrix
    _company(app)
    response = client.post("/api/evolution/run", headers=who["admin_b"])
    assert response.status_code == 200, response.text
    for path in ("/api/evolution/enable", "/api/evolution/disable", "/api/evolution/config"):
        assert client.post(path, json={}, headers=who["admin_b"]).status_code == 403


def test_global_activation_receipt_requires_global_rollback(matrix):
    from superlocalmemory.evolution.evolution_store import EvolutionStore
    from superlocalmemory.server.routes import evolution
    from tests.test_governance.test_role_surface_matrix_rest import _global_admin

    app, client, who, ids = matrix
    _global_admin(app, who, ids)
    _company(app)
    record_id = _candidate(evolution.MEMORY_DIR, 'sales')
    client.post('/api/profiles/sales/switch', headers=who['root_admin'])
    approved = client.post('/api/evolution/approve', headers=who['root_admin'],
                           json={'record_id': record_id, 'installation_wide': True,
                                 'reviewed_hash': _reviewed('sales')})
    assert approved.json()['success'] is True, approved.text
    live = Path(approved.json()['live_path'])
    denied = client.post('/api/evolution/rollback', headers=who['admin_b'],
                         json={'record_id': record_id, 'skill_name': 'shared-name'})
    assert denied.status_code == 403, denied.text
    assert live.read_text() == 'sales instructions'
    store = EvolutionStore(evolution.MEMORY_DIR / 'memory.db')
    assert store.get_latest_status(record_id, 'sales').value == 'active'


def test_workspace_quarantine_and_live_sources_are_isolated(tmp_path, monkeypatch):
    from superlocalmemory.evolution.skill_evolver import SkillEvolver
    from superlocalmemory.evolution.types import EvolutionCandidate, EvolutionType, TriggerType
    from superlocalmemory.server.caller_workspace import CallerWorkspace
    from superlocalmemory.server.profile_runtime import pin_request_workspace
    from superlocalmemory.server.routes import evolution

    monkeypatch.setattr(evolution, 'MEMORY_DIR', tmp_path)
    paths = {}
    for profile in ('default', 'sales'):
        with pin_request_workspace(CallerWorkspace(profile, bound=True)):
            activator = evolution._activator(False)
        evolver = SkillEvolver.__new__(SkillEvolver)
        evolver._activator = activator
        candidate = EvolutionCandidate(skill_name='same-skill',
                                       evolution_type=EvolutionType.DERIVED,
                                       trigger=TriggerType.HEALTH_CHECK)
        path, directory = evolver._write_evolved_skill(candidate, profile, f'{profile}-id')
        paths[profile] = path
        activator.activate('same-skill', directory)
        assert evolver._read_skill_content('same-skill') == profile
    assert paths['default'] != paths['sales']
    assert paths['default'].read_text() == 'default'
    assert paths['sales'].read_text() == 'sales'
