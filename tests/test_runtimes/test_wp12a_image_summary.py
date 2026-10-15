"""The image worker runs without the product package installed."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from superlocalmemory.runtimes import media_image_ops


def test_image_prepare_caption_in_standalone_worker(tmp_path):
    module = Path(__file__).parents[2] / "src/superlocalmemory/runtimes/media_image_ops.py"
    script = tmp_path / "standalone.py"
    script.write_text('''import sys, importlib.util, types
from pathlib import Path
sys.modules["superlocalmemory"] = None
sys.modules["imagehash"] = types.SimpleNamespace(phash=lambda _: "test")
spec = importlib.util.spec_from_file_location("ops", sys.argv[1])
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
root = Path(sys.argv[2])
class Image:
    width, height, format = 64, 32, "PNG"
    def convert(self, mode): return Image()
    def thumbnail(self, size): pass
    def getdata(self): return [(0, 0, 255)] * 2048
    def close(self): pass
ops._open_checked = lambda _: Image()
ops._load_upright = lambda image: (image, {}, None)
ops._encode_original = lambda *args: b"original"
ops._encode_thumb = lambda *args: b"thumbnail"
result = ops.prepare_image(str(root / "blue.png"), str(root))
assert "blue (100%)" in result["visual_summary"]
assert "64 by 32" in result["visual_summary"]
''')
    result = subprocess.run([sys.executable, "-I", str(script), str(module), str(tmp_path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_offline_subject_caption_includes_confident_objects_only(monkeypatch):
    observations = [
        SimpleNamespace(identifier=lambda: "bicycle", confidence=lambda: .93),
        SimpleNamespace(identifier=lambda: "motorcycle", confidence=lambda: .2),
    ]
    request = SimpleNamespace(results=lambda: observations)
    factory = SimpleNamespace(alloc=lambda: SimpleNamespace(init=lambda: request))
    handler = SimpleNamespace(performRequests_error_=lambda *args: (True, None))
    vision = SimpleNamespace(VNClassifyImageRequest=factory,
                             VNImageRequestHandler=SimpleNamespace(alloc=lambda: SimpleNamespace(
                                 initWithURL_options_=lambda *args: handler)))
    foundation = SimpleNamespace(NSURL=SimpleNamespace(fileURLWithPath_=lambda path: path))
    monkeypatch.setattr(media_image_ops.sys, "platform", "darwin")
    monkeypatch.setattr(media_image_ops.importlib, "import_module",
                         lambda name: vision if name == "Vision" else foundation)
    caption = media_image_ops._apple_subjects("fixture.png")
    assert "bicycle" in caption and "motorcycle" not in caption


def test_a_failing_vision_binding_never_breaks_the_caption(monkeypatch, caplog):
    def broken(name):
        raise OSError("vision framework crashed")

    monkeypatch.setattr(media_image_ops.sys, "platform", "darwin")
    monkeypatch.setattr(media_image_ops.importlib, "import_module", broken)
    with caplog.at_level("DEBUG", logger=media_image_ops.logger.name):
        assert media_image_ops._apple_subjects("fixture.png") == ""
    assert "classifier" in caplog.text.lower()


def test_a_request_to_skip_the_classifier_skips_it(monkeypatch, tmp_path):
    class Image:
        width, height, format = 4, 4, "PNG"
        def convert(self, mode): return self
        def thumbnail(self, size): pass
        def getdata(self): return [(0, 0, 255)] * 16
        def close(self): pass

    real = media_image_ops.importlib.import_module
    fake_hash = SimpleNamespace(phash=lambda _: "ff")
    monkeypatch.setattr(media_image_ops.importlib, "import_module",
                        lambda name: fake_hash if name == "imagehash" else real(name))
    monkeypatch.setattr(media_image_ops, "_open_checked", lambda _: Image())
    monkeypatch.setattr(media_image_ops, "_load_upright", lambda image: (image, {}, None))
    monkeypatch.setattr(media_image_ops, "_encode_original", lambda *args: b"original")
    monkeypatch.setattr(media_image_ops, "_encode_thumb", lambda *args: b"thumbnail")
    seen = []
    monkeypatch.setattr(media_image_ops, "_apple_subjects",
                        lambda path: seen.append(path) or " subject")
    skipped = media_image_ops.prepare_image(str(tmp_path / "p.png"), str(tmp_path), classify=False)
    assert seen == [] and "subject" not in skipped["visual_summary"]
    kept = media_image_ops.prepare_image(str(tmp_path / "p.png"), str(tmp_path))
    assert len(seen) == 1 and "subject" in kept["visual_summary"]
