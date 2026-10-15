# SuperLocalMemory for Claude Code

SuperLocalMemory gives Claude Code a long-term memory that lives on your own
computer. Claude can save decisions, constraints and lessons as you work, and
find them again later by asking in plain words, in any project and any session.
Everything is stored in a local database in your home folder (`~/.superlocalmemory`).

## Install

```bash
npm install -g superlocalmemory
claude plugin marketplace add qualixar/superlocalmemory
claude plugin install superlocalmemory@qualixar
```

The plugin starts SuperLocalMemory's MCP server (`slm mcp`) and the local
service it talks to. It adds skills that tell Claude when to recall and what to
save, four advisor agents, the `/slm-loop` command and the hooks below.

## What the hooks do

Every hook runs on your computer and fails open: a hook problem never blocks Claude.

| When | What runs | What it touches |
|---|---|---|
| Session start | Session guidance; loads recent context from your local memory | Reads your local store |
| Session start | `ensure-venv.sh` prepares the plugin's own Python environment when the SuperLocalMemory command is not already installed | On first run only, downloads the SuperLocalMemory package and its libraries from PyPI into the plugin's data folder; skipped when `slm` is installed |
| After a tool runs | Records which files changed and whether recalled memories were used | Writes to your local store |
| When you send a prompt | Notices a repeated question or a change of topic, and reminds Claude to recall | Reads your prompt on your computer and passes it only to your local SuperLocalMemory |
| Before a web search | Reminds Claude to check your memory first | Reads your local store |
| Session end | Saves a session summary and closes open recall outcomes | Writes to your local store |

Nothing leaves your computer unless you turn on an online option yourself
(Mode C, the online answer check, or Web access), each documented separately.

## Learn more

- Website: https://superlocalmemory.com
- Documentation: https://github.com/qualixar/superlocalmemory/wiki
- Source and issues: https://github.com/qualixar/superlocalmemory

## License

This plugin and the SuperLocalMemory engine are licensed under AGPL-3.0-or-later (see `LICENSE`).
