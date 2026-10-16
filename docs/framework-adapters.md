# Framework Adapters

Nine Python packages back their framework's native memory interface with the local SLM data root. Install each alongside the target framework; all data stays in the configured data root unless an optional SLM provider, connector, or backup feature is explicitly enabled.

**Prerequisites:** Python 3.12–3.14 and a supported SLM platform. CrewAI supports **3.12–3.13 only**. Google ADK, CrewAI and Semantic Kernel use a separate SLM interpreter because their dependencies conflict with SLM 4.1.25's validated runtime pins. The other six adapters embed SLM in their own environment.

For those three, install this checkout into two virtual environments and explicitly select the engine interpreter (example: Google ADK; substitute `crewai` or `semantic-kernel`):

```bash
uv venv .slm-engine --python 3.13
uv pip install --python .slm-engine/bin/python ./superlocalmemory
export SLM_ENGINE_PYTHON="$PWD/.slm-engine/bin/python"
uv venv .agent-runtime --python 3.13
uv pip install --python .agent-runtime/bin/python ./superlocalmemory/ide/integrations/google-adk
# Run your Python program with .agent-runtime/bin/python.
```

On Windows, use each virtual environment's `Scripts/python.exe` and set `SLM_ENGINE_PYTHON` in your shell. The adapter starts a local child process through JSON pipes, writes through the existing SLM engine to `SLM_DATA_DIR`, and closes it when the adapter closes. No daemon or TCP port is needed. Install the modified CrewAI adapter **from this checkout** until its next package release; the published adapter retains the old dependency constraints.

## Installing an adapter

Three adapters are published on PyPI: LangChain, LlamaIndex and CrewAI. Install them with the `pip install` command shown for each. The other six are not on PyPI yet, so a `pip install <name>` for them fails; install them from the repository folder instead:

```bash
git clone https://github.com/qualixar/superlocalmemory
pip install ./superlocalmemory/ide/integrations/langgraph
```

The folder for each adapter is in the table at the end of this page. The `pip install` line under each of those six headings below is the name the package will have once it is published, and the folder install gives the same package.

---

## LangGraph

**Install:** `pip install ./superlocalmemory/ide/integrations/langgraph` (from a clone of the repository)  
**Requires:** `langgraph >= 1.0.0`  
**Implements:** `BaseStore`  
**Class:** `SuperLocalMemoryStore`

```python
from langgraph_superlocalmemory import SuperLocalMemoryStore

store = SuperLocalMemoryStore()
store.put(("users", "1"), "profile", {"name": "Ada", "role": "engineer"})
item = store.get(("users", "1"), "profile")
results = list(store.search(("users",), filter={"role": "engineer"}))
store.delete(("users", "1"), "profile")
```

Drop in as the `store=` argument to `create_react_agent(...)` or `StateGraph(...).compile(store=store)`.

---

## Semantic Kernel

**Install:** `pip install ./superlocalmemory/ide/integrations/semantic-kernel` (from a clone of the repository)  
**Requires:** `semantic-kernel >= 1.34.0`, `SLM_ENGINE_PYTHON`
**Implements:** `VectorStore`; `get_collection` returns a `VectorStoreCollection`  
**Class:** `SuperLocalMemoryVectorStore`

```python
from dataclasses import dataclass
from typing import Annotated
from semantic_kernel.data.vector import VectorStoreField, vectorstoremodel
from semantic_kernel_superlocalmemory import SuperLocalMemoryVectorStore

@vectorstoremodel
@dataclass
class Doc:
    id: Annotated[str, VectorStoreField("key")]
    text: Annotated[str, VectorStoreField("data")]

store = SuperLocalMemoryVectorStore()
collection = store.get_collection(Doc, collection_name="docs")
await collection.ensure_collection_exists()
await collection.upsert(Doc(id="d1", text="hello world"))
doc = await collection.get("d1")
assert doc.text == "hello world"
collection.close()
```

