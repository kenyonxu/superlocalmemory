"""Native framework contracts run in a dependency-isolated interpreter."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_ux27_query_decodes_owned_envelope():
    path = ROOT / "ide/integrations/autogen/autogen_superlocalmemory/_v3_autogen_store.py"
    spec = importlib.util.spec_from_file_location("autogen_store_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = {"project": "R2", "nested": {"source": "contract"}, "fact_id": "caller-value"}
    envelope = json.dumps(
        {
            "adapter": "autogen memory",
            "content": "Ada likes tea",
            "mime_type": "text/plain",
            "metadata": metadata,
        }
    )
    fact = SimpleNamespace(content=envelope, fact_id="retrieval-id", session_id="autogen-mem:1")
    native_json = '{"content":"ordinary JSON","metadata":{"keep":true}}'
    other = SimpleNamespace(content=native_json, fact_id="other-id", session_id="unrelated")
    store = module.V3AutogenStore.__new__(module.V3AutogenStore)
    store._engine = SimpleNamespace(
        recall=lambda *a, **kw: SimpleNamespace(
            results=[SimpleNamespace(fact=fact, score=0.9), SimpleNamespace(fact=other, score=0.8)]
        )
    )
    first, second = store.query_text("Ada")
    assert first["content"] == "Ada likes tea"
    assert all(first["metadata"][k] == v for k, v in metadata.items())
    assert first["metadata"]["_slm"]["fact_id"] == "retrieval-id"
    assert second["content"] == native_json


@pytest.mark.slow
def test_ux26_native_message_roundtrip(tmp_path):
    python = os.environ.get("WP7_FRAMEWORK_PYTHON")
    assert python, "set WP7_FRAMEWORK_PYTHON to the isolated native SDK interpreter"
    code = """
from agent_framework import Content, Message
from agent_framework_superlocalmemory.providers import _dict_to_message, _message_to_dict
for role in ('user', 'assistant', 'tool'):
    image = Content.from_uri('https://example.org/image.png', media_type='image/png')
    msg = Message(role=role, contents=['Tea', image],
                  message_id='m1', author_name='Ada', additional_properties={'source': 'contract'})
    got = _dict_to_message(_message_to_dict(msg))
    assert got.to_dict() == msg.to_dict(), (got.to_dict(), msg.to_dict())
legacy = _dict_to_message({'role': 'assistant', 'text': 'Legacy text'})
assert legacy.role == 'assistant' and legacy.text == 'Legacy text'
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "ide/integrations/agent-framework")}
    result = subprocess.run(
        [python, "-c", code], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_native_messages_still_feed_context(tmp_path):
    python = os.environ.get("WP7_FRAMEWORK_PYTHON")
    assert python
    code = """
import asyncio
from types import SimpleNamespace
from agent_framework import Message
from agent_framework_superlocalmemory.providers import (
    SuperLocalMemoryContextProvider, _message_to_dict)
provider = SuperLocalMemoryContextProvider.__new__(SuperLocalMemoryContextProvider)
provider.source_id = 'test'; provider._max_recall = 10
messages = [_message_to_dict(Message('user', ['Tea']))]
provider._store = SimpleNamespace(messages=lambda *a, **kw: messages)
seen = []
context = SimpleNamespace(extend_instructions=lambda *args: seen.append(args))
asyncio.run(provider.before_run(agent=None, session=None, context=context, state={}))
assert 'Tea' in seen[0][1], seen
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "ide/integrations/agent-framework")}
    result = subprocess.run(
        [python, "-c", code], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_native_autogen_and_history_persistence(tmp_path):
    import sys

    code = r"""
import asyncio
from agent_framework import Content, Message
from agent_framework_superlocalmemory import SuperLocalMemoryHistoryProvider
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_superlocalmemory import SuperLocalMemoryMemory

async def run():
    history = SuperLocalMemoryHistoryProvider()
    session = "native-session"
    sent = [Message(role, ['Tea', Content.from_uri('https://example.org/image.png',
                    media_type='image/png')], message_id=role)
            for role in ('user', 'assistant', 'tool')]
    await history.save_messages(session, sent)
    found = await history.get_messages(session)
    assert [m.to_dict() for m in found] == [m.to_dict() for m in sent]
    history.close()
    memory = SuperLocalMemoryMemory()
    sent = MemoryContent(content='Ada prefers jasmine tea.', mime_type=MemoryMimeType.TEXT,
                         metadata={'project': 'R2', 'nested': {'source': 'contract'}})
    await memory.add(sent)
    result = await memory.query('Ada prefers jasmine tea')
    own = [m for m in result.results if m.content == sent.content]
    assert own and all(own[0].metadata[k] == v for k, v in sent.metadata.items()), result
    await memory.close()
asyncio.run(run())
"""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [
                str(ROOT / "src"),
                str(ROOT / "ide/integrations/agent-framework"),
                str(ROOT / "ide/integrations/autogen"),
            ]
        ),
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_history_supported_minimum_sdk(tmp_path):
    import sys

    python = Path(os.environ["WP7_NATIVE_ENV_ROOT"]) / "framework-min/bin/python"
    code = """
import asyncio, importlib.util, os
from pathlib import Path
from agent_framework import Message
from agent_framework_superlocalmemory import providers
root = Path(os.environ['WP7_REPO'])
spec = importlib.util.spec_from_file_location('bridge', root / 'ide/integrations/_engine_bridge.py')
bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
store_file = (root / 'ide/integrations/agent-framework'
              / 'agent_framework_superlocalmemory/_v3_message_store.py')
providers._open_store = lambda path: bridge.RemoteStore(os.environ['WP7_ENGINE_PYTHON'], store_file,
    'V3MessageStore', Path(os.environ['SLM_DATA_DIR']) / 'memory.db')
async def run():
    history = providers.SuperLocalMemoryHistoryProvider()
    sent = [Message(role, contents=['Tea']) for role in ('user', 'assistant', 'tool')]
    await history.save_messages('s1', sent)
    got = await history.get_messages('s1')
    assert [m.to_dict() for m in got] == [m.to_dict() for m in sent]
    history.close()
asyncio.run(run())
"""
    env = {
        **os.environ,
        "WP7_REPO": str(ROOT),
        "WP7_ENGINE_PYTHON": sys.executable,
        "PYTHONPATH": str(ROOT / "ide/integrations/agent-framework"),
    }
    result = subprocess.run(
        [str(python), "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=90
    )
    assert result.returncode == 0, result.stderr


def test_ux27_preserves_reserved_key_collision():
    path = ROOT / 'ide/integrations/autogen/autogen_superlocalmemory/_v3_autogen_store.py'
    spec = importlib.util.spec_from_file_location('autogen_collision', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = {'_slm': {'caller': 'keep'}, '_slm_retrieval': 'also keep'}
    content = json.dumps({'adapter': 'autogen memory', 'content': 'Tea', 'metadata': metadata})
    fact = SimpleNamespace(content=content, fact_id='id1', session_id='autogen-mem:1')
    store = module.V3AutogenStore.__new__(module.V3AutogenStore)
    store._engine = SimpleNamespace(recall=lambda *a, **kw: SimpleNamespace(
        results=[SimpleNamespace(fact=fact, score=1.0)]))
    result = store.query_text('Tea')[0]
    assert all(result['metadata'][k] == v for k, v in metadata.items())
    assert {'fact_id': 'id1'} in result['metadata'].values()
