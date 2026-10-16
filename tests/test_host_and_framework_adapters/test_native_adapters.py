"""Installed adapter wheels, real framework objects, canonical engine persistence."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = {
    "crewai": """
from crewai.memory.types import MemoryRecord
from crewai.memory.storage.backend import StorageBackend
from crewai_superlocalmemory import SuperLocalMemoryBackend
backend = SuperLocalMemoryBackend()
assert isinstance(backend, StorageBackend)
record = MemoryRecord(content='Ada prefers tea', scope='/project', embedding=[1.0, 0.0],
                      categories=['preference'], metadata={'nested': {'source': 'fixture'}})
backend.save([record])
got = backend.get_record(record.id)
assert got.content == record.content and got.metadata == record.metadata
assert backend.search([1.0, 0.0], scope_prefix='/project')
backend.close()
""",
    "google-adk": """
import asyncio
from google.adk.sessions import Session
from google.adk.events import Event
from google.genai import types
from google_adk_superlocalmemory import SuperLocalMemoryService
async def run():
    service = SuperLocalMemoryService()
    event = Event(author='user', content=types.Content(parts=[types.Part(text='Ada prefers tea')]))
    session = Session(id='s1', app_name='app', user_id='u1', events=[event])
    await service.add_session_to_memory(session)
    found = await service.search_memory(app_name='app', user_id='u1', query='Ada prefers tea')
    assert found.memories[0].content.parts[0].text == 'Ada prefers tea'
    service.close()
asyncio.run(run())
""",
    "semantic-kernel": """
import asyncio
from dataclasses import dataclass
from typing import Annotated
from semantic_kernel.data.vector import VectorStoreField, vectorstoremodel
from semantic_kernel_superlocalmemory import SuperLocalMemoryVectorStore
@vectorstoremodel
@dataclass
class Doc:
    id: Annotated[str, VectorStoreField('key')]
    text: Annotated[str, VectorStoreField('data')]
async def run():
    store = SuperLocalMemoryVectorStore()
    collection = store.get_collection(Doc, collection_name='docs')
    await collection.ensure_collection_exists()
    await collection.upsert(Doc(id='d1', text='Ada prefers tea'))
    assert (await collection.get('d1')).text == 'Ada prefers tea'
    collection.close()
asyncio.run(run())
""",
}


@pytest.mark.slow
@pytest.mark.parametrize("name", SCRIPTS)
def test_installed_native_adapter(name, tmp_path):
    python = Path(os.environ["WP7_NATIVE_ENV_ROOT"]) / name / "bin/python"
    env = {**os.environ, "SLM_ENGINE_PYTHON": sys.executable}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(python), "-c", SCRIPTS[name]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    checked = subprocess.run(
        ["uv", "pip", "check", "--python", str(python)], capture_output=True, text=True, timeout=30
    )
    assert checked.returncode == 0, checked.stderr


@pytest.mark.slow
def test_crewai_native_on_python312(tmp_path):
    python = Path(os.environ["WP7_NATIVE_ENV_ROOT"]) / "crewai312/bin/python"
    env = {**os.environ, "SLM_ENGINE_PYTHON": sys.executable}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(python), "-c", SCRIPTS["crewai"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_crewai_pip_refuses_python314(tmp_path):
    python = Path(os.environ["WP7_NATIVE_ENV_ROOT"]) / "crewai314/bin/python"
    project = Path(__file__).resolve().parents[2] / "ide/integrations/crewai"
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "--dry-run", "--ignore-installed", str(project)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode != 0
    assert "crewai-superlocalmemory" in result.stderr
    assert "requires a different Python" in result.stderr
