"""Pictures keep their source identity even when their words match."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from superlocalmemory.media import open_media_store
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord
from superlocalmemory.storage.source_identity import store_source_fact

from .test_ingest import env as _env  # noqa: F401
from .test_ingest import png, save


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "slm"
    monkeypatch.setenv("SLM_DATA_DIR", str(root))
    store = open_media_store(create=True, data_root=root)
    try:
        yield _env.__wrapped__(root, store)
    finally:
        store.close()


class SourceRuntime:
    def __init__(self, db):
        self.db = db

    def remember(self, request, actor, **kwargs):
        mid, fid = uuid4().hex, uuid4().hex
        record = MemoryRecord(memory_id=mid, content=request.content,
                              profile_id=request.profile_id, scope=request.scope,
                              metadata=dict(request.metadata))
        fact = AtomicFact(fact_id=fid, memory_id=mid, content=request.content,
                          profile_id=request.profile_id, scope=request.scope)
        fid = store_source_fact(self.db, record, fact)
        return SimpleNamespace(payload={"status": "queryable", "fact_ids": [fid],
                                        "memory_id": record.memory_id, "operation_id": "test"})


@pytest.mark.parametrize("tags", [("a", "b"), ("b", "a")])
def test_different_wordless_images_keep_two_facts_and_correct_sources(env, tmp_path, tags):
    import hashlib

    original = env.client.prepare_image

    def unique_thumbnail(*args, **kwargs):
        from pathlib import Path
        info = original(*args, **kwargs)
        Path(info["thumb_path"]).write_bytes(hashlib.sha256(Path(args[0]).read_bytes()).digest())
        return info

    env.client.prepare_image = unique_thumbnail
    db = DatabaseManager(tmp_path / "identity.db")
    db.initialize(schema)
    env.runtime = SourceRuntime(db)
    try:
        a = save(env, png(tags[0]), profile_id="default")
        b = save(env, png(tags[1]), profile_id="default")
        assert a.status == b.status == "stored"
        assert a.memory_id != b.memory_id
        rows = db.execute("SELECT memory_id, metadata_json FROM memories")
        assert len(db.execute("SELECT fact_id FROM atomic_facts")) == 2
        import json
        sources = {r["memory_id"]: json.loads(r["metadata_json"])["_slm_source"] for r in rows}
        assert sources[a.memory_id]["media_id"] == a.media_id
        assert sources[b.memory_id]["media_id"] == b.media_id
        assert sources[a.memory_id]["content_sha256"] != sources[b.memory_id]["content_sha256"]
        for receipt, tag in zip((a, b), tags):
            row = env.store.get_item(receipt.media_id)
            assert row["anchor_memory_id"] == receipt.memory_id
            assert row["thumb_webp"] == hashlib.sha256(png(tag)).digest()
    finally:
        db.close()


def _white_png(size: int = 8) -> bytes:
    """A real, valid, wordless white PNG."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body)))

    raw = b"".join(b"\x00" + b"\xff\xff\xff" * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _measuring_client(env, size: int):
    """The worker's real caption function over the picture's own pixels."""
    from superlocalmemory.runtimes.media_image_ops import visual_summary

    class Pixels:
        width = height = size

        def convert(self, _mode):
            return self

        def thumbnail(self, _size):
            pass

        def getdata(self):
            return [(255, 255, 255)] * (size * size)

        def close(self):
            pass

    original = env.client.prepare_image

    def measured(*args, **kwargs):
        return {**original(*args, **kwargs), "visual_summary": visual_summary(Pixels())}

    env.client.prepare_image = measured


def test_pixel_caption_never_enters_a_picture_memorys_words_or_facts(env, tmp_path):
    from superlocalmemory.core.answer_check_stage import run_answer_check
    from superlocalmemory.media.labels import NO_TEXT
    from superlocalmemory.retrieval import answer_check_status as acs
    from superlocalmemory.retrieval import media_rerank
    from superlocalmemory.retrieval.fusion import FusionResult

    _measuring_client(env, 8)
    db = DatabaseManager(tmp_path / "caption.db")
    db.initialize(schema)
    env.runtime = SourceRuntime(db)
    try:
        receipt = save(env, _white_png(), profile_id="default")
        content = db.execute("SELECT content FROM memories")[0]["content"]
        facts = [r["content"] for r in db.execute("SELECT content FROM atomic_facts")]
        assert NO_TEXT in content and facts
        for text in [content, *facts]:
            lowered = text.lower()
            assert "pixel" not in lowered and "white" not in lowered and "image 8 by" not in lowered
            assert media_rerank.is_label_only(text)
        # The caption lives in the picture's own record, where the dashboard and fetch can show it.
        record = env.store.get_item(receipt.media_id)["exif_json"]
        assert "Dominant pixel colors: white (100%)" in record
        # The reranker leaves the wordless picture where the picture channel put it.
        fused = [FusionResult(fact_id="f1", fused_score=1.0, channel_ranks={"media": 1},
                              channel_scores={"media": 0.9})]
        assert media_rerank.neutral_ids(fused, {"f1": SimpleNamespace(content=facts[0])}) == {"f1"}
        # The answer check has nothing to judge.
        fact = SimpleNamespace(fact_id="f1", content=facts[0], profile_id="default", memory_kind="")
        results = [SimpleNamespace(fact=fact, channel_scores={"media": 0.9})]
        assert media_rerank.all_unreadable(results)
        judge = SimpleNamespace(backend="local", top_k=3,
                                assess=lambda *a, **k: pytest.fail("judged"))
        engine = SimpleNamespace(_sufficiency_judge=judge, _db=None)
        outcome = run_answer_check(engine, "what is in the picture?",
                                   SimpleNamespace(results=results))
        assert (outcome.status, outcome.detail) == (acs.STATUS_SKIPPED, acs.DETAIL_MEDIA_UNJUDGED)
        # A keyword search for the colour cannot reach it.
        assert not {"white", "pixels"} & set(content.lower().replace(".", " ").split())
    finally:
        db.close()


def test_a_picture_with_a_note_keeps_only_the_note_and_the_label(env):
    _measuring_client(env, 8)
    save(env, _white_png(), content="Whiteboard from the Monday sync")
    stored = env.runtime.requests[0].content
    assert "Whiteboard from the Monday sync" in stored
    assert "pixel" not in stored.lower() and "Dominant" not in stored