Written against Semantic Kernel 1.44. Uses the post-1.34 `semantic_kernel.data.vector` API; the deprecated `MemoryStoreBase` is not used.

---

## Microsoft Agent Framework

**Install:** `pip install ./superlocalmemory/ide/integrations/agent-framework` (from a clone of the repository)  
**Requires:** `agent-framework-core >= 1.5.0`  
**Implements:** `ContextProvider` and `HistoryProvider`  
**Classes:** `SuperLocalMemoryContextProvider`, `SuperLocalMemoryHistoryProvider`

```python
from agent_framework_superlocalmemory import (
    SuperLocalMemoryContextProvider,
    SuperLocalMemoryHistoryProvider,
)

history = SuperLocalMemoryHistoryProvider()
memory = SuperLocalMemoryContextProvider(max_recall=10)
# agent = ChatAgent(..., context_providers=[memory], history_provider=history)
history.close()
memory.close()
```

`HistoryProvider` implements `get_messages` / `save_messages` per session.  
`ContextProvider` overrides `before_run` (inject context) and `after_run` (persist turn).

---

## LangChain

**Install:** `pip install langchain-superlocalmemory`  
**Requires:** `langchain-core >= 1.0.0`  
**Implements:** `BaseChatMessageHistory`  
**Class:** `SuperLocalMemoryChatMessageHistory`

```python
from langchain_core.messages import AIMessage, HumanMessage
from langchain_superlocalmemory import SuperLocalMemoryChatMessageHistory

history = SuperLocalMemoryChatMessageHistory(session_id="my-chat-session")
history.add_messages([
    HumanMessage(content="What is SuperLocalMemory?"),
    AIMessage(content="A local-first memory system for AI assistants."),
])
messages = history.messages   # chronological list
history.clear()
```

---

## LlamaIndex

**Install:** `pip install llama-index-storage-chat-store-superlocalmemory`  
**Requires:** SuperLocalMemory installed in the same virtual environment  
**Implements:** `BaseChatStore`  
**Class:** `SuperLocalMemoryChatStore`

```python
from llama_index.storage.chat_store.superlocalmemory import SuperLocalMemoryChatStore
from llama_index.core.memory import ChatMemoryBuffer
# https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/base/llms/types.py
from llama_index.core.llms import ChatMessage, MessageRole

chat_store = SuperLocalMemoryChatStore()
memory = ChatMemoryBuffer.from_defaults(
    chat_store=chat_store, chat_store_key="user-123", token_limit=3000)
# Or directly:
chat_store.add_message("session-1", ChatMessage(role=MessageRole.USER, content="Hi"))
```

---

## CrewAI

**Install:** `pip install ./superlocalmemory/ide/integrations/crewai` (inside the agent environment)
**Requires:** `crewai >= 1.14.6`, Python 3.12–3.13, `SLM_ENGINE_PYTHON`
**Implements:** `StorageBackend`  
**Class:** `SuperLocalMemoryBackend`

```python
from crewai_superlocalmemory import SuperLocalMemoryBackend

backend = SuperLocalMemoryBackend()
# Pass as storage_backend= in your CrewAI memory configuration.
backend.close()
```

Each `MemoryRecord` is stored as one SLM memory with `session_id = "crewai-rec:<id>"`. The backend supports scoped search and cosine-ranked retrieval over stored embeddings.

---

## AutoGen

**Install:** `pip install ./superlocalmemory/ide/integrations/autogen` (from a clone of the repository)  
**Requires:** `autogen-agentchat >= 0.7.5`  
**Implements:** `Memory` (autogen-core)  
**Class:** `SuperLocalMemoryMemory`

```python
from autogen_superlocalmemory import SuperLocalMemoryMemory
from autogen_core.memory import MemoryContent, MemoryMimeType

mem = SuperLocalMemoryMemory()
await mem.add(MemoryContent(content="Ada prefers dark mode.", mime_type=MemoryMimeType.TEXT))
result = await mem.query("Ada preferences")
# agent = AssistantAgent("assistant", memory=[mem], model_client=...)
assert result.results
await mem.close()
```

