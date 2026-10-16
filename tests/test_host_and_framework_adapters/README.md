# Adapter and host contract tests

Run each module separately with the repository's frozen environment and isolation plugin. The optional native cases carry `slow` markers. HTTP cases use ephemeral loopback ports and a local embedding fixture; SDK cases use mock transports, not hosted model calls.

Prepare disposable framework environments under your temporary directory:

```bash
export WP7_NATIVE_ENV_ROOT="$TMPDIR/slm-native"
uv venv "$WP7_NATIVE_ENV_ROOT/google-adk" --python 3.13
uv pip install --python "$WP7_NATIVE_ENV_ROOT/google-adk/bin/python" ./ide/integrations/google-adk
uv venv "$WP7_NATIVE_ENV_ROOT/crewai" --python 3.13
uv pip install --python "$WP7_NATIVE_ENV_ROOT/crewai/bin/python" ./ide/integrations/crewai
uv venv "$WP7_NATIVE_ENV_ROOT/semantic-kernel" --python 3.13
uv pip install --python "$WP7_NATIVE_ENV_ROOT/semantic-kernel/bin/python" ./ide/integrations/semantic-kernel
```

The native test modules expect `WP7_NATIVE_ENV_ROOT` to contain `google-adk`, `crewai`, `semantic-kernel`, `crewai312`, `crewai314`, and `framework-min` virtual environments. `crewai314` needs pip for its deliberate refusal test. The minimum Agent Framework environment installs `agent-framework-core==1.5.0`. `WP7_FRAMEWORK_PYTHON` identifies a separate interpreter with the current Agent Framework SDK. `WP7_CONTINUE_NODE_MODULES` is the npm prefix containing `@continuedev/config-yaml@1.23.0` and `yaml`.

The SDK and six embedded-framework smoke cases also need the native SDKs available to the test interpreter: `openai`, `anthropic`, `openai-agents`, `llama-index-core`, `agent-framework-core`, `autogen-core`, `langgraph`, and `langchain-core`. Install optional test packages into a disposable test environment; keep SLM's core dependency pins unchanged.

Example module command (enable native cases explicitly):

```bash
uv run --frozen pytest tests/test_host_and_framework_adapters/test_sdk_contracts.py -m 'slow or not slow' -q -o tmp_path_retention_policy=none -p no:cacheprovider --basetemp="$TMPDIR/pt-sdk"
```

Adapter directories reuse the package name `tests`, so run their modules with `-c pyproject.toml --import-mode=importlib` to retain the root isolation plugin without module-name collisions.
