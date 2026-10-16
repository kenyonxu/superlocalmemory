"""Model guidance is explicit; doctor never rewrites the installed config."""

from dataclasses import replace
from unittest.mock import Mock

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.model_catalog import best_local_llm
from superlocalmemory.storage.models import Mode


def test_installed_gemma4_is_recommended_over_llama():
    assert best_local_llm(32, ["llama3.2:latest", "gemma4:e2b"]) == "gemma4:e2b"


def test_installed_gemma3_is_recommended_over_llama():
    assert best_local_llm(16, ["llama3.2:latest", "gemma3:4b"]) == "gemma3:4b"


def test_doctor_nudges_without_switching(tmp_path):
    from superlocalmemory.cli.doctor_models import local_model_finding

    config = SLMConfig.for_mode(Mode.B, base_dir=tmp_path)
    config.llm = replace(config.llm, model="llama3.2")
    status, detail, fix = local_model_finding(config, ["llama3.2:latest", "gemma4:e2b"], 32)
    assert status == "WARN"
    assert "gemma4:e2b" in detail + fix
    assert config.llm.model == "llama3.2"
    config.save = Mock()
    local_model_finding(config, ["llama3.2:latest", "gemma4:e2b"], 32)
    config.save.assert_not_called()


def test_doctor_matches_exact_installed_tag(tmp_path):
    from superlocalmemory.cli.doctor_models import local_model_finding

    config = SLMConfig.for_mode(Mode.B, base_dir=tmp_path)
    config.llm = replace(config.llm, model="gemma3:12b")
    status, detail, _ = local_model_finding(config, ["gemma3:4b"], 32)
    assert status == "WARN"
    assert "not installed" in detail


def test_cloud_tag_is_not_an_installed_local_model():
    from superlocalmemory.core.model_catalog import DEFAULT_LOCAL_LLM

    assert best_local_llm(32, ["minimax-m3:cloud"]) == DEFAULT_LOCAL_LLM


def test_noninteractive_setup_keeps_an_explicit_installed_model(tmp_path, capsys):
    from superlocalmemory.cli.model_choice import pick_mode_b_model

    config = SLMConfig.for_mode(Mode.B, base_dir=tmp_path)
    config.llm = replace(config.llm, provider="ollama", model="llama3.2")
    pick_mode_b_model(
        config,
        interactive=False,
        prompt=lambda *_: "",
        installed=["llama3.2:latest", "gemma4:e2b"],
        ram_gb=32,
    )
    assert config.llm.model == "llama3.2"
    assert "gemma4:e2b" in capsys.readouterr().out
