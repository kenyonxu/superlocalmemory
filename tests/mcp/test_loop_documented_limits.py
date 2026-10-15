"""Verifier L2: input binding cannot make executable code honest."""

import sys

from tests.mcp.test_loop_escape_integrity import db as db
from tests.mcp.test_loop_escape_integrity import register, run, watch

PY = sys.executable


async def test_L2_docs_example_code_under_test_forces_exit_zero(db, tmp_path):
    """Docs example: pinned tests, mutable src/**/*.py. Agent edits ONLY src."""
    cwd = tmp_path / "proj"
    (cwd / "src" / "pkg").mkdir(parents=True)
    (cwd / "tests").mkdir()
    (cwd / "src" / "pkg" / "__init__.py").write_text("")
    (cwd / "src" / "pkg" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (cwd / "tests" / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "from pkg.calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    (cwd / "pytest.ini").write_text("[pytest]\n")
    job = register(db, cwd, [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                   ["tests", "pytest.ini"], ["src/**/*.py"], tmp_path / "slm")
    honest = run(db, job)
    (cwd / "src" / "pkg" / "calc.py").write_text(
        "import os\nos._exit(0)\ndef add(a, b):\n    return a - b\n")
    cheat = run(db, job)
    result = await watch(db, job["job_id"])
    assert honest['verdict'] == 'FAIL'
    assert cheat['verdict'] == 'PASS'
    assert result['status'] == 'DONE'  # Documented limit: executed code can lie.