> For projects using **Microsoft Agent Framework** (`agent-framework-core`), prefer the `agent-framework-superlocalmemory` adapter instead.

---

## Google ADK

**Install:** `pip install ./superlocalmemory/ide/integrations/google-adk` (from a clone of the repository)  
**Requires:** `google-adk >= 2.5.0`, `SLM_ENGINE_PYTHON`
**Implements:** `BaseMemoryService`  
**Class:** `SuperLocalMemoryService`

```python
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google_adk_superlocalmemory import SuperLocalMemoryService

agent = Agent(name="memory_agent", model="gemini-2.5-flash")
service = SuperLocalMemoryService()
runner = Runner(agent=agent, app_name="my_app", memory_service=service,
                session_service=InMemorySessionService())
# After a run, explicitly call await service.add_session_to_memory(session).
service.close()
```

Implements `add_session_to_memory` (idempotent upsert per session) and `search_memory` (semantic recall). Custom database path: `SuperLocalMemoryService(db_path="/path/to/memory.db")`.

---

## OpenAI Agents

**Install:** `pip install ./superlocalmemory/ide/integrations/openai-agents` (from a clone of the repository)  
**Requires:** `openai-agents >= 0.18.3`  
**Implements:** `SessionABC`  
**Class:** `SLMSession`

```python
# https://openai.github.io/openai-agents-python/sessions/
from agents import Agent, Runner
from openai_agents_superlocalmemory import SLMSession

# Requires OPENAI_API_KEY for the selected hosted model.
agent = Agent(name="Geography", model="gpt-4o-mini")
session = SLMSession(session_id="user-42-conv-7")
try:
    result = await Runner.run(agent, "What is the capital of France?", session=session)
    assert result.final_output
finally:
    session.close()
```

API: https://openai.github.io/openai-agents-python/sessions/

Manages the ordered `TResponseInputItem` conversation history for one `session_id`. Custom path: `SLMSession(session_id="s1", db_path="/path/to/memory.db")`.

---

## All Adapters at a Glance

| Framework | Package | On PyPI | Folder in `ide/integrations/` | Interface | Class |
|---|---|:---:|---|---|---|
| LangGraph | `langgraph-superlocalmemory` | no | `langgraph` | `BaseStore` | `SuperLocalMemoryStore` |
| Semantic Kernel | `semantic-kernel-superlocalmemory` | no | `semantic-kernel` | `VectorStore` | `SuperLocalMemoryVectorStore` |
| Microsoft Agent Framework | `agent-framework-superlocalmemory` | no | `agent-framework` | `ContextProvider` / `HistoryProvider` | `SuperLocalMemoryContextProvider`, `SuperLocalMemoryHistoryProvider` |
| LangChain | `langchain-superlocalmemory` | yes | `langchain` | `BaseChatMessageHistory` | `SuperLocalMemoryChatMessageHistory` |
| LlamaIndex | `llama-index-storage-chat-store-superlocalmemory` | yes | `llamaindex` | `BaseChatStore` | `SuperLocalMemoryChatStore` |
| CrewAI | `crewai-superlocalmemory` | yes | `crewai` | `StorageBackend` | `SuperLocalMemoryBackend` |
| AutoGen | `autogen-superlocalmemory` | no | `autogen` | `Memory` | `SuperLocalMemoryMemory` |
| Google ADK | `google-adk-superlocalmemory` | no | `google-adk` | `BaseMemoryService` | `SuperLocalMemoryService` |
| OpenAI Agents | `openai-agents-superlocalmemory` | no | `openai-agents` | `SessionABC` | `SLMSession` |

Each adapter folder has its own README with extended usage examples, prerequisites, and scope notes.

---

*SuperLocalMemory — Copyright 2026 Varun Pratap Bhardwaj. AGPL-3.0-or-later. Part of Qualixar.*
