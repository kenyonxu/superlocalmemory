"""The release evidence renderer stays canonical, reviewable and bounded."""

import json
from pathlib import Path

from scripts import integration_compatibility as compat

ROOT = Path(__file__).resolve().parents[2]


def test_generated_evidence_is_bounded_lossless_and_canonical(tmp_path):
    manifest = compat.load_manifest(ROOT / "ide/integration-contracts.json")
    evidence = compat.build_evidence(ROOT, manifest, work_dir=tmp_path)
    rendered = compat._render_evidence(evidence)
    assert json.loads(rendered) == evidence
    assert 1 < len(rendered.splitlines()) < 800
    assert rendered == compat._render_evidence(dict(reversed(list(evidence.items()))))
    assert rendered.endswith("\n")
