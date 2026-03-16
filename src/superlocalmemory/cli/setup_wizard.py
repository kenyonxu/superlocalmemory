# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Interactive setup wizard for first-time configuration.

Guides new users through mode selection and provider setup.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import os
import shutil


def run_wizard() -> None:
    """Run the interactive setup wizard."""
    print()
    print("SuperLocalMemory V3 — First Time Setup")
    print("=" * 40)
    print()
    print("Choose your operating mode:")
    print()
    print("  [A] Local Guardian (default)")
    print("      Zero cloud. Zero LLM. Your data never leaves your machine.")
    print("      EU AI Act compliant. Works immediately.")
    print()
    print("  [B] Smart Local")
    print("      Local LLM via Ollama for answer synthesis.")
    print("      Still private — nothing leaves your machine.")
    print()
    print("  [C] Full Power")
    print("      Cloud LLM for best accuracy (~78% on LoCoMo).")
    print("      Requires: API key from a supported provider.")
    print()

    choice = input("Select mode [A/B/C] (default: A): ").strip().lower() or "a"

    if choice not in ("a", "b", "c"):
        print(f"Invalid choice: {choice}. Using Mode A.")
        choice = "a"

    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import Mode

    if choice == "a":
        config = SLMConfig.for_mode(Mode.A)
        config.save()
        print()
        print("Mode A configured. Zero cloud, zero LLM.")
        print(f"Config saved to: {config.base_dir / 'config.json'}")

    elif choice == "b":
        config = SLMConfig.for_mode(Mode.B)
        print()
        print("Checking for Ollama...")
        if shutil.which("ollama"):
            print("  Ollama found!")
        else:
            print("  Ollama not found. Install it from https://ollama.ai")
            print("  After installing, run: ollama pull llama3.2")
        config.save()
        print(f"Config saved to: {config.base_dir / 'config.json'}")

    elif choice == "c":
        config = SLMConfig.for_mode(Mode.C)
        configure_provider(config)

    print()
    print("Ready! Your AI now remembers you.")
    print()


def configure_provider(config: object) -> None:
    """Configure LLM provider for Mode C.

    Args:
        config: An SLMConfig instance (typed as object to avoid circular import
                at module level; actual type checked at runtime).
    """
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import Mode

    presets = SLMConfig.provider_presets()

    print()
    print("Choose your LLM provider:")
    print()
    providers = list(presets.keys())
    for i, name in enumerate(providers, 1):
        preset = presets[name]
        print(f"  [{i}] {name.capitalize()} — {preset['model']}")
    print()

    idx = input(f"Select provider [1-{len(providers)}]: ").strip()
    try:
        provider_name = providers[int(idx) - 1]
    except (ValueError, IndexError):
        print("Invalid choice. Using OpenAI.")
        provider_name = "openai"

    preset = presets[provider_name]

    # Resolve API key from environment or prompt
    env_key = preset.get("env_key", "")
    api_key = ""
    if env_key:
        existing = os.environ.get(env_key, "")
        if existing:
            print(f"  Found {env_key} in environment.")
            api_key = existing
        else:
            api_key = input(
                f"  Enter your {provider_name.capitalize()} API key: ",
            ).strip()

    updated = SLMConfig.for_mode(
        Mode.C,
        llm_provider=provider_name,
        llm_model=preset["model"],
        llm_api_key=api_key,
        llm_api_base=preset["base_url"],
    )
    updated.save()
    print(f"  Provider: {provider_name}")
    print(f"  Model: {preset['model']}")
    print(f"Config saved to: {updated.base_dir / 'config.json'}")
