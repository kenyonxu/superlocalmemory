# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The full data export holds up on a store that carries every awkward type.

4.1.25 audit G-C2: the dashboard export and ``/api/compliance/gdpr/export`` died
on ``Object of type bytes is not JSON serializable`` because an embedding is a
BLOB in a TEXT-declared column, and ``slm gdpr export`` hid the same defect by
passing ``default=str`` (writing a Python ``b'..'`` repr into an Art. 20 file).
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import math
import pathlib
import uuid
from argparse import Namespace

import pytest

from superlocalmemory.compliance.export_encoding import (
    ExportOptions,
    dumps,
    encode_row,
    encode_value,
)
from superlocalmemory.compliance.gdpr import GDPRCompliance
from tests.test_compliance.awkward_store import (
    EMBEDDING_BYTES,
    FISHER_BYTES,
    MODEL_STATE_BYTES,
    PROFILE,
    THUMB_BYTES,
    build_awkward_store,
)


@pytest.fixture()
def store(engine_with_mock_deps):
    info = build_awkward_store(engine_with_mock_deps)
    info["engine"] = engine_with_mock_deps
    return info


def _gdpr(store) -> GDPRCompliance:
    return GDPRCompliance(store["engine"]._db, data_root=store["root"])


# --------------------------------------------------------------------- encoding

def test_every_python_type_a_row_can_hold_becomes_valid_json() -> None:
    row = {
        "when": dt.datetime(2026, 10, 1, 12, 30, tzinfo=dt.timezone.utc),
        "day": dt.date(2026, 10, 1),
        "price": decimal.Decimal("12.50"),
        "tags": {"b", "a"},
        "ident": uuid.UUID(int=7),
        "where": pathlib.Path("/tmp/x"),
        "nested": {"when": dt.date(2026, 1, 2), "pair": (1, 2)},
        "nothing": None, "flag": True, "count": 3, "ratio": 0.5, "text": "t",
    }
    text = dumps(encode_row(row, "t", ExportOptions()))
    back = json.loads(text)
    assert back["when"] == "2026-10-01T12:30:00+00:00"
    assert back["day"] == "2026-10-01"
    assert back["price"] == "12.50"
    assert sorted(back["tags"]) == ["a", "b"]
    assert back["nested"] == {"when": "2026-01-02", "pair": [1, 2]}
    assert back["flag"] is True and back["count"] == 3 and back["nothing"] is None


def test_non_finite_numbers_never_reach_the_file_as_bare_tokens() -> None:
    for bad in (float("inf"), float("-inf"), float("nan")):
        value = encode_value(bad)
        assert not isinstance(value, float) or math.isfinite(value)
    # json.loads rejects nothing here, but strict consumers reject "Infinity".
    json.loads(dumps({"x": encode_value(float("inf"))}), parse_constant=lambda c: pytest.fail(c))


def test_binary_columns_are_left_out_by_default_and_listed() -> None:
    options = ExportOptions()
    row = encode_row({"id": "a", "embedding": b"\x00\x01", "blob": b"zz", "note": "x"},
                     "atomic_facts", options)
    assert row == {"id": "a", "note": "x"}
    assert options.omitted["atomic_facts"] == {"embedding", "blob"}


def test_a_text_embedding_is_also_left_out_by_default() -> None:
    options = ExportOptions()
    row = encode_row({"id": "a", "embedding": "[0.1, 0.2]", "fisher_mean": None},
                     "atomic_facts", options)
    assert row == {"id": "a"}


def test_binary_columns_round_trip_as_base64_when_asked() -> None:
    options = ExportOptions(include_embeddings=True)
    row = encode_row({"id": "a", "embedding": b"\x00\xffabc"}, "atomic_facts", options)
    assert base64.b64decode(row["embedding"]) == b"\x00\xffabc"
    assert options.base64_columns["atomic_facts"] == {"embedding"}
    assert options.omitted == {}


# ----------------------------------------------------------------- the full export

def test_export_of_a_real_store_is_plain_json(store) -> None:
    data = _gdpr(store).export_profile_data(PROFILE)
    # No default=str, no allow_nan: this is what a strict reader would parse.
    text = json.dumps(data, allow_nan=False)
    assert "b'" not in text and "\\x" not in text
    facts = {f["fact_id"]: f for f in data["atomic_facts"]}
    assert set(facts) >= {"f-embedded", "f-hindi", "f-plain", "f-badutf8"}
    assert "embedding" not in facts["f-embedded"]
    assert "fisher_mean" not in facts["f-embedded"]
    assert facts["f-hindi"]["content"].startswith("मुझे")


