"""Bounded dashboard diagnostics using the same CLI doctor as operators."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from fastapi import HTTPException

from superlocalmemory.cli.deployment import runtime_port
from superlocalmemory.infra.data_root import canonical_data_root


async def run_doctor(*, repair: bool = False) -> dict:
    command = [sys.executable, "-m", "superlocalmemory.cli.main", "doctor", "--quick", "--json"]
    if repair:
        command.append("--fix")
    env = {
        **os.environ,
        "SLM_DATA_DIR": str(canonical_data_root()),
        "SLM_DAEMON_PORT": str(runtime_port()),
    }
    process = await asyncio.create_subprocess_exec(
        *command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except (TimeoutError, asyncio.CancelledError) as exc:
        process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(504, "Diagnostics took too long. Retry the check.") from None
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and result.get("command") == "doctor" and result.get("success"):
            data = result.get("data") or {}
            checks = [
                {key: row.get(key, "") for key in ("name", "status", "detail")}
                for row in data.get("checks", [])
                if isinstance(row, dict)
            ]
            return {
                "success": process.returncode == 0,
                "checks": checks,
                "summary": data.get("summary", {}),
            }
    raise HTTPException(502, "Diagnostics could not be confirmed. Retry the check.")
