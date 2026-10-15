# Framework Adapters

SLM ships nine small Python packages that plug it into agent frameworks as their native memory or chat-history store. Use one when you are writing code against LangGraph, LangChain, CrewAI or another framework and want that framework's memory to land in SLM, where you can also recall it from the CLI, an MCP client or the dashboard. Only three of the nine are on PyPI today (LangChain, LlamaIndex and CrewAI), and those published copies were built against older dependency ranges than the repository folders. The other six install from a clone of the repository. If you only want an AI tool such as Cursor or Claude Code to use SLM, you do not need any of this; see [IDE Setup](IDE-Setup).

## What they are

Each adapter lives in its own folder under `ide/integrations/` in the repository, outside the main `src/` tree, with its own `pyproject.toml`, README and tests. Eight of the nine accept Python 3.12 to 3.14; CrewAI accepts 3.12 and 3.13 only. Six of them (LangChain, LlamaIndex, LangGraph, Microsoft Agent Framework, AutoGen and OpenAI Agents) declare `superlocalmemory>=3.6.23,<5` as a dependency and run SLM inside your own Python environment. CrewAI, Semantic Kernel and Google ADK do not declare SLM as a dependency, because their framework dependencies conflict with SLM's pinned runtime. They start SLM in a second, separate environment instead (see Installing). Platform support follows SLM itself: Apple Silicon macOS, 64-bit Windows and 64-bit Linux.

| Framework | Package | On PyPI | Folder | Implements | Main class | SLM runs |
|---|---|:---:|---|---|---|---|
| LangChain | `langchain-superlocalmemory` | yes | `langchain` | `BaseChatMessageHistory` | `SuperLocalMemoryChatMessageHistory` | same environment |
| LlamaIndex | `llama-index-storage-chat-store-superlocalmemory` | yes | `llamaindex` | `BaseChatStore` | `SuperLocalMemoryChatStore` | same environment |
| CrewAI | `crewai-superlocalmemory` | yes | `crewai` | `StorageBackend` | `SuperLocalMemoryBackend` | separate environment |
| LangGraph | `langgraph-superlocalmemory` | no | `langgraph` | `BaseStore` | `SuperLocalMemoryStore` | same environment |
| Semantic Kernel | `semantic-kernel-superlocalmemory` | no | `semantic-kernel` | `VectorStore` and `VectorStoreCollection` | `SuperLocalMemoryVectorStore` | separate environment |
| Microsoft Agent Framework | `agent-framework-superlocalmemory` | no | `agent-framework` | `ContextProvider`, `HistoryProvider` | `SuperLocalMemoryContextProvider`, `SuperLocalMemoryHistoryProvider` | same environment |
| AutoGen | `autogen-superlocalmemory` | no | `autogen` | `Memory` | `SuperLocalMemoryMemory` | same environment |
| Google ADK | `google-adk-superlocalmemory` | no | `google-adk` | `BaseMemoryService` | `SuperLocalMemoryService` | separate environment |
| OpenAI Agents | `openai-agents-superlocalmemory` | no | `openai-agents` | `SessionABC` | `SLMSession` | same environment |

The "On PyPI" column was checked against the PyPI JSON endpoint for each package name on 2026-10-12; the six marked "no" returned 404. It will change as packages are published, so check before relying on it. The three that exist were published with older dependency ranges than the repository folders: the published LangChain package, for example, asks for `langchain-core` below 1.0, while the folder asks for 1.0 or newer. The ranges below are the folders' ranges, so install from the folder to get what this page describes. Minimum framework versions from each `pyproject.toml`: `langgraph>=1.0.0`, `semantic-kernel>=1.34.0`, `agent-framework-core>=1.5.0`, `langchain-core>=1.0.0`, `llama-index-core>=0.13.0,<0.15`, `crewai>=1.14.6`, `autogen-agentchat>=0.7.5`, `google-adk>=2.5.0`, `openai-agents>=0.18.3`.

## Installing

The six adapters that run SLM in your own environment install next to SLM. Installing from the repository folder works for all six and gives the ranges listed above:

```bash
git clone https://github.com/qualixar/superlocalmemory
pip install ./superlocalmemory/ide/integrations/langgraph
```

LangChain and LlamaIndex can also be installed by package name (`pip install langchain-superlocalmemory`), subject to the older ranges noted above. For the other four of this group, a plain `pip install <name>` fails because the package is not published.

CrewAI, Semantic Kernel and Google ADK need two virtual environments, because their framework dependencies conflict with SLM's pinned runtime. SLM goes in its own environment, `SLM_ENGINE_PYTHON` names that environment's Python, and the adapter goes in the environment where your agent program runs. Example for Google ADK; substitute `crewai` or `semantic-kernel` for the other two:

```bash
uv venv .slm-engine --python 3.13
uv pip install --python .slm-engine/bin/python ./superlocalmemory
export SLM_ENGINE_PYTHON="$PWD/.slm-engine/bin/python"
uv venv .agent-runtime --python 3.13
uv pip install --python .agent-runtime/bin/python ./superlocalmemory/ide/integrations/google-adk
```

Then run your program with `.agent-runtime/bin/python`. On Windows use each environment's `Scripts/python.exe` and set the variable in your shell. The adapter starts the engine as a local child process and talks to it over JSON pipes, with no network port and no daemon, and stops it when you close the adapter. For CrewAI, the repository's own documentation says to install from the folder rather than from PyPI.

