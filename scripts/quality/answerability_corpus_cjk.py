# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The Chinese (CJK) twin of the answerability corpus.

Same shape, same machinery, same row counts as the EN store
(scripts/quality/answerability_corpus.py): a minority of human-readable notes
buried in a majority of ID-heavy rows, unevenly described, lifecycles spread
like a real store's. The EN module is imported, never copied or edited, so its
digest stays byte-stable; only what a Chinese agent would actually have
written differs here. The human-readable filler templates (meeting minutes,
maintenance tickets, session summaries, word-bearing stubs) are Chinese; the
ID-heavy rows (receipts, logs, commits, tool events, hex stubs) stay on the
shared EN generators, which are language-neutral -- a real Chinese agent's
store is full of machine-English hex lines too.

Anchors live in ``tests/fixtures/answerability_cjk/anchors.jsonl`` (seeded
with three rows; the calibration tasks fill the rest). Same code, same rows,
same order on every machine: only ``hashlib``, through the EN ``_Stream``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

#: Bump when the generated rows change; recorded baselines name the version.
CORPUS_VERSION = "answerability-corpus-cjk-v1"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "answerability_cjk"

_HERE = Path(__file__).resolve().parent


def _load_en():
    """The EN corpus module, loaded the way the eval loads corpus modules:
    by file path (``scripts/quality`` is not a package) and registered under
    its import name, which its PEP 563 dataclass needs while it executes."""
    name = "answerability_corpus"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "answerability_corpus.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_en = _load_en()

# Shared, language-neutral machinery: the row type, the deterministic stream,
# the interleaving and the slice/lifecycle/date spread, and the filler counts.
Memory = _en.Memory
LEGACY, DESCRIBED = _en.LEGACY, _en.DESCRIBED
_Stream = _en._Stream
interleave = _en.interleave
_slice_of = _en._slice_of
_lifecycle_of = _en._lifecycle_of
_date = _en._date
_FILLER_COUNTS = _en._FILLER_COUNTS
_PROJECTS = _en._PROJECTS

# The Chinese vocabulary of the human-readable templates.
_PEOPLE = ("陈立群", "王雅文", "林志远", "高美玲", "赵国栋",
           "周晓兰", "吴建辉", "郑淑芬", "孙浩然", "许静怡")
_COMPONENTS = ("网关", "计费服务", "任务队列", "调度器", "仪表盘", "导出器",
               "摄取队列", "搜索索引", "认证服务", "备份任务")
_TOPICS = ("错误预算", "发布检查清单", "值班轮换", "压测计划",
           "缓存预热", "重试策略", "日志格式", "告警阈值",
           "schema 评审", "依赖审计")


def _session(s: _Stream, i: int) -> str:
    return (f"会话 {s.uuid()} 已结束。写入 {s.below(40)} 条记忆，召回 {s.below(25)} 次，"
            f"时长 {1 + s.below(180)} 分钟。")


def _meeting(s: _Stream, i: int) -> str:
    return (f"会议 {i}：{s.pick(_PEOPLE)}提出{s.pick(_COMPONENTS)}的{s.pick(_TOPICS)}需要在"
            f"第 {10 + s.below(40)} 个迭代重新过一遍，由{s.pick(_PEOPLE)}整理纪要。")


def _maintenance(s: _Stream, i: int) -> str:
    component = s.pick(_COMPONENTS)
    return (f"{_date(s)}，{s.pick(_PROJECTS)} 的{component}维护工单 AUD-{1000 + i} 由 "
            f"{s.pick(_PEOPLE)} 复核。共记录 {20 + s.below(37)} 次成功检查、"
            f"{s.below(4)} 次重试。{component}构建版本为 v{1 + s.below(4)}.{s.below(12)}."
            f"{s.below(30)}，{1 + s.below(14)} 天后复检。")


def _stub(s: _Stream, i: int) -> str:
    # Under 40 characters, as real stubs are. The word-bearing forms are
    # Chinese; the id-bearing forms keep the shared machine text.
    form = i % 5
    if form == 0:
        return f"[] 会话已结束 {_date(s)} #{i}"
    if form == 1:
        return f"rcpt asdk_app_{s.hex(12)} ok"
    if form == 2:
        return f"ok {s.hex(8)} ({i})"
    if form == 3:
        return f"run {s.uuid()[:23]}"
    return f"已保存 {s.below(9)} 条 #{i}"


#: The ID-heavy rows are the EN generators verbatim: hex, UUIDs, ids and api
#: words carry no language, and reusing them keeps the hub geometry shared.
_MAKERS = {
    "receipt": _en._receipt, "log": _en._log, "commit": _en._commit,
    "tool_event": _en._tool_event,
    "session": _session, "meeting": _meeting,
    "maintenance": _maintenance, "stub": _stub,
}


def anchors() -> list[Memory]:
    rows = []
    stream = _Stream(CORPUS_VERSION + ":anchor-dates")
    for line in (FIXTURES / "anchors.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows.append(Memory(row["key"], row["content"], row["kind"], row["project"],
                               _date(stream), _slice_of(row["key"]),
                               _lifecycle_of(row["key"], anchor=True)))
    return rows


def fillers() -> list[Memory]:
    rows: list[Memory] = []
    for kind, count in _FILLER_COUNTS.items():
        stream = _Stream(f"{CORPUS_VERSION}:{kind}")
        for i in range(count):
            content = _MAKERS[kind](stream, i)
            key = f"fill-{kind}-{i}"
            rows.append(Memory(key, content, kind, _PROJECTS[i % len(_PROJECTS)],
                               _date(stream), _slice_of(key), _lifecycle_of(key, anchor=False)))
    return rows


def corpus() -> list[Memory]:
    rows = interleave(anchors(), fillers())
    seen = set()
    for row in rows:
        if row.key in seen or row.content in seen:
            raise ValueError(f"duplicate row in the answerability corpus: {row.key}")
        seen.add(row.key)
        seen.add(row.content)
    return rows


def load_questions(name: str) -> list[dict]:
    path = FIXTURES / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def digest() -> str:
    """Identifies the store: corpus rows plus the generator version."""
    h = hashlib.sha256(CORPUS_VERSION.encode("utf-8"))
    for row in corpus():
        h.update(json.dumps([row.key, row.content, row.kind, row.project, row.session_date,
                             row.slice, row.lifecycle], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


def slice_of_key() -> dict[str, str]:
    return {row.key: row.slice for row in corpus()}
