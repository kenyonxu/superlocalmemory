# Host Integration Upgrades

Updating SLM through npm, pip or a repository checkout updates the `slm` program and nothing else. It does not rewrite Claude Code, Codex, Cursor, VS Code, Antigravity, Hermes or any other tool's configuration, hooks or instructions. Those files are yours and may hold unrelated servers, policies or credentials. When you want the integrations refreshed, run `slm upgrade-hosts`: it previews by default, changes only SLM-owned pieces, and applies only to hosts you name. Installing and consenting are kept separate on purpose so an upgrade stays reversible and easy to inspect.

## Preview first

```bash
slm upgrade-hosts
```

With no flags this is read-only. It looks only at hosts that already contain an SLM entry, so it never discovers and touches an editor you have not connected. It does not create a data directory, start the daemon, edit any file or turn on the experimental tool gate. For each host it prints one line in the form `[status] host: detail`. The statuses are `preview` (a change that `--apply` would make), `updated` (a change made), `verified` (an existing SLM entry was checked and left alone), `plugin-managed` (Claude Code, see below), and `blocked` (it could not proceed, with the reason).

How "already integrated" is decided: for each target in the connect matrix it reads the tool's global MCP config and looks for a `superlocalmemory` entry (a Continue entry is matched by its server name); for Claude Code it checks whether SLM's hooks are installed; for Hermes it looks for `superlocalmemory` under `mcp_servers` in `~/.hermes/config.yaml`.

## Apply

```bash
slm upgrade-hosts --host codex --apply
slm upgrade-hosts --host cursor --host zed --apply     # --host is repeatable
slm upgrade-hosts --all-detected --apply               # every host that already has SLM
```

Two rules are enforced before any file is read. `--apply` with neither `--host` nor `--all-detected` is rejected with "--apply requires at least one --host or --all-detected", so a package update cannot sweep every supported application. And `--host` together with `--all-detected` is rejected, so you choose one meaning. A host name that is not in the managed list (the editors in [IDE Setup](IDE-Setup) plus `claude-code` and `hermes`) is refused as unsupported.

## What actually changes

The rule is merge, never clobber. For a portable editor that already has an SLM entry, the entry is verified and preserved, with the status `verified`, so a custom command path, an agent identity set in its environment, or unrelated MCP servers are never downgraded to a generic block.

Codex is the one host with refreshable assets. If its SLM entry exists, apply refreshes only SLM-owned skills, subagent files and lifecycle hooks, and the MCP block stays intact. The result line names what was written: how many skill files and subagent files, where they went, and which subagent files were preserved because SLM did not write them. If some skill paths resolve outside the location SLM writes, the line says those were not refreshed rather than reporting a no-op as a success.

Claude Code is managed by its own plugin lifecycle, so SLM reports `plugin-managed` and tells you to run:

```bash
claude plugin update superlocalmemory@qualixar
```

Hermes is reported `verified` if its existing mapping is present, and the mapping is never overwritten with an invented format; if Hermes is not connected the result is `blocked` and points you to the setup steps.

Naming a host that is not yet connected is a first-time connection, not an upgrade: `slm upgrade-hosts --host windsurf --apply` writes the MCP entry the same way `slm connect windsurf` does. That path is never reached by `--all-detected`.

The command ends with a line, "Apps to restart or reconnect", that names the hosts you just applied plus any host whose running MCP session reports an older version than the installed SLM, and points to the dashboard's Connected apps page for reconnecting live sessions. Restart or reconnect those applications, then run `slm doctor` to check SLM itself. If nothing is detected, the command says so and suggests `slm setup` for first-time setup.

## Your memory store after an upgrade

A separate safeguard covers data. An upgrade that changes the memory store first takes a verified copy; the restore points can be listed and used from the CLI (`slm db restore-points`, `slm db restore`), and there is no dashboard screen for them. A few minutes after SLM first starts on a new version it also checks the store once, read-only, and reports in the dashboard under Health > Memory store, with a Repair now button if something needs fixing ([Health and Repair](Health-and-Repair)).

## First-time setup instead

On a new machine use `slm setup`, the interactive wizard that detects installed tools and asks before connecting each one, or `slm connect <target>` for a single tool. The npm package creates a private Python runtime and the pip package installs into the active environment; neither performs host activation on its own.

Code: `src/superlocalmemory/cli/host_upgrades.py`, `hooks/portable_kit.py`, `hooks/codex_assets.py`, `hooks/codex_hooks.py`, `docs/host-upgrades.md`.