Two older single-file adapters, `integrations/langchain_memory.py` and `integrations/crewai_memory.py`, remain at the repository root for backward compatibility. Both carry a migration notice pointing to the partner packages above; use the packages for new work.

## How they store data

All nine follow the same pattern. The adapter opens its own SLM memory engine in Mode A (no language model) on the database `memory.db` in the SLM data folder, or on the path you pass as `db_path=`, with forgetting turned off and the cross-encoder reranker skipped. The data folder is `SLM_DATA_DIR` when set (the code also reads `SL_MEMORY_PATH` and `SLM_HOME`), otherwise `~/.superlocalmemory`. Six adapters run the engine inside your Python process; CrewAI, Semantic Kernel and Google ADK run it in the child process described above, with the same storage code. Content goes in through the engine's normal store call, so SLM's ingestion makes it searchable; for chat histories the original payload is kept as the exact record, which is what lets a history round-trip losslessly. Each adapter tags its records with a session identifier so one conversation, store key or agent never mixes with another. The AutoGen, CrewAI, Google ADK, LangGraph and OpenAI Agents adapters hand their blocking engine calls to a worker thread with `asyncio.to_thread`; the Semantic Kernel and Microsoft Agent Framework async methods call the store directly, without a worker thread.

Two consequences follow. A record written through an adapter shows up in `slm recall`, over MCP and on the dashboard like any other memory. And because the adapter does not go through the resident daemon, heavy concurrent writing from several processes to one database is something to test in your own setup; the daemon is otherwise the single writer ([Architecture](Architecture)). Optional SLM features such as a cloud provider, backup or connectors keep their own, separately documented network behaviour.

## Per-adapter notes

**LangChain.** Implements `BaseChatMessageHistory`. Records are tagged `langchain:session:<id>`, so each `session_id` is independent. All standard message types are supported and `additional_kwargs` round-trip.

```python
from langchain_core.messages import HumanMessage
from langchain_superlocalmemory import SuperLocalMemoryChatMessageHistory

history = SuperLocalMemoryChatMessageHistory(session_id="my-chat-session")
history.add_messages([HumanMessage(content="What is SLM?")])
print(history.messages)
history.clear()
```

**LlamaIndex.** Implements `BaseChatStore`, for use with `ChatMemoryBuffer`. The session identifier is `llamaindex:` plus a hash of the chat-store key. Import path: `llama_index.storage.chat_store.superlocalmemory`.

**CrewAI.** Implements `StorageBackend` for scope-aware memory records. CrewAI supplies the embeddings; the adapter stores them and ranks by cosine similarity in plain Python. Each `MemoryRecord` is one SLM memory with session `crewai-rec:<id>`. Scope matching is hierarchical (`/project` matches `/project/alpha`, never `/projectx`), and `metadata_filter` supports equality only.

**LangGraph.** Implements `BaseStore` for namespaced long-term memory, usable as `store=` in `create_react_agent` or `StateGraph(...).compile(store=store)`. Search is namespace-prefix plus `filter` matching and returns `score=None`; vector queries are not indexed here, so use SLM's recall for semantic ranking. TTL is not supported, and `"*"` wildcard namespace paths are treated literally.

**Semantic Kernel.** `SuperLocalMemoryVectorStore.get_collection(...)` returns a collection with upsert, get, delete and lifecycle calls. Search is field-filter retrieval with `score=None`; dense-vector ranking is listed as a follow-up. The Semantic Kernel vector-store API is still marked preview, so validate against your installed version.

**Microsoft Agent Framework.** A history provider (`get_messages` and `save_messages` per session) and a context provider whose `before_run` injects recent session memory as instructions and whose `after_run` persists the turn. `max_recall` (default 10) limits how much is injected. Wider semantic recall across the store is a documented follow-up. Do not confuse it with AutoGen; they are separate products.

**AutoGen.** Implements `autogen_core.memory.Memory`. Each `add()` writes a row with an `autogen-mem:` session, and `clear()` deletes only those rows, leaving your other SLM memories alone.

**Google ADK.** Implements `BaseMemoryService`. `add_session_to_memory` is an idempotent upsert per session, and `search_memory` uses SLM recall with post-filtering by app and user. The ranking score is not exposed in the response, and very short event text (under about 15 characters) may not produce searchable facts, though it is still stored.

**OpenAI Agents.** `SLMSession` implements `SessionABC` for ordered conversation history, with deterministic ordering by creation time then row id. Pass `db_path=` to use another database.

## Limits worth knowing

Semantic ranking is not built into the LangGraph and Semantic Kernel adapters; both filter by fields and namespaces. CrewAI is limited to equality metadata filters and in-process cosine ranking, which is fine for modest collections. Six of nine are not yet installable from PyPI, and three need a second Python environment. The Python version range is tight (3.12 to 3.14, and 3.12 to 3.13 for CrewAI) because it tracks SLM and the frameworks. Detailed behaviour and extra examples are in each adapter's README, and the table of contract status per host is in `ide/integration-contracts.json`.

The dashboard's Bounded Loops pane lists these same nine adapters beside "Any MCP-compatible agent", because loops and the adapters are both ways to put SLM under agent code ([Bounded Loops](Bounded-Loops), [Dashboard Tour](Dashboard-Tour)).

Code: `ide/integrations/<framework>/` (each with a `_v3_*` store module over the SLM engine), `ide/integration-contracts.json`, `docs/framework-adapters.md`.
