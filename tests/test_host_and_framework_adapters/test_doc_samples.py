"""Execute literal changed quickstarts; no paid calls or owner state."""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def blocks(document, language):
    return re.findall(rf"```{language}\n(.*?)```", (ROOT / document).read_text(), re.S)


@pytest.mark.parametrize("section", ["Visual Studio Code", "Gemini CLI"])
def test_ux41_ide_quickstart(tmp_path, section):
    text = (ROOT / "docs/ide-setup.md").read_text()
    # These are the two advertised names in the body, not the overview table.
    name = "vscode" if section == "Visual Studio Code" else "gemini"
    commands = [
        line.split("#")[0].strip()
        for line in text.splitlines()
        if line.startswith("slm connect " + name) and not line.startswith("slm connect --")
    ]
    assert commands
    for command in commands:
        result = subprocess.run(
            [sys.executable, "-m", "superlocalmemory.cli", *shlex.split(command)[1:]],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.slow
@pytest.mark.parametrize("index", range(9))
def test_ux25_framework_quickstarts(tmp_path, index):
    isolated = {1: "semantic-kernel", 5: "crewai", 7: "google-adk"}.get(index)
    python = (
        str(Path(os.environ["WP7_NATIVE_ENV_ROOT"]) / isolated / "bin/python")
        if isolated
        else sys.executable
    )
    snippet = blocks("docs/framework-adapters.md", "python")[index]
    # Top-level await in notebooks is the only mechanical transformation.
    from tests.test_host_and_framework_adapters.offline_agents import BOOTSTRAP

    bootstrap = BOOTSTRAP if index == 8 else ""
    code = (
        bootstrap
        + "import asyncio\nasync def sample():\n"
        + "".join("    " + line + "\n" for line in snippet.splitlines())
        + "\nasyncio.run(sample())\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [
                str(ROOT / "src"),
                str(ROOT / "ide/integrations/llamaindex"),
                str(ROOT / "ide/integrations/openai-agents"),
                *[
                    str(ROOT / "ide/integrations" / name)
                    for name in (
                        "langgraph",
                        "semantic-kernel",
                        "agent-framework",
                        "langchain",
                        "crewai",
                        "autogen",
                        "google-adk",
                    )
                ],
            ]
        ),
    }
    if isolated:
        env["SLM_ENGINE_PYTHON"] = sys.executable
        env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [python, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
