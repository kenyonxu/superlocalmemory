# Auto-Memory

Auto-memory is how your AI assistant uses SLM without you typing `slm remember` or `slm recall`. At the start of a session SLM hands the assistant the memories that look relevant to the project, and while you work the assistant can pass along decisions, bug fixes and preferences for SLM to keep. Both halves are switchable, both run only on the surfaces you connected, and neither blocks the manual commands, which work whatever you set here.

## Auto-recall

When a session starts, the assistant calls `session_init` (a hook does it for hosts that run hooks; MCP clients without hooks make the call themselves). SLM recalls against the project, using a query built from the project folder unless the host gives one, and returns up to 10 memories. A memory is included only if its score reaches the relevance threshold, which is 0.3 by default, and it is no older than 30 days unless its score is 0.7 or higher. Pinned memories and standing rules for the project come along with the recalled ones. With the Claude Code or Codex hooks installed, a recall also runs before each prompt you send, so the assistant gets memories relevant to what you just asked, not only to the project. Short acknowledgements such as "ok" or "thanks" are skipped. The same context is available from a terminal as `slm session-context`, with `--max-age-days` to change the age window (0 turns the age filter off).

The first thing to know is that this is recall for loading context, not for answering a question, so the [answer check](Answer-Check) is deliberately skipped. Nothing is judged, so with the online option on no memory text goes off the machine for it and it costs nothing. If an answer check did run on the recall, its verdict would be placed above the memories as one plain line and never in place of them.

The second thing is trust. A recalled memory can contain an old prompt, imported text or something hostile. SLM puts recalled content inside one reference-only "untrusted evidence" block, with where each memory came from, size limits, recognised secrets removed and any forged boundary markers neutralised. If that renderer fails, SLM leaves the memory context out instead of falling back to raw text. The instruction files SLM writes for Cursor, Copilot and Antigravity contain only fixed product rules; memory itself is fetched at run time and never copied into a file the host trusts.

For a person this means the assistant can say "last time the auth bug was clock skew between services" without being asked, because the relevant memories were already in front of it when the session began.

## Auto-capture

During a session the assistant can call the `observe` tool, or `slm observe` from a terminal or hook, with a snippet of the conversation. SLM does not use a model for this. It matches the text against three small sets of patterns and keeps the snippet only if one set matches strongly enough.

| Category | What the patterns look for |
|---|---|
| Decision | Words such as decided, chose, picked, selected, went with, using, switched to, or a reason that leads to a choice |
| Bug fix | Words such as fixed, resolved, solved, root cause, bug, issue, error, or "the fix was" |
| Preference | Words such as prefer, always use, never use, convention, standard or style, with "is" or "should be" |

Content under 20 characters is never captured. One matching pattern scores 0.75 and two score 1.0, so with the default minimum confidence of 0.5 any single match is enough. Casual conversation, questions and anything that matches none of the sets are dropped, and the reply says why (`captured: false` with a reason). A captured snippet is saved as an ordinary memory marked as auto-observed and attributed to the agent that sent it, goes through the same journal and enrichment as `slm remember` ([How Remember Works](How-Remember-Works)), and shows up in recall and on the dashboard like any other.

Two limits are worth stating plainly. Pattern matching is crude: "using" and "issue" are common words, so it will capture some things you would not, and it will miss a decision phrased in words it does not know. And what the assistant chooses to send is up to the assistant; SLM does not read the whole conversation by itself. If something important was missed, save it yourself with `slm remember`, and remove a wrong capture with `slm forget` or `slm delete <fact_id>`.

## Turning it on and off

The settings live in the `rules` section of `config.json` in your data folder. The dashboard's Settings page has switches for the master auto-capture switch, capturing decisions and capturing bug fixes, and for the master auto-recall switch and recall on session start; the other keys are edited in `config.json`. These are the defaults.

| Setting | Default | Effect |
|---|---|---|
| `auto_recall.enabled` | on | Master switch for session context |
| `auto_recall.on_session_start` | on | Load context when a session starts |
| `auto_recall.on_every_prompt` | on | Recall before each prompt, through the prompt hooks (Claude Code's prompt hook and Codex's) |
| `auto_recall.relevance_threshold` | 0.3 | Lowest score a memory needs to be included |
| `auto_recall.max_memories_injected` | 10 | Most memories the auto-recall helper puts in a context block |
| `auto_capture.enabled` | on | Master switch for `observe` |
| `auto_capture.capture_session_summary` | on | Save a short summary when a session ends, through the stop hook |
| `auto_capture.capture_decisions`, `capture_bugs`, `capture_preferences` | on | One switch per category |
| `auto_capture.min_confidence` | 0.5 | Lowest confidence needed to store |

Each of the two master switches also turns off its hooks: with `auto_recall.enabled` off the per-prompt recall does nothing, and with `auto_capture.enabled` off no session summary is saved. Older releases wrote `on_every_prompt: false` with every Settings change although nothing read it; that stored value is dropped once on the first read after the upgrade, so per-prompt recall keeps running unless you set it to false again. With either master switch off, `slm remember` and `slm recall` still work. The hooks that run these steps in Claude Code and Codex are installed with `slm hooks install` (add `--agent codex` for Codex) and removed with `slm hooks remove`; `slm hooks status` shows the state. Nothing is installed without your say-so. Which hook does what is listed in [Plugins, Skills and Hooks](Plugins-Skills-and-Hooks).

## What stays on your computer

Auto-capture and auto-recall read and write the local data folder. The network is used only by features you turned on separately: Mode C sends the model request, including selected memory evidence, to the provider you configured; optional connectors, cloud backup and model downloads have their own rules. The full list is in [Privacy and Data Flow](Privacy-and-Data-Flow). To see what has been stored, run `slm list` or `slm recall "recent"`, or open the dashboard.

Related pages: [Active Memory](Active-Memory) for how SLM learns from feedback, [Memory Kinds](Memory-Kinds) for rules and decisions that are loaded at the start of every session, and [Modes Explained](Modes-Explained).

Code: `hooks/auto_capture.py`, `hooks/auto_recall.py`, `hooks/rules_engine.py`, `mcp/tools_active.py` (`observe`), `mcp/session_start.py`, `hooks/claude_code_hooks.py`, `cli/main.py` (`hooks`, `session-context`, `observe`).
