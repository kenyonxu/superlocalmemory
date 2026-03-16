# FAQ

Frequently asked questions about SuperLocalMemory V3.

## General

### What is SuperLocalMemory?

SuperLocalMemory is a persistent memory system for AI assistants. It stores your decisions, bug fixes, project context, and preferences locally, then automatically provides them to your AI in future sessions. Your AI stops forgetting you.

### Is it really free?

Yes. SuperLocalMemory is open-source (MIT license) and completely free. No usage limits, no credit system, no subscription. Forever.

### Where is my data stored?

All data is stored locally in a SQLite database at `~/.superlocalmemory/memory.db`. In Mode A and Mode B, your data never leaves your machine. In Mode C, query data is sent to your configured cloud LLM provider.

### Which IDEs are supported?

17+ IDEs including Claude Code, Cursor, VS Code (with MCP extension), Windsurf, Gemini CLI, JetBrains IDEs (IntelliJ, PyCharm, WebStorm), Continue.dev, Zed, and any IDE that supports the Model Context Protocol.

### Does it work offline?

Mode A and Mode B work fully offline. Mode C requires internet for the cloud LLM.

## Installation

### What are the requirements?

- Node.js 18 or later
- npm (comes with Node.js)
- Any supported IDE
- For Mode B: Ollama with a pulled model
- For Mode C: API key for your cloud LLM provider

### How do I install it?

```bash
npm install -g superlocalmemory
slm setup
```

### How do I update?

```bash
npm install -g superlocalmemory@latest
```

### I am upgrading from V2. Will I lose my data?

No. Run `slm migrate` after updating. All memories, profiles, trust scores, and settings are preserved. See [Migration from V2](Migration-from-V2) for details.

## Usage

### How does auto-recall work?

When you start a conversation in your IDE, SuperLocalMemory automatically retrieves relevant memories and injects them into your AI's context. You do not need to call "recall" explicitly — it happens in the background.

### How does auto-capture work?

SuperLocalMemory monitors your IDE conversations and stores important information automatically — decisions, bug fixes, configurations, preferences. An entropy gate filters out low-information messages so only useful content is stored.

### Can I disable auto-capture?

Yes: `slm config set auto_capture false`

### What is the difference between `recall` and `search`?

`recall` uses all 4 retrieval channels and returns the most relevant memories for a specific query. `search` is broader and returns partial matches and related content. Use `recall` when you know what you are looking for, and `search` when you are exploring.

### How do I delete a memory?

```bash
slm forget --id <memory-id>     # Delete by ID
slm forget "search query"       # Delete matching memories
slm forget --all                # Delete everything (requires confirmation)
```

## Modes

### Which mode should I use?

- **Mode A** if you need privacy, compliance, or offline operation
- **Mode B** if you want composed answers and have a capable machine (16GB+ RAM)
- **Mode C** if you want maximum accuracy and cloud access is acceptable

### Can I switch modes after setup?

Yes: `slm mode a`, `slm mode b`, or `slm mode c`. Your memories are shared across all modes.

### What are the accuracy differences?

On the LoCoMo benchmark: Mode A achieves 62.3% (highest zero-LLM score), Mode C achieves approximately 78%. Higher modes add LLM synthesis and reranking.

## Privacy and Security

### Can anyone else see my memories?

No. Your database is a local file on your machine. It is not synced, uploaded, or shared with anyone — including us.

### Is it EU AI Act compliant?

Mode A and Mode B are compliant by architecture — data never leaves your device. Mode C requires additional consideration since data is sent to a cloud provider.

### Can I export my data?

Yes: `slm export > my-data.json`

### Can I delete all my data?

Yes: `slm forget --all` deletes all memories. `slm erasure --user <id>` performs a GDPR-compliant erasure.

## Troubleshooting

### My AI does not seem to remember anything.

1. Check that SuperLocalMemory is running: `slm status`
2. Check that you have stored memories: `slm list`
3. Verify your IDE connection: restart the IDE after configuring MCP
4. Check the active profile: `slm profile`

### Recall returns irrelevant results.

Try using more specific queries. If the issue persists, rebuild the index:

```bash
slm compact_memories
```

### The setup wizard does not detect my IDE.

Use manual configuration. See [IDE Setup](IDE-Setup) for per-IDE config paths.

### Where can I report bugs?

Open an issue at [github.com/qualixar/superlocalmemory/issues](https://github.com/qualixar/superlocalmemory/issues).

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
