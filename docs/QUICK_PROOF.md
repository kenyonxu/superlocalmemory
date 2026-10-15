# Store and recall one synthetic fact

Install using the [Getting Started guide](getting-started.md#install), choose operating mode
A in `slm setup`, and run `slm doctor` before using the CLI.

```bash
slm remember "Synthetic demo: the release checklist requires a human approval after tests pass." --tags demo --json --sync
slm recall "release checklist human approval" --json
```

The first command returns a durable `queryable` receipt. The second recalls that exact synthetic
sentence, with `tier: supported`. A ranking score is not an accuracy measurement, and this
one-fact check is not a retrieval benchmark.

To see the other two tiers, ask something memory cannot answer:

```bash
slm recall "zxqv blorptangle quux"          # That isn't in SuperLocalMemory.
```

To keep the check away from an existing memory service, give it a separate data folder and an
unused port: set `SLM_DATA_DIR` to a dedicated absolute path, `SLM_DAEMON_PORT` to a free port and
`SLM_DISABLE_LEGACY_PORT=1`. It writes no real data and changes no host hooks. Setup and
integrations remain explicit choices; review them before changing an existing workspace.
