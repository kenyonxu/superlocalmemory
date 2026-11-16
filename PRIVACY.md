# SuperLocalMemory privacy policy

Effective 15 October 2026. This policy covers the SuperLocalMemory software: the `superlocalmemory` package on PyPI and npm, and the plugins, extensions and connectors built from this repository for Claude Code, Codex, Cursor, VS Code, Antigravity, Hermes, Grok, Muse, OpenClaw and other hosts. The qualixar.com website has its own policy at [qualixar.com/privacy](https://qualixar.com/privacy).

SuperLocalMemory is developed by Varun Pratap Bhardwaj (Qualixar). Contact: varun.pratap.bhardwaj@gmail.com.

## The short version

SuperLocalMemory runs on your computer and keeps your memory there. Qualixar does not operate a service that receives your memories, and the software does not report usage, crashes or other telemetry to Qualixar. The optional features listed below can send data to providers you choose, and only after you turn them on.

## What the software stores, and where

The software stores what you and your AI agents save to it, in a database on your own computer (by default under `~/.superlocalmemory`). This can include:
- memories, decisions and notes;
- documents and pictures you add;
- records of the tools an agent used and their outcomes;
- session summaries;
- settings.

What you save may contain personal data, such as names or email addresses in your notes. It stays on your computer.

The plugins' hooks record the agent's own activity locally: session start and end, tool use, and prompts used to look up relevant memories. Each plugin's README lists every hook and what it records.

You can export or delete your data at any time (`slm gdpr export`, `slm gdpr erase`, `slm forget`, or by deleting the data folder).

## What leaves your computer, and only when

Nothing leaves your computer unless you turn on a feature that sends it. Each feature says what it sends:

| What leaves | Only when |
|---|---|
| A question and the top three memories | You turn on Jev, the online answer check, and tick its consent |
| Memory text | You choose Mode C, a cloud embedding or reranking model, a model on another computer, or Jev kind typing (which asks for its own consent) |
| Encrypted backup files | You connect GitHub or Google Drive backup |
| Mesh messages | You configure mesh peers, or allow a web app to message your bots |
| Tool calls from web apps | You turn on Web access and add an app; the database stays on your computer |

When one of these is on, the data goes to the provider you chose (for example your model provider, GitHub, Google, or the web app you connected), under that provider's own terms. It does not go to Qualixar. Secrets in memory text are redacted on every outbound path.

## Downloads

The first time a local model is needed, the software downloads the model files from Hugging Face. Installing or updating the software downloads packages from PyPI or npm. These downloads send none of your memories.

## Children

SuperLocalMemory is not directed at children under 18.

## Changes

Changes to this policy are made in this file. The repository history shows every version.
