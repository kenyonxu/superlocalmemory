# Getting Started

This page takes you from nothing to a working memory in about ten minutes: install, choose a mode, save one memory, find it again, connect your AI tool and open the dashboard. Each step says what you should see, so you can tell whether it worked. Everything here runs on your own computer. Detail behind any step is on the page it links to.

## 1. Install

The easiest way is to let your AI agent do it: paste the install prompt from [Installation](Installation#install) into Claude Code, Codex, Cursor or any agent that can run commands. On macOS and Linux a one-line installer needs no Python or Node. If you prefer to type the commands, the routes below need Python 3.12 to 3.14 and, for npm, Node.js 18 or later. Apple Silicon Macs, 64-bit Windows and 64-bit Linux are supported.

```bash
npm install -g superlocalmemory
```

If you prefer Python tools, `pipx install superlocalmemory` does the same job, or create a virtual environment and run `python -m pip install superlocalmemory`. Installing the package only puts the `slm` command on disk. It does not create your data folder, change any editor, start a background service or download a model. Platform notes, the other install routes and fixes for common errors are in [Installation](Installation).

## 2. Choose a mode

```bash
slm setup
```

The wizard asks first which mode to use. **Mode A** saves and recalls memories without a language model, and nothing leaves your device; it is the default and the right place to start. **Mode B** adds a model running on your own machine (Ollama, or any local server that speaks the OpenAI API). **Mode C** uses your own endpoint or a cloud provider and needs a key if it is a cloud service. You can change the mode later with `slm mode a`, `slm mode b` or `slm mode c`, followed by `slm restart`; [Modes Explained](Modes-Explained) says what each one changes. The wizard also lets you choose optional features and asks for consent before it connects any editor or installs a login service, and `slm setup --auto` takes every default without asking.

To download the embedding model ahead of time, run `slm warmup`. It is about 500 MB the first time, and it exits with an error if recall is not ready, so you know whether it worked.

## 3. Save a memory

```bash
slm remember "Our API uses JWT tokens with a 24-hour expiry. Refresh tokens last 30 days." --json
```

The reply is a receipt. It includes the id of the new memory, an `operation_id`, and a state of `queryable`, which means the memory can already be found by its wording while SLM finishes working out entities and meaning in the background. Add `--sync` if your next step needs that work to be finished first. Add `--kind rule` or `--kind decision` for something you want loaded at the start of later sessions ([Memory Kinds](Memory-Kinds)). How a save works, including what happens if the computer stops halfway, is in [How Remember Works](How-Remember-Works).

## 4. Find it again

```bash
slm recall "JWT token expiry"
```

You should see the memory listed with a number in brackets. That number says how well the memory matches the question; it is not the chance that the memory is the right answer. `slm trace "JWT token expiry"` shows the same search with the score each retrieval channel gave. If you turn on the [answer check](Answer-Check), recall also tells you whether the top result actually answers the question. How recall works end to end is in [How Recall Works](How-Recall-Works).

## 5. Check that everything is healthy

```bash
slm status     # mode, profile, data folder, database size
slm doctor     # dependencies, the embedding worker and the background service
```

`slm health` reports the state of the scoring and lifecycle layers. If something is wrong, [Troubleshooting](Troubleshooting) lists the symptoms and fixes.

## 6. Connect your AI tool

```bash
slm connect --list     # which editors were detected
slm connect cursor     # write the connection for one named tool
```

For tools that read an MCP configuration, the entry is a command that runs `slm mcp`. Claude Code and Codex can also install a plugin with skills, agents and hooks that load context at the start of a session and capture decisions as you go. Each tool's steps are in [IDE Setup](IDE-Setup), what the plugins contain is in [Plugins, Skills and Hooks](Plugins-Skills-and-Hooks), and what automatic capture and recall do is in [Auto-Memory](Auto-Memory).

## 7. Open the dashboard

```bash
slm dashboard
```

This opens the dashboard on your own computer, by default at `http://localhost:8765`. It shows what SLM knows, how it is health-checked, and the settings. [Dashboard Tour](Dashboard-Tour) walks through every screen.

## Commands you will use most

| Command | What it does |
|---|---|
| `slm remember "..."` | Save a memory |
| `slm recall "..."` | Search your memories |
| `slm trace "..."` | Search and show the score from each channel |
| `slm list` | Show recent memories with the ids that `update` and `delete` take |
| `slm forget "..."` | Delete memories that match a query, after asking you |
| `slm delete <id>` | Delete one memory by its exact id |
| `slm status` | Mode, profile, data folder and database size |
| `slm mode a` or `b` or `c` | Switch mode |
| `slm profile list`, `create NAME`, `switch NAME` | Keep separate memory spaces ([Profiles](Profiles)) |
| `slm connect` | Connect an AI tool |
| `slm dashboard` | Open the dashboard |

The full list, with every option, is in [CLI Reference](CLI-Reference).

## Where to go next

If you want to understand the design, start with [Architecture](Architecture). If you work in a team or need to show where an answer came from, read [Governance](Governance) and [Privacy and Data Flow](Privacy-and-Data-Flow). If you have screenshots, PDFs or notes folders to search, read [Pictures and Documents](Pictures-and-Documents). If you want an AI app on the web to use your memory, read [Web Access and Connected Apps](Web-Access-and-Connected-Apps).

Code: `cli/setup_wizard.py`, `cli/main.py`, `cli/commands.py`, `core/config.py` (`daemon_port`).
