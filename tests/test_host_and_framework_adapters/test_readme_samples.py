"""Execute every Python example in the three changed adapter READMEs."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.parametrize("name", ["google-adk", "crewai", "semantic-kernel"])
def test_changed_readme_examples(name, tmp_path):
    text = (ROOT / "ide/integrations" / name / "README.md").read_text()
    examples = re.findall(r"```python\n(.*?)```", text, re.S)
    assert examples
    python = Path(os.environ["WP7_NATIVE_ENV_ROOT"]) / name / "bin/python"
    env = {**os.environ, "SLM_ENGINE_PYTHON": sys.executable}
    env.pop("PYTHONPATH", None)
    for example in examples:
        code = (
            "import asyncio\nasync def sample():\n"
            + "".join("    " + line + "\n" for line in example.splitlines())
            + "\nasyncio.run(sample())\n"
        )
        result = subprocess.run(
            [str(python), "-c", code],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
