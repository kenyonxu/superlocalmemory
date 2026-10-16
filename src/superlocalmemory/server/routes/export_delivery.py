# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Hand an export to the browser only once it is whole.

A streamed response commits to ``200`` before the first row is read, so a failure
half-way leaves the person with a file that looks complete and is not. The
exports are therefore written to an owner-only temporary file first, in a worker
thread (the event loop stays free), and the response is built from the finished
file: a failure is an error status with nothing to download, memory use does not
grow with the size of the export, and the length is known up front.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

logger = logging.getLogger("superlocalmemory.routes.export_delivery")


@dataclass(frozen=True)
class SpooledExport:
    path: Path
    count: int
    size: int
    gzipped: bool


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - nothing useful to do
        logger.warning("export: could not remove a temporary file")


def _gzip_file(source: Path) -> Path:
    """Compress next to ``source``; the copy is owner-only like the original (0600)."""
    target = source.with_name(source.name + ".gz")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(source, "rb") as src, os.fdopen(fd, "wb") as raw, \
            gzip.GzipFile(fileobj=raw, mode="wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    return target


def spool_export(
    write: Callable[[BinaryIO], int], *, gzip_over: int | None = None,
) -> SpooledExport:
    """Run ``write(fp)`` into a temporary file; compress it when it passes ``gzip_over`` bytes.

    ``write`` returns the number of records written. On any failure the temporary
    files are removed and the exception propagates.
    """
    fd, name = tempfile.mkstemp(prefix="slm-export-", suffix=".tmp")  # owner-only (0600)
    path = Path(name)
    produced: list[Path] = [path]
    try:
        with os.fdopen(fd, "w+b") as fp:
            count = write(fp)
            fp.flush()
            size = fp.tell()
        if gzip_over is not None and size > gzip_over:
            compressed = _gzip_file(path)
            produced.append(compressed)
            _unlink(path)
            return SpooledExport(compressed, count, compressed.stat().st_size, True)
        return SpooledExport(path, count, size, False)
    except BaseException:
        for leftover in produced:
            _unlink(leftover)
        raise


async def export_response(
    write: Callable[[BinaryIO], int], *, media_type: str, filename: str,
    gzip_over: int | None = None, gz_media_type: str = "application/gzip",
) -> FileResponse:
    """Build the file in a worker thread and answer with it; failure is a 500."""
    try:
        spooled = await asyncio.to_thread(spool_export, write, gzip_over=gzip_over)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("export failed")
        raise export_failed(exc) from exc
    return FileResponse(
        spooled.path,
        media_type=gz_media_type if spooled.gzipped else media_type,
        filename=filename + (".gz" if spooled.gzipped else ""),
        content_disposition_type="attachment",
        headers={"X-SLM-Records": str(spooled.count)},
        background=BackgroundTask(_unlink, spooled.path),
    )


def export_failed(exc: BaseException) -> HTTPException:
    """The error the person sees: what failed, never the cause (that is in the log)."""
    return HTTPException(
        status_code=500,
        detail=f"Export failed ({type(exc).__name__}). The daemon log has the details.",
    )


__all__ = ["SpooledExport", "export_failed", "export_response", "spool_export"]
