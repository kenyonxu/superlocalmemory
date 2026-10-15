"""The setup wizard's existing phases, with explicit per-run state.

The public module supplies helpers at call time, so callers and tests that
replace its prompts, downloads or feature checks retain their behavior.
"""

from types import ModuleType, SimpleNamespace


class _SetupStopped(Exception):
    pass


def _system_check_1(state, support):
    state.interactive = support.is_interactive() and (not state.auto)
    state.slm_root = support._resolve_slm_home()
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  SuperLocalMemory V4 — The Unified Brain               ║")
    print("║  by Varun Pratap Bhardwaj / Qualixar                   ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    print("─── Step 1/10: System Check ───")
    print()
    state.py_ver = support.platform.python_version()
    state.py_ok = support.sys.version_info >= (3, 12)
    state.ram_gb = support._get_ram_gb()
    print(f"  Python:   {state.py_ver} {('✓' if state.py_ok else '✗ (3.12+ required)')}")
    print(f"  Platform: {support.platform.system()} {support.platform.machine()}")
    if state.ram_gb > 0:
        memory_note = '✓' if state.ram_gb >= 4 else '⚠ (4GB+ recommended)'
        print(f"  RAM:      {state.ram_gb:.1f} GB {memory_note}")
    print(f"  Data dir: {state.slm_root}")
    state.st_ok = False


def _system_check_2(state, support):
    try:
        import sentence_transformers

        state.sentence_transformers = sentence_transformers
        state.st_ok = True
        print("  sentence-transformers: ✓")
    except ImportError:
        print("  sentence-transformers: ✗ (not installed)")
        print("    Run: pip install 'sentence-transformers>=4.0.0'")
    if not state.py_ok:
        print("\n  ✗ Python 3.12+ is required. Please upgrade Python.")
        print("    https://python.org/downloads/")
        raise _SetupStopped()


def _mode_choice_1(state, support):
    state.ollama_present = support._ollama_available()
    state.default_mode = "b" if state.ollama_present else "a"
    print()
    print("─── Step 2/10: Choose Operating Mode ───")
    print()
    if state.ollama_present:
        print("  ✓ Ollama detected on this machine — Mode B recommended.")
        print("    Press Enter to accept, or pick another mode.")
        print()
    print("  [A] Local Guardian" + ("" if state.ollama_present else " (recommended)"))
    print("      No model-provider call in the core memory path.")
    print("      Review optional integrations and network policy for your deployment.")
    print()
    print(
        "  [B] Smart Local" + (" (recommended — Ollama detected)" if state.ollama_present else "")
    )
    print("      A model on this machine for enrichment — Ollama by default,")
    print("      any local OpenAI-compatible server works. Data stays on your machine.")
    print()
    print("  [C] Full Power")
    print("      Your own endpoint, or a cloud provider, for maximum accuracy.")
    print("      Requires an API key for a cloud provider; a keyless custom")
    print("      endpoint needs none.")
    print()


def _mode_choice_2(state, support):
    if state.interactive:
        state.choice = support._prompt(
            f"  Select mode [A/B/C] (default: {state.default_mode.upper()}): ", state.default_mode
        ).lower()
    else:
        state.choice = "a"
        print("  Auto-selecting Mode A (non-interactive, zero-config)")
    if state.choice not in ("a", "b", "c"):
        print(f"  Invalid choice '{state.choice}', using Mode A.")
        state.choice = "a"
    from superlocalmemory.core.config import SLMConfig

    state.SLMConfig = SLMConfig
    from superlocalmemory.storage.models import Mode

    state.Mode = Mode
    state.mode_map = {"a": state.Mode.A, "b": state.Mode.B, "c": state.Mode.C}
    state.config = support._build_wizard_config(state.mode_map[state.choice])


def _shared_memory_1(state, support):
    print()
    print("  Shared memory lets other local profiles' 'shared'/'global' memories")
    print("  appear in your recall. It is OFF by default — your memories stay private.")
    if state.interactive:
        state.sm_choice = support._prompt(
            "  See shared/global memories from other profiles by default? [y/N] (default: N): ", "n"
        ).lower()
    else:
        state.sm_choice = "n"
    if state.sm_choice in ("y", "yes"):
        from superlocalmemory.core.config import ScopeConfig

        state.ScopeConfig = ScopeConfig
        state.config.scope = state.ScopeConfig(
            default_scope="personal", recall_include_global=True, recall_include_shared=True
        )
        print("  ✓ Shared-memory recall ENABLED (your own writes still default to personal).")
    else:
        print("  ✓ Shared memory OFF (default) — enable later in mode_*.json if needed.")
    if state.choice == "b":
        support._run_mode_b_server_step(state.config, interactive=state.interactive)
    elif state.choice == "c" and state.interactive:
        support.configure_provider(state.config)
    else:
        state.config.save(mode_change=True)
    from superlocalmemory.core.modes import mode_short_name

    state.mode_short_name = mode_short_name
    print(f"\n  ✓ Mode {state.choice.upper()} ({state.mode_short_name(state.choice)}) configured")


def _code_graph_1(state, support):
    print()
    print("─── Step 3/10: Code Knowledge Graph ───")
    print()
    print("  CodeGraph builds a structural map of your codebase using Tree-sitter.")
    print("  It gives your AI assistant blast-radius analysis, call graphs,")
    print("  and connects code structure to your session memories.")
    print()
    print("  [Y] Enable CodeGraph (recommended for developers)")
    print("  [N] Disable CodeGraph (can enable later via config)")
    print()
    if state.interactive:
        state.cg_choice = support._prompt(
            "  Enable Code Knowledge Graph? [Y/n] (default: Y): ", "y"
        ).lower()
    else:
        state.cg_choice = "y"
        print("  Auto-enabling CodeGraph (non-interactive)")
    state.code_graph_enabled = state.cg_choice in ("", "y", "yes")
    state.slm_root.mkdir(parents=True, exist_ok=True)
    state.cg_config_path = state.slm_root / "code_graph_config.json"
    import json

    state.json = json
    state.cg_config_data = {
        "enabled": state.code_graph_enabled,
        "bridge_enabled": state.code_graph_enabled,
    }
    state.cg_config_path.write_text(
        state.json.dumps(state.cg_config_data, indent=2), encoding="utf-8"
    )


def _code_graph_2(state, support):
    if state.code_graph_enabled:
        print("\n  ✓ CodeGraph enabled")
        print("    Ask your agent to run the build_code_graph tool in a repo to index it")
    else:
        print(f"\n  ✓ CodeGraph disabled (enable later in {state.cg_config_path})")
    if state.st_ok and (not support._embedding_is_remote(state.config)):
        print()
        print("  Models to download (one-time, cached locally):")
        print("    • Embedding model   ~500 MB  — required for semantic recall")
        print("    • Reranker model    ~130 MB  — result quality")
        print("    • Compression model ~560 MB  — optional (you'll be asked)")
        if support._laya_summary_line_applies(interactive=state.interactive,
                                              no_laya=state.no_laya):
            print("    • Answer-check model ~1.1 GB — optional, Apple Silicon only")
            print("      (installed by default; --no-laya opts out)")


def _embedding_models_1(state, support):
    print()
    print("  ⚠ Only install models from sources you trust — a malicious model")
    print("    checkpoint can run code on your machine at load time. See SECURITY.md.")
    print("─── Step 4/10: Download Embedding Model ───")
    if support._embedding_is_remote(state.config):
        state.emb = state.config.embedding
        print("  ✓ Skipped — remote/OpenAI-compatible embedding endpoint configured")
        print(
            f"    provider={getattr(state.emb, 'provider', '?')}, "
            f"dimension={getattr(state.emb, 'dimension', '?')}"
        )
        print("    No local embedding model needed.")
    elif not state.st_ok:
        print("  ⚠ Skipped (sentence-transformers not installed)")
        print("    Models will download on first use.")
    else:
        state.embed_ok = support._download_model(support._EMBED_MODEL, "Embedding model")
        if not state.embed_ok:
            print("  ⚠ Model will download on first use (may take a few minutes)")
    print()
    print("─── Step 4b/10: Download Reranker Model ───")


def _embedding_models_2(state, support):
    if support._reranker_is_remote(state.config):
        state.rt = state.config.retrieval
        print("  ✓ Skipped — remote rerank endpoint configured")
        print(
            f"    backend={getattr(state.rt, 'cross_encoder_backend', '?')}, "
            f"model={getattr(state.rt, 'cross_encoder_model', '?')}"
        )
        print("    No local reranker model needed.")
    elif not state.st_ok:
        print("  ⚠ Skipped (sentence-transformers not installed)")
    else:
        support._download_reranker(support._RERANKER_MODEL)


def _compression_1(state, support):
    print()
    print("─── Step 4c/10: Compression Model (LLMLingua-2, ~560MB) ───")
    print("  Only used for aggressive prompt compression. It also downloads")
    print("  automatically the first time you enable that feature — so skipping")
    print("  here is safe.")
    if not state.st_ok:
        print("  ⚠ Skipped (sentence-transformers not installed)")
    elif state.interactive:
        state.comp_choice = support._prompt(
            "  Download the ~560MB compression model now? [y/N] (default: N): ", "n"
        ).lower()
        if state.comp_choice in ("y", "yes"):
            support._download_compressor(support._COMPRESSOR_MODEL)
        else:
            print("  ✓ Skipped — downloads automatically when you enable compression.")
    else:
        print("  ✓ Skipped (non-interactive) — downloads on first use.")
    support._run_laya_step(state.config, interactive=state.interactive, no_laya=state.no_laya)
    print()
    print("─── Step 5/10: Daemon Configuration ───")
    print()
    print("  The SLM daemon runs in the background for instant memory access.")
    print()
    print("  [1] 24/7 Always-On (recommended — brain never sleeps)")
    print("  [2] Auto-shutdown after idle (saves RAM when not coding)")
    print()


def _compression_2(state, support):
    if state.interactive:
        state.daemon_choice = support._prompt("  Select daemon mode [1/2] (default: 1): ", "1")
    else:
        state.daemon_choice = "1"
        print("  Auto-selecting 24/7 mode (non-interactive)")
    if state.daemon_choice == "2":
        if state.interactive:
            state.timeout_choice = support._prompt(
                "  Idle timeout [30m/1h/2h] (default: 30m): ", "30m"
            )
        else:
            state.timeout_choice = "30m"
        state.timeout_map = {"30m": 1800, "1h": 3600, "2h": 7200}
        state.config.daemon_idle_timeout = state.timeout_map.get(state.timeout_choice, 1800)
        print(f"\n  ✓ Auto-shutdown after {state.timeout_choice} idle")
    else:
        state.config.daemon_idle_timeout = 0
        print("\n  ✓ 24/7 Always-On mode")
    state.config.save(mode_change=True)
    print()


def _mesh_1(state, support):
    print("─── Step 6/10: Mesh Communication ───")
    print()
    print("  SLM Mesh enables agent-to-agent P2P communication.")
    print("  Multiple AI sessions can share knowledge in real-time.")
    print()
    print("  [Y] Enable Mesh (recommended)")
    print("  [N] Disable Mesh")
    print()
    if state.interactive:
        state.mesh_choice = support._prompt("  Enable Mesh? [Y/n] (default: Y): ", "y").lower()
    else:
        state.mesh_choice = "y"
        print("  Auto-enabling Mesh (non-interactive)")
    state.config.mesh_enabled = state.mesh_choice in ("", "y", "yes")
    state.config.save(mode_change=True)
    print(f"\n  ✓ Mesh {('enabled' if state.config.mesh_enabled else 'disabled')}")


def _ingestion_1(state, support):
    print()
    print("─── Step 7/10: Ingestion Adapters ───")
    print()
    print("  These let SLM learn from your email, calendar, and meetings.")
    print("  All adapters are OFF by default. You can enable them later.")
    print()
    print("  Available adapters:")
    print("    • Gmail Ingestion     — requires Google OAuth setup")
    print("    • Google Calendar     — shares Gmail credentials")
    print("    • Meeting Transcripts — watches a folder for .srt/.vtt files")
    print()
    if state.interactive:
        state.adapter_input = support._prompt(
            "  Enable any now? [Enter to skip, or type: gmail,calendar,transcript]: ", ""
        )
    else:
        state.adapter_input = ""
    state.adapters_config = {"gmail": False, "calendar": False, "transcript": False}
    if state.adapter_input:
        for state.name in state.adapter_input.split(","):
            state.name = state.name.strip().lower()
            if state.name in state.adapters_config:
                state.adapters_config[state.name] = True
    state.adapters_path = state.slm_root / "adapters.json"
    import json as _json

    state._json = _json
    state.adapters_path.write_text(
        state._json.dumps(
            {k: {"enabled": v, "tier": "auto"} for (k, v) in state.adapters_config.items()},
            indent=2,
        ),
        encoding="utf-8",
    )
    state.enabled_adapters = [k for (k, v) in state.adapters_config.items() if v]


def _ingestion_2(state, support):
    if state.enabled_adapters:
        print(f"\n  ✓ Enabled: {', '.join(state.enabled_adapters)}")
        print("    Run `slm adapters start <name>` to begin ingestion")
    else:
        print("\n  ✓ All adapters disabled (enable later: slm adapters enable gmail)")


def _entity_compilation_1(state, support):
    print()
    print("─── Step 8/10: Entity Compilation ───")
    print()
    print("  Entity compilation builds knowledge summaries per person,")
    print("  project, and concept. Runs automatically during consolidation.")
    print()
    print("  [Y] Enable entity compilation (recommended)")
    print("  [N] Disable")
    print()
    if state.interactive:
        state.ec_choice = support._prompt(
            "  Enable entity compilation? [Y/n] (default: Y): ", "y"
        ).lower()
    else:
        state.ec_choice = "y"
        print("  Auto-enabling entity compilation (non-interactive)")
    state.config.entity_compilation_enabled = state.ec_choice in ("", "y", "yes")
    state.config.save(mode_change=True)
    enabled = 'enabled' if state.config.entity_compilation_enabled else 'disabled'
    print(f"\n  ✓ Entity compilation {enabled}")


def _skill_evolution_1(state, support):
    print()
    print("─── Step 9/10: Skill Evolution ───")
    print()
    print("  SLM can automatically evolve skills that underperform.")
    print("  It detects degradation, generates improvements, and verifies them blindly.")
    print("  Requires an LLM backend (Claude CLI, Ollama, or API key).")
    print()
    print("  OFF by default — when enabled it makes background LLM calls.")
    print("  [Y] Enable Skill Evolution")
    print("  [N] Keep disabled (enable later: slm config set evolution.enabled true)")
    print()
    if state.interactive:
        state.evo_choice = support._prompt(
            "  Enable Skill Evolution? [y/N] (default: N): ", "n"
        ).lower()
    else:
        state.evo_choice = "n"
        print("  Skill Evolution stays OFF (non-interactive) — enable later with:")
        print("    slm config set evolution.enabled true")
    state.evolution_enabled = state.evo_choice in ("y", "yes")
    state.slm_root.mkdir(parents=True, exist_ok=True)
    state.evo_config_path = state.slm_root / "config.json"
    state.evo_cfg: dict = {}
    if state.evo_config_path.exists():
        try:
            state.evo_cfg = state.json.loads(state.evo_config_path.read_text(encoding="utf-8"))
        except (state.json.JSONDecodeError, OSError):
            pass


def _skill_evolution_2(state, support):
    state.evo_cfg["evolution"] = {"enabled": state.evolution_enabled, "backend": "auto"}
    state.evo_config_path.write_text(
        state.json.dumps(state.evo_cfg, indent=2) + "\n", encoding="utf-8"
    )
    if state.evolution_enabled:
        print("\n  ✓ Skill Evolution enabled (backend: auto-detect)")
    else:
        print("\n  ✓ Skill Evolution disabled")


def _verification_1(state, support):
    print()
    print("─── Step 10/10: Verification ───")
    if state.st_ok:
        state.verified = support._verify_installation()
    else:
        print("  ⚠ Skipped (sentence-transformers not installed)")
        state.verified = False
    try:
        from superlocalmemory.cli.wizard_v3426_options import (
            persist_v3426_options,
            prompt_v3426_options,
            validate_install_data_dir,
        )

        state.persist_v3426_options = persist_v3426_options
        state.prompt_v3426_options = prompt_v3426_options
        state.validate_install_data_dir = validate_install_data_dir
        (state.ok, state.reason) = state.validate_install_data_dir(state.slm_root)
        if not state.ok:
            print()
            print("  ⚠ Data directory check failed:")
            for line in state.reason.splitlines():
                print(f"    {line}")
            print()
        state.v3426_opts = state.prompt_v3426_options(interactive=state.interactive)
        state.persist_v3426_options(state.v3426_opts, state.slm_root)
    except Exception as exc:
        print(f"  (v3.4.26 options step skipped: {exc})")


def _integrations_1(state, support):
    support._configure_external_integrations(interactive=state.interactive)
    support._configure_autostart(interactive=state.interactive)
    support._mark_complete()


def _completion_1(state, support):
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    if state.verified:
        print("║  ✓ Setup Complete — The Unified Brain is ready!        ║")
    else:
        print("║  ✓ Setup Complete — basic config saved                 ║")
        print("║    Models will auto-download on first use              ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    state.daemon_mode = (
        "24/7"
        if state.config.daemon_idle_timeout == 0
        else f"auto-shutdown ({state.config.daemon_idle_timeout}s)"
    )
    print(f"  Enabled: Mode {state.choice.upper()}, Daemon ({state.daemon_mode})", end="")
    if state.config.mesh_enabled:
        print(", Mesh", end="")
    if state.config.entity_compilation_enabled:
        print(", Entity Compilation", end="")
    if state.code_graph_enabled:
        print(", CodeGraph", end="")
    if state.evolution_enabled:
        print(", Skill Evolution", end="")
    print()
    if state.enabled_adapters:
        print(f"  Adapters: {', '.join(state.enabled_adapters)}")
    else:
        print("  Adapters: none (enable via: slm adapters enable gmail)")
    print()


def _completion_2(state, support):
    try:
        from superlocalmemory.core import component_registry as _cr

        state._cr = _cr
        state._sum = state._cr.snapshot(state.config)["summary"]
        if state._sum.get("healthy"):
            print("  ✓ All components ready — models and dependencies present.")
        else:
            print(f"  ⚠ {state._sum.get('missing', 0)} component(s) need attention.")
            print("    SLM auto-repairs fixable ones on daemon start. To check/fix now:")
            print("      slm doctor          (auto-fix: slm doctor --fix)")
    except Exception:
        pass
    print()
    print("  Quick start:")
    print('    slm remember "your first memory"')
    print('    slm recall "search query"')
    from superlocalmemory.cli.deployment import runtime_port

    state.runtime_port = runtime_port
    print(f"    slm dashboard              → http://localhost:{state.runtime_port()}")
    print("    slm adapters enable gmail  → start Gmail ingestion")
    print()
    print("  Need help?")
    print("    slm help       — every command, grouped (try: slm help self-heal)")
    print("    slm doctor     — diagnose issues (auto-fix: slm doctor --fix)")
    print("    slm serve install — install auto-start service")
    print("    https://github.com/qualixar/superlocalmemory")


def _completion_3(state, support):
    print()


def run(auto: bool, support: ModuleType, *, no_laya: bool = False) -> None:
    state = SimpleNamespace(auto=auto, no_laya=no_laya)
    try:
        _system_check_1(state, support)
        _system_check_2(state, support)
        _mode_choice_1(state, support)
        _mode_choice_2(state, support)
        _shared_memory_1(state, support)
        _code_graph_1(state, support)
        _code_graph_2(state, support)
        _embedding_models_1(state, support)
        _embedding_models_2(state, support)
        _compression_1(state, support)
        _compression_2(state, support)
        _mesh_1(state, support)
        _ingestion_1(state, support)
        _ingestion_2(state, support)
        _entity_compilation_1(state, support)
        _skill_evolution_1(state, support)
        _skill_evolution_2(state, support)
        _verification_1(state, support)
        _integrations_1(state, support)
        _completion_1(state, support)
        _completion_2(state, support)
        _completion_3(state, support)
    except _SetupStopped:
        return
