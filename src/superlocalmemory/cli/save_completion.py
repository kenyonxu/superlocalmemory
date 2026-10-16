"""A synchronous save only succeeds after canonical materialization."""
from __future__ import annotations

import sys


def require_completed_receipt(result: dict, use_json: bool) -> None:
    if result.get('materialization_state') == 'complete':
        return
    message = ('Memory saved, but enrichment is still pending. Retry the same '
               'command to retrieve its completion receipt.')
    if use_json:
        from superlocalmemory.cli.json_output import json_print

        json_print('remember', data=result, error={
            'code': 'MATERIALIZATION_INCOMPLETE', 'message': message})
    else:
        print(message, file=sys.stderr)
    raise SystemExit(1)
