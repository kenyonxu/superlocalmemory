"""Default OCR must detect languages and continue after an empty backend."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.runtimes import media_image_ops as ops


def test_apple_vision_selects_accurate_automatic_language_detection():
    request = MagicMock()
    request.results.return_value = []
    handler = MagicMock()
    handler.performRequests_error_.return_value = (True, None)
    vision = SimpleNamespace(VNRecognizeTextRequest=MagicMock(),
                             VNImageRequestHandler=MagicMock(),
                             VNRequestTextRecognitionLevelAccurate=0)
    vision.VNRecognizeTextRequest.alloc.return_value.init.return_value = request
    vision.VNImageRequestHandler.alloc.return_value.initWithURL_options_.return_value = handler
    modules = {"Vision": vision, "Foundation": MagicMock()}
    with patch.object(ops.sys, "platform", "darwin"), \
            patch.object(ops.importlib, "import_module", side_effect=modules.__getitem__):
        assert ops._apple_vision("synthetic.png") == ""
    request.setAutomaticallyDetectsLanguage_.assert_called_once_with(True)
    request.setRecognitionLevel_.assert_called_once_with(0)


def test_automatic_ocr_tries_installed_fallback_after_empty_result():
    with patch.object(ops, "_apple_vision", return_value=""), \
            patch.object(ops, "_rapidocr", return_value="保存期限71天") as fallback:
        assert ops.ocr_image("synthetic.png") == {"engine": "rapidocr", "text": "保存期限71天"}
    fallback.assert_called_once()


def test_explicit_engine_and_empty_image_keep_backend_identity():
    with patch.object(ops, "_apple_vision", return_value=""), \
            patch.object(ops, "_rapidocr", return_value=None):
        assert ops.ocr_image("empty.png") == {"engine": "apple_vision", "text": ""}
        assert ops.ocr_image("empty.png", engine="apple_vision") == {
            "engine": "apple_vision", "text": ""}


@pytest.mark.native
@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision integration")
@pytest.mark.parametrize("text,font,expected", [
    ("ASTER4729 保存期限71天", "/System/Library/Fonts/STHeiti Medium.ttc", "保存期限"),
    ("مدة الاحتفاظ 71 يوم ASTER4729",
     "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "الاحتفاظ"),
])
def test_default_native_ocr_recovers_synthetic_non_latin_pixels(tmp_path, text, font, expected):
    pytest.importorskip("Vision")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1500, 200), "white")
    ImageDraw.Draw(image).text((35, 50), text, font=ImageFont.truetype(font, 60), fill="black")
    path = tmp_path / "synthetic.png"
    image.save(path)
    image.close()
    result = ops.ocr_image(str(path))
    assert result["engine"] == "apple_vision"
    assert expected in result["text"]
    assert "ASTER4729" in result["text"]