def test_text_that_is_not_utf8_and_an_infinite_real_are_exported(store) -> None:
    facts = {f["fact_id"]: f for f in _gdpr(store).export_profile_data(PROFILE)["atomic_facts"]}
    assert "�" in facts["f-badutf8"]["content"]
    assert not isinstance(facts["f-badutf8"]["confidence"], float) \
        or math.isfinite(facts["f-badutf8"]["confidence"])


def test_embeddings_are_exported_as_base64_on_request(store) -> None:
    data = _gdpr(store).export_profile_data(PROFILE, include_embeddings=True)
    facts = {f["fact_id"]: f for f in data["atomic_facts"]}
    assert base64.b64decode(facts["f-embedded"]["embedding"]) == EMBEDDING_BYTES
    assert base64.b64decode(facts["f-embedded"]["fisher_mean"]) == FISHER_BYTES
    json.dumps(data, allow_nan=False)


def test_binary_columns_in_other_tables_and_files_follow_the_same_rule(store) -> None:
    gdpr = _gdpr(store)
    plain = gdpr.export_profile_data(PROFILE)
    polar = plain["polar_embeddings"][0]
    assert "angle_indices" not in polar and "qjl_bits" not in polar and polar["radius"] == 1.5
    states = plain["learning_signals"]["learning_model_state"]
    assert states and "state_bytes" not in states[0]
    json.dumps(plain, allow_nan=False)

    full = gdpr.export_profile_data(PROFILE, include_embeddings=True)
    assert base64.b64decode(full["learning_signals"]["learning_model_state"][0]["state_bytes"]) \
        == MODEL_STATE_BYTES
    assert base64.b64decode(full["polar_embeddings"][0]["angle_indices"]) == b"\x01\x02\x03"


def test_the_thumbnail_blob_never_reaches_the_media_section(store) -> None:
    media = _gdpr(store).export_profile_data(PROFILE, include_embeddings=True)["media"]
    assert media["pictures"][0]["has_thumbnail"] is True
    assert base64.b64encode(THUMB_BYTES).decode() not in json.dumps(media)
    assert media["documents"][0]["title"] == "Lease"


def test_the_export_says_what_it_left_out_and_why(store) -> None:
    manifest = _gdpr(store).export_profile_data(PROFILE)["export_manifest"]
    assert manifest["embeddings"] == "omitted"
    omitted_facts = set(manifest["omitted_columns"]["atomic_facts"])
    assert {"embedding", "fisher_mean", "fisher_variance"} <= omitted_facts
    assert "angle_indices" in manifest["omitted_columns"]["polar_embeddings"]
    assert manifest["how_to_include"]
    assert any("search index" in item["reason"] for item in manifest["not_exported"])
    full = _gdpr(store).export_profile_data(PROFILE, include_embeddings=True)["export_manifest"]
    assert full["embeddings"] == "included_base64" and not full["omitted_columns"]


def test_the_audit_records_about_the_person_are_in_the_export(store) -> None:
    data = _gdpr(store).export_profile_data(PROFILE)
    chain = data["audit_chain"]
    assert [row["agent_id"] for row in chain] == ["claude"]  # not the other profile's row
    assert chain[0]["event_hash"] and chain[0]["prev_hash"]
    assert any(r["action"] == "export" for r in data["compliance_audit"])
    assert data["total_items"] >= len(data["atomic_facts"]) + len(chain)


def test_streamed_file_equals_the_dict_and_keeps_the_aliases(store, tmp_path) -> None:
    gdpr = _gdpr(store)
    target = tmp_path / "out.json"
    with target.open("w+b") as fp:
        summary = gdpr.write_profile_export(PROFILE, fp)
    streamed = json.loads(target.read_text(encoding="utf-8"))
    assert summary["bytes"] == target.stat().st_size
    assert summary["total_items"] == streamed["total_items"]
    assert streamed["facts"] == streamed["atomic_facts"]
    assert streamed["entities"] == streamed["canonical_entities"]
    direct = gdpr.export_profile_data(PROFILE)
    for key in ("atomic_facts", "memories", "polar_embeddings", "audit_chain", "media",
                "learning_signals", "export_manifest"):
        assert streamed[key] == json.loads(json.dumps(direct[key], allow_nan=False)), key
    assert streamed["total_items"] >= summary["tables"]["atomic_facts"]


