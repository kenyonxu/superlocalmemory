# Modes Explained

SLM runs in one of three modes, and the mode answers one question: which language model, if any, may touch your memories. In Mode A, memories are saved and recalled without a language model. Mode B uses a model on your own machine. Mode C uses an endpoint you choose, which can be a cloud provider. In every mode the memory database stays on your computer, and you can change modes at any time without losing anything.

## What each mode runs

The definitions live in one place, `core/modes.py`, so the dashboard, the CLI and the MCP tools all describe the modes with the same words. The one-line descriptions the server sends, and the dashboard shows, are these:

- **Mode A:** "Memories are saved and recalled without a language model — nothing leaves this device. Fastest and most private."
- **Mode B:** "A model on this machine — Ollama by default, any local server works — and nothing leaves this device."
- **Mode C:** "Your own endpoint, or a cloud provider, for the best recall quality. Your queries leave this device."

When the optional online answer check is switched on, the MCP description of Mode A and Mode B says "nothing else leaves this device" instead, because the check sends the question and top memories to its provider. The wording about Mode A is deliberately about saving and recalling: the on-device answer check ([Answer Check](Answer-Check)) is a small separate model that runs after recall, on your own computer, and it does not change what Mode A means for your memories.

| | Mode A: Local Guardian | Mode B: Smart Local | Mode C: Full Power |
|---|---|---|---|
| Language model | None. Extraction uses deterministic rules. | A model on this machine: Ollama by default, any OpenAI-compatible local server works. | Your own endpoint or a cloud provider. |
| What the model does | Nothing. | Fact extraction, entity resolution, type classification, importance scoring, answer generation. | The same as B, plus agentic multi-round retrieval and an optional cloud reranker. |
| Embeddings (preset) | Local `nomic-ai/nomic-embed-text-v1.5`, 768 dimensions. | Ollama `nomic-embed-text`, 768 dimensions. | Local nomic, 768 dimensions. Cloud embeddings only if you configure an endpoint (the catalogue default is `text-embedding-3-small`, 1536 dimensions). |
| Reranker | Local cross-encoder `cross-encoder/ms-marco-MiniLM-L-12-v2`. | Same. | Same, with `semantic_top_k` 80 and 2 agentic rounds. |
| LLM default | None. | `ollama` at `http://localhost:11434`, model `gemma3:4b`. | `openrouter` with `openai/gpt-6-luna`. |

The presets are built in `SLMConfig._mode_template` (`core/config.py`). A fresh install writes all three mode files from these presets. After that, your own edits to a mode are kept. Mode C does not move you to a paid embedder on its own: a cloud embedding model is something you opt into, because a half-configured cloud embedder used to make ingestion fail.

## What leaves the machine

This is the table people usually want, so it is deliberately blunt.

| | Mode A | Mode B | Mode C |
|---|---|---|---|
| Memory text sent to a language model | Never. | Only to the model server you configured. If that is Ollama on another host, it goes there. | Yes. Extraction and consolidation text, and recall queries and context, go to the configured provider. |
| `data_stays_local` in the code | True | True | False |

In Mode C the text that goes to the provider is the memory text in extraction prompts at save time, the query and top candidates during agentic recall refinement, and answer-generation prompts. Credentials in it are replaced first, but personal data is not.

Any mode can still send data off the machine when you turn on an optional feature: a custom embedding endpoint, a remote reranker, the online [Answer Check](Answer-Check), [Web Access and Connected Apps](Web-Access-and-Connected-Apps), or encrypted [Cloud Backup](Cloud-Backup). SLM treats Mode A as "no language model in the save and recall path", not as "no network feature can ever be enabled". The code leaves the EU AI Act status of all three modes as `None` on purpose, because that needs a deployment assessment. See [Privacy and Data Flow](Privacy-and-Data-Flow) for the full picture.

## Switching modes

There are four ways to switch, and they all end in the same function, `SLMConfig.switch_mode`.

```bash
slm mode          # show the current mode
slm mode b        # switch
slm restart       # apply it
```

You can also use the Settings pane, group "Operating Mode & LLM Provider", or the MCP tool `set_mode` (with `get_mode` to read it). To choose the model for B or C, use `slm provider set <openai|anthropic|azure|ollama|openrouter|custom>`. For your own OpenAI-compatible server, `slm provider set custom --endpoint URL [--key KEY] [--model NAME] --mode b|c`; the default target mode is C, and a keyless local server is fine.

A switch changes the `mode` field and nothing else. It saves your current settings to the old mode's file, loads the target mode's file if one exists, and otherwise copies your current settings and changes only the mode. LLM defaults are filled in only when you have no provider at all. Your embedding, retrieval, forgetting and math settings are not reset, and if a mode file names a different embedding model, `set_mode` answers `needs_reindex` and the daemon re-indexes in the background rather than blocking.

