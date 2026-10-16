# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Daemon-backed local calibration: start and poll with no store opened by CLI."""

from __future__ import annotations

import json
import sys

from superlocalmemory.cli.daemon import DaemonConflict, DaemonUnprocessable, daemon_request

PATH = "/api/v3/answer-check/calibration"


def run(args):
    status = args.embedder_command == "calibration-status"
    cancel = args.embedder_command == "calibration-cancel"
    body = None if status else {} if cancel else {"model": args.model, "samples": args.samples}
    try:
        data = daemon_request(
            "GET" if status else "POST",
            PATH + "/cancel" if cancel else PATH,
            body,
            preserve_conflict=True,
            preserve_unprocessable=True,
        )
    except (DaemonConflict, DaemonUnprocessable) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    if data is None:
        print("The SLM daemon is not running. Start it with: slm serve", file=sys.stderr)
        raise SystemExit(1)
    if getattr(args, "json", False):
        print(json.dumps(data))
    else:
        print(
            f"Calibration: {data['state']} — {data.get('detail', '')} "
            f"({data.get('done', 0)}/{data.get('total', 0)})."
        )
        if not status and not cancel:
            print("Progress: slm embedder calibration-status")
