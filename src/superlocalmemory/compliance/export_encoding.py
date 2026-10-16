# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Turn any value a SQLite row can hold into JSON, and say what was left out.

Both data exports (the memory export and the full Art. 15/20 export) used to be
``SELECT *`` followed by ``json.dumps``. SQLite lets a column declared TEXT hold
a BLOB, and that is exactly what an embedding is, so on a store the engine had
written the dump died on ``bytes is not JSON serializable`` and the person got
nothing. This module is the one place that knows how each type is written, so
no export can meet a value it cannot serialise:

* binary values (BLOBs) and vector columns (an embedding, a Fisher vector) are
  left out by default, because they are derived numbers, not what the person
  wrote, and they make a file ten times larger. ``include_embeddings`` writes
  them as base64 instead. Either way the export's manifest lists the columns.
* datetimes, dates, Decimals, UUIDs, paths, sets and tuples become their usual
  text or list forms;
* a non-finite float (``Infinity``, ``NaN``) becomes a string, because a bare
  ``Infinity`` token is not JSON and a strict reader rejects the whole file.
"""

from __future__ import annotations

import base64
import datetime as _dt
import decimal
import enum
import json
import math
import pathlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

#: Columns that hold a vector no matter how SQLite stored it. Before the vector
#: columns were converted to BLOBs they held JSON text, and old stores still do.
VECTOR_COLUMNS = frozenset({"embedding", "fisher_mean", "fisher_variance"})

_BINARY_TYPES = (bytes, bytearray, memoryview)

#: Why each kind of table is not part of an export, for the manifest.
SEARCH_INDEX_REASON = (
    "search index derived from the exported text (full-text and vector "
    "indexes); it is rebuilt from the memories, and holds nothing of yours that "
    "the exported records do not"
)
SYSTEM_TABLE_REASON = (
    "installation or sign-in bookkeeping (schema versions, settings, other "
    "users' credentials and sessions); it is not data about you"
)


def is_vector_column(name: str) -> bool:
    """Whether ``name`` is a column that holds an embedding-style vector."""
    return name in VECTOR_COLUMNS or name.endswith("_embedding")


@dataclass
class ExportOptions:
    """How an export treats binary data, and a record of what it did.

    ``omitted`` and ``base64_columns`` fill in while rows are encoded, so the
    manifest written at the end of an export reflects the data that was read,
    not a guess made beforehand.
    """

    include_embeddings: bool = False
    omitted: dict[str, set[str]] = field(default_factory=dict)
    base64_columns: dict[str, set[str]] = field(default_factory=dict)
    not_exported: list[dict[str, Any]] = field(default_factory=list)
    skipped_tables: dict[str, str] = field(default_factory=dict)

    def note_omitted(self, table: str, column: str) -> None:
        self.omitted.setdefault(table, set()).add(column)

    def note_base64(self, table: str, column: str) -> None:
        self.base64_columns.setdefault(table, set()).add(column)

    def manifest(self) -> dict[str, Any]:
        """The export's own account of what is and is not in it."""
        return {
            "embeddings": "included_base64" if self.include_embeddings else "omitted",
            "omitted_columns": {t: sorted(c) for t, c in sorted(self.omitted.items())},
            "base64_columns": {t: sorted(c) for t, c in sorted(self.base64_columns.items())},
            "how_to_include": (
                "Binary and vector columns (embeddings, Fisher vectors, quantised "
                "codes, model state) are derived numbers, not text you wrote. They "
                "are omitted by default; export with --include-embeddings (CLI) or "
                "include_embeddings=true (HTTP) to get them as base64."
            ),
            "not_exported": list(self.not_exported),
            "tables_not_read": dict(self.skipped_tables),
        }


def _finite(value: float) -> Any:
    return value if math.isfinite(value) else str(value)


def encode_value(value: Any, *, include_binary: bool = False) -> Any:
    """``value`` as something ``json`` can write, whatever SQLite or Python gave."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return _finite(value)
    if isinstance(value, _BINARY_TYPES):
        return base64.b64encode(bytes(value)).decode("ascii") if include_binary else None
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (uuid.UUID, pathlib.PurePath)):
        return str(value)
    if isinstance(value, enum.Enum):
        return encode_value(value.value, include_binary=include_binary)
    if isinstance(value, Mapping):
        return {str(k): encode_value(v, include_binary=include_binary) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((encode_value(v, include_binary=include_binary) for v in value), key=str)
    if isinstance(value, (list, tuple)):
        return [encode_value(v, include_binary=include_binary) for v in value]
    return str(value)


def encode_row(row: Mapping[str, Any], table: str, options: ExportOptions) -> dict[str, Any]:
    """One row as a JSON-safe dict, applying the binary/vector rule of ``options``."""
    out: dict[str, Any] = {}
    for column, value in row.items():
        binary = isinstance(value, _BINARY_TYPES)
        if is_vector_column(column) or binary:
            if not options.include_embeddings:
                options.note_omitted(table, column)
                continue
            if binary:
                options.note_base64(table, column)
        out[column] = encode_value(value, include_binary=options.include_embeddings)
    return out


def dumps(value: Any, **kwargs: Any) -> str:
    """``json.dumps`` that cannot meet a type it cannot write, nor a bare NaN."""
    return json.dumps(value, default=_default, allow_nan=False, **kwargs)


def _default(value: Any) -> Any:
    return encode_value(value)


__all__ = [
    "ExportOptions", "SEARCH_INDEX_REASON", "SYSTEM_TABLE_REASON", "VECTOR_COLUMNS",
    "dumps", "encode_row", "encode_value", "is_vector_column",
]
