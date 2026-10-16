"""OCR is optional during save; model resolution must stay entirely local."""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from superlocalmemory.runtimes import media_image_ops as ops


@pytest.fixture
def local_models(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    assets = {role: (f"{role}.onnx", hashlib.sha256(role.encode()).hexdigest())
              for role in ("Det", "Cls", "Rec")}
    for role, (filename, _) in assets.items():
        (models / filename).write_bytes(role.encode())
    network = MagicMock(side_effect=RuntimeError("offline download attempted"))
    def factory(*, params=None):
        if not params or any(f"{role}.model_path" not in params for role in assets):
            network()
        for role, (filename, _) in assets.items():
            assert params[f"{role}.model_path"] == str(models / filename)
        return lambda path: SimpleNamespace(txts=["保存期限71天"])
    module = SimpleNamespace(__file__=str(tmp_path / "__init__.py"),
                             RapidOCR=MagicMock(side_effect=factory))
    monkeypatch.setattr(ops, "_RAPIDOCR_MODELS", assets, raising=False)
    monkeypatch.setattr(ops.importlib, "import_module", lambda name: module)
    monkeypatch.setattr(ops, "_apple_vision", lambda path: "")
    return models, module, network


def test_partial_model_set_never_reaches_downloader(local_models, caplog):
    models, module, network = local_models
    (models / "Det.onnx").unlink()
    (models / "Cls.onnx").unlink()
    assert ops.ocr_image("blank.png") == {"engine": "apple_vision", "text": ""}
    network.assert_not_called()
    module.RapidOCR.assert_not_called()
    assert "Det" in caplog.text and "Cls" in caplog.text and "missing" in caplog.text


def test_corrupt_models_never_reach_downloader(local_models, caplog):
    models, module, network = local_models
    (models / "Rec.onnx").write_bytes(b"corrupt")
    assert ops.ocr_image("blank.png") == {"engine": "apple_vision", "text": ""}
    network.assert_not_called()
    module.RapidOCR.assert_not_called()
    assert "Rec" in caplog.text and "corrupt" in caplog.text


def test_complete_verified_models_get_explicit_local_paths(local_models):
    _, module, network = local_models
    assert ops.ocr_image("text.png") == {"engine": "rapidocr", "text": "保存期限71天"}
    network.assert_not_called()
    module.RapidOCR.assert_called_once()


@pytest.mark.parametrize("during", ["initialization", "inference"])
def test_unavailable_backend_keeps_first_empty_result(local_models, caplog, during):
    _, module, network = local_models
    if during == "initialization":
        module.RapidOCR.side_effect = RuntimeError("offline initialization failed")
    else:
        module.RapidOCR.side_effect = None
        module.RapidOCR.return_value = MagicMock(
            side_effect=RuntimeError("offline inference failed"))
    assert ops.ocr_image("blank.png") == {"engine": "apple_vision", "text": ""}
    assert ops.ocr_image("blank.png", engine="rapidocr") == {"engine": "none", "text": ""}
    network.assert_not_called()
    assert "unavailable" in caplog.text.lower()


def test_missing_package_is_reported_and_keeps_empty_native_result(monkeypatch, caplog):
    monkeypatch.setattr(ops, "_apple_vision", lambda path: "")
    def unavailable(name):
        raise ImportError("not installed")
    monkeypatch.setattr(ops.importlib, "import_module", unavailable)
    assert ops.ocr_image("blank.png") == {"engine": "apple_vision", "text": ""}
    assert "package" in caplog.text and "missing" in caplog.text
