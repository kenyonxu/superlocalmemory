# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Write a large JSON document one record at a time.

An export used to build one dict of every row of every table and then one string
of the whole thing, so a store of a few hundred thousand memories needed several
times its size in memory before the first byte left. This writer takes rows from
an iterator and puts each on the file as it arrives, so the memory used does not
depend on how much is exported.

The file must be seekable: an array that has already been written can be copied
byte-for-byte under a second key (:meth:`JsonObjectWriter.alias`), which is how
the long-standing friendly names (``facts`` for ``atomic_facts``) stay in the
document without a second query and without a second copy in memory.
"""

from __future__ import annotations

from typing import Any, BinaryIO, Iterable

from superlocalmemory.compliance.export_encoding import dumps

_COPY_CHUNK = 1 << 20


class JsonObjectWriter:
    """A single top-level JSON object written key by key into a seekable file."""

    def __init__(self, fp: BinaryIO) -> None:
        self._fp = fp
        self._wrote_key = False
        self._ranges: dict[str, tuple[int, int]] = {}

    def begin(self) -> None:
        self._fp.write(b"{")

    def end(self) -> None:
        self._fp.write(b"\n}\n")

    def _key(self, key: str) -> None:
        self._fp.write((",\n" if self._wrote_key else "\n").encode("ascii"))
        self._fp.write((dumps(key) + ": ").encode("utf-8"))
        self._wrote_key = True

    def value(self, key: str, value: Any) -> None:
        """A key whose value is small enough to hold in memory."""
        self._key(key)
        self._fp.write(dumps(value, indent=1).encode("utf-8"))

    def array(self, key: str, records: Iterable[Any]) -> int:
        """An array written one record per line; returns how many were written."""
        self._key(key)
        start = self._fp.tell()
        count = 0
        self._fp.write(b"[")
        for record in records:
            self._fp.write(b",\n " if count else b"\n ")
            self._fp.write(dumps(record).encode("utf-8"))
            count += 1
        self._fp.write(b"\n]" if count else b"]")
        self._ranges[key] = (start, self._fp.tell())
        return count

    def alias(self, key: str, source: str) -> None:
        """Write ``source``'s array again under ``key``, copied from the file."""
        start, end = self._ranges[source]
        self._key(key)
        resume = self._fp.tell()
        offset = start
        while offset < end:
            self._fp.seek(offset)
            chunk = self._fp.read(min(_COPY_CHUNK, end - offset))
            self._fp.seek(resume)
            self._fp.write(chunk)
            resume += len(chunk)
            offset += len(chunk)

    def has_array(self, key: str) -> bool:
        return key in self._ranges


def copy_file(source: BinaryIO, target: Any) -> int:
    """Copy ``source`` (from its start) to a binary or text stream; returns bytes."""
    source.seek(0)
    sink = getattr(target, "buffer", target)
    total = 0
    while True:
        chunk = source.read(_COPY_CHUNK)
        if not chunk:
            break
        sink.write(chunk)
        total += len(chunk)
    return total


__all__ = ["JsonObjectWriter", "copy_file"]