def test_streaming_a_large_store_does_not_hold_it_in_memory(store, tmp_path) -> None:
    import tracemalloc

    db = store["engine"]._db
    rows = [(f"big-{i}", "m-plain", PROFILE, f"fact {i}", EMBEDDING_BYTES) for i in range(3000)]
    with db.raw_connection() as conn:
        conn.executemany(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, embedding) "
            "VALUES (?, ?, ?, ?, ?)", rows)
    target = tmp_path / "big.json"
    tracemalloc.start()
    with target.open("w+b") as fp:
        _gdpr(store).write_profile_export(PROFILE, fp, include_embeddings=True)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert target.stat().st_size > 8_000_000
    assert peak < target.stat().st_size / 3, (peak, target.stat().st_size)


# ---------------------------------------------------------------------------- CLI

def _run_cli(store, monkeypatch, capsys, **overrides):
    from superlocalmemory.cli import gdpr_cmd

    monkeypatch.setenv("SLM_DATA_DIR", str(store["root"]))
    args = Namespace(profile=PROFILE, output=None, json=False, include_embeddings=False)
    for key, value in overrides.items():
        setattr(args, key, value)
    try:
        gdpr_cmd._cmd_gdpr_export(args)
        code = 0
    except SystemExit as stop:
        code = int(stop.code or 0)
    return code, capsys.readouterr()


def test_cli_export_to_file_writes_plain_json(store, monkeypatch, capsys, tmp_path) -> None:
    target = tmp_path / "me.json"
    code, out = _run_cli(store, monkeypatch, capsys, output=str(target))
    assert code == 0, out.err
    text = target.read_text(encoding="utf-8")
    data = json.loads(text)
    assert "b'" not in text and "embedding" not in data["atomic_facts"][0]
    assert str(target.resolve()) in out.out and "items" in out.out


def test_cli_export_to_stdout_is_one_valid_document(store, monkeypatch, capsys) -> None:
    code, out = _run_cli(store, monkeypatch, capsys)
    assert code == 0, out.err
    assert json.loads(out.out)["profile_id"] == PROFILE


def test_cli_json_envelope_wraps_the_export(store, monkeypatch, capsys) -> None:
    code, out = _run_cli(store, monkeypatch, capsys, json=True)
    envelope = json.loads(out.out)
    assert code == 0 and envelope["success"] is True
    assert envelope["command"] == "gdpr-export"
    assert envelope["data"]["profile_id"] == PROFILE and envelope["data"]["atomic_facts"]


def test_cli_include_embeddings_flag_writes_base64(store, monkeypatch, capsys, tmp_path) -> None:
    target = tmp_path / "me.json"
    code, out = _run_cli(store, monkeypatch, capsys, output=str(target), include_embeddings=True)
    assert code == 0, out.err
    facts = {f["fact_id"]: f for f in json.loads(target.read_text())["atomic_facts"]}
    assert base64.b64decode(facts["f-embedded"]["embedding"]) == EMBEDDING_BYTES


def test_cli_flag_is_registered_on_the_parser(tmp_path) -> None:
    import os
    import subprocess
    import sys

    env = {**os.environ, "SLM_DATA_DIR": str(tmp_path)}
    done = subprocess.run(
        [sys.executable, "-m", "superlocalmemory.cli", "gdpr", "export", "--help"],
        capture_output=True, text=True, env=env, timeout=120)
    assert "--include-embeddings" in done.stdout, done.stderr


def test_the_evidence_bundle_export_meets_no_binary_column(engine_with_mock_deps) -> None:
    """`slm evidence export` has its own writer: it drops the vector columns of
    ``atomic_facts`` and reads every other table whole. That is safe only while no
    other table it reads holds a binary column, so a table that gains one must
    fail here rather than in a user's export."""
    from superlocalmemory.compliance.export_encoding import is_vector_column
    from superlocalmemory.core.evidence_bundle import _TABLES

    db = engine_with_mock_deps._db
    for _, table, _ in _TABLES:
        if table == "atomic_facts":
            continue
        for column in db.execute(f"PRAGMA table_info({table})"):
            kind = str(column["type"] or "").upper()
            where = (table, column["name"])
            assert "BLOB" not in kind and not is_vector_column(column["name"]), where
