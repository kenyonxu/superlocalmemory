# tests/test_quality/test_cjk_corpus.py
"""The CJK corpus module must not perturb the EN corpus, and vice versa."""
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, _REPO / "scripts" / "quality" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_en_digest_is_byte_stable():
    en = _load("answerability_corpus")
    assert en.digest() == "d21fd3d53039f6e6"  # the cached store's digest


def test_cjk_digest_differs_from_en():
    en, cjk = _load("answerability_corpus"), _load("answerability_corpus_cjk")
    assert cjk.digest() != en.digest()


def test_cjk_corpus_loads_anchors_and_unique_rows():
    cjk = _load("answerability_corpus_cjk")
    rows = cjk.corpus()
    anchors = [r for r in rows if not r.key.startswith("fill-")]
    assert len(anchors) >= 3
    assert len({r.key for r in rows}) == len(rows)


def test_cjk_filler_carries_chinese_human_templates():
    cjk = _load("answerability_corpus_cjk")
    rows = [r.content for r in cjk.fillers()
            if r.kind in ("meeting", "maintenance")]
    assert rows and all(any("\u4e00" <= ch <= "\u9fff" for ch in t) for t in rows)
