# Host Integration Upgrades

An SLM package update upgrades SuperLocalMemory itself. It never silently
rewrites Codex, Claude Code, Cursor, VS Code, Antigravity, Hermes, or another
host's configuration.

After upgrading through npm, pip, or a repository checkout, inspect your local
integrations:

```bash
slm upgrade-hosts
```

The default command is a read-only preview. It does not create memory data,
start the daemon, edit host configuration, or turn on the experimental tool
gate.

To apply a reviewed change, name the host explicitly:

```bash
slm upgrade-hosts --host codex --apply
```

Or deliberately refresh all hosts that already contain an SLM integration:

```bash
slm upgrade-hosts --all-detected --apply
```

The command rejects an unbounded `--apply`. Existing portable MCP entries are
verified and preserved rather than replaced with a generic configuration. This
protects custom command paths, agent identities, and unrelated MCP servers.

Codex may refresh its SLM-owned skills, agents, and lifecycle hooks. Claude
Code uses its own marketplace plugin lifecycle:

```bash
claude plugin update superlocalmemory@qualixar
```

Restart the relevant application, then run:

```bash
slm doctor
```

For first-time setup, use `slm setup`; it detects integrations and asks before
connecting them. Read the full source documentation in
[Host Integration Upgrades](https://github.com/qualixar/superlocalmemory/blob/main/docs/host-upgrades.md).