The reason for this design is an older bug where switching modes quietly replaced a tuned embedding endpoint with the mode's defaults. Switching now preserves what you set.

For an endpoint that is not on `localhost`, plain HTTP is accepted only for a numeric private-LAN address while `retrieval.trust_plain_http_lan` is true (the default). Public hosts and bare hostnames need HTTPS. Set the flag to false in a hardened network to require HTTPS everywhere off the machine.

## Where a mode is stored

| File in the data folder | What it holds |
|---|---|
| `config.json` | The active configuration, including `mode`. |
| `mode_a.json`, `mode_b.json`, `mode_c.json` | A saved copy of the settings for each mode. |
| `current_mode` | One letter, the single source of truth for the active mode. |
| `answer_check.json`, `memory_kinds.json` | Deliberately outside the per-mode copies. |

Answer Check and memory-kind settings live in their own files because the per-mode copies are restored on every switch. Consent given in one mode would otherwise come back after you switched away and back. If `config.json` disagrees with `current_mode`, SLM loads the mode file that `current_mode` names.

## What the default mode is

A fresh install starts in Mode A. `SLMConfig.load()` returns Mode A when there is no `config.json`, first-boot migration writes `a` into `current_mode`, and `slm init --auto` selects Mode A.

There is one edge case worth knowing. `SLMConfig.read_current_mode()` returns `"b"` when the `current_mode` file is missing or empty. Normally the file exists, so you will not see this, but if you delete it by hand while `config.json` still exists you can end up with two different answers to "what is the default". Recreate the file with `slm mode a` (or the letter you want) and the question goes away.

## Embedding choices

The Settings pane offers two embedding providers: the default local model, or a custom OpenAI-compatible endpoint where you give a model name, dimension, endpoint and an optional key. From a terminal, `slm embedder switch MODEL [--dimension N] [--provider sentence-transformers|ollama|openai|slm-media] [--endpoint URL]` does the same, and keys are never taken on the command line.

`slm models` lists the catalogue (version `2026-10-08` in `core/model_catalog.py`): local and hosted language models and the embedders above, with test results for how often a model changed a fact during SLM's extraction test. The catalogue is advice. Any installed Ollama model, OpenRouter id or custom endpoint can be typed in.

### Changing the embedding model re-indexes in the background

Vectors from different models cannot be compared, so a model change has to re-embed every memory. SLM does this inside the daemon without stopping: recall and remember keep using the old model and old vectors until the new space is complete, then both change in one transaction. The previous space is kept so one `slm embedder rollback` can return to it, and a daemon restart resumes the job where it stopped. A save that changes the vector width without going through this job is refused with HTTP 409 and the message points you to `slm embedder switch MODEL --dimension N`. The tuning variables are `SLM_REINDEX_BATCH` (32), `SLM_REINDEX_PAUSE_S` (0.02) and `SLM_REINDEX_DUTY_REST` (1.0). The full walkthrough, including why text stays on its own model, is on [Embedding Models](Embedding-Models).

## Language

There is no language or locale setting in SLM. Nothing in Settings, the setup wizard or `SLMConfig` selects a language, and the dashboard is English only (`<html lang="en">`). No catalogue entry is labelled multilingual.

What you can do for non-English text is replace two components. The bundled reranker, `cross-encoder/ms-marco-MiniLM-L-12-v2`, is English-only, and the code says so (`core/config.py`, `retrieval/remote_reranker.py`). Point SLM at a multilingual reranker such as `BAAI/bge-reranker-v2-m3` or a Qwen reranker served by an OpenAI-compatible `/v1/rerank` endpoint, by setting `retrieval.cross_encoder_backend` to `"openai"` together with `cross_encoder_endpoint` and `cross_encoder_model` in `config.json`. This is not exposed in Settings. For embeddings, use a custom endpoint (for example Qwen3-Embedding) through Settings or `slm embedder switch`. Details and a worked JSON example are in [Configuration](Configuration).

SLM makes no claim about how well the default embedding model handles non-English text, because nothing in the code states it. Test it on your own material before relying on it.

Code: `core/modes.py`, `core/config.py` (`SLMConfig`, `_mode_template`, `switch_mode`, `read_current_mode`), `core/model_catalog.py`, `core/embedding_reindex.py`, `cli/commands.py` (`cmd_mode`), `cli/embedder_cmd.py`, `mcp/tools_v3.py` (`set_mode`, `get_mode`), `retrieval/remote_reranker_config.py`, `core/provider_endpoint_trust.py`.
