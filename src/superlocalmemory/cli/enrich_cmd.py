# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Power-user control of the default automatic background improvements.

Plain sentences in plain output; ``--json`` for machines. A refusal from the
memory service is printed as the service's own reason (WP-19 review L1).
"""

from __future__ import annotations

import argparse
import json
import sys

from superlocalmemory.cli.daemon import DaemonUnprocessable, daemon_request

FIXES = ('entity-links', 'refile-hidden', 'repair-saves')
#: No undo record: the confirmation says so, and the service insists on it.
NO_UNDO = ('repair-saves',)
NOT_RUNNING = 'Your memory service did not answer. Start it with: slm serve'


def register_enrich_parser(sub):
    parser = sub.add_parser(
        'enrich', help='Automatic memory improvements: status, pause, resume, undo, fix')
    actions = parser.add_subparsers(dest='enrich_action', required=True)
    for action in ('status', 'pause', 'resume', 'undo', 'fix'):
        command = actions.add_parser(action)
        if action in ('pause', 'resume'):
            command.add_argument('run_id')
        elif action == 'undo':
            command.add_argument('run_id', nargs='?', default='',
                                 help='Defaults to the most recent run that changed something')
        elif action == 'fix':
            command.add_argument('repair', choices=FIXES)
            command.add_argument('--include-undone', action='store_true',
                                 help='Also apply items from a repair you undid earlier')
        if action in ('undo', 'fix'):
            command.add_argument('--yes', action='store_true',
                                 help='Confirm without asking (needed when not interactive)')
        command.add_argument('--json', action='store_true', default=argparse.SUPPRESS)


def _fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def _request(method, path, body=None):
    try:
        result = daemon_request(method, path, body, preserve_unprocessable=True)
    except DaemonUnprocessable as exc:
        _fail(exc.message)
    if not isinstance(result, dict):
        _fail(NOT_RUNNING)
    return result


def _confirmed(args, question):
    if getattr(args, 'yes', False):
        return True
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        _fail('This changes your memories. Run again with --yes to confirm.', 2)
    try:
        return input(f'{question} [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def _latest_changing_run(status):
    return next((r for r in status.get('runs') or []
                 if r.get('status') != 'undone' and r.get('undoable', True)), None)


def _print_status(result):
    print(result.get('summary', ''))
    print(f"Status: {result.get('state', '')}")
    for item in result.get('suggestions') or []:
        print(f"Can be fixed: {item.get('sentence') or str(item['found']) + ' ' + item['label']} "
              f"(slm enrich fix {item['action']})")
    runs = result.get('runs') or []
    if runs:
        print('Runs that changed something (newest first):')
    for run in runs:
        undone = '  (undone)' if run.get('status') == 'undone' else ''
        if run.get('undoable') is False:
            undone = "  (can't be undone)"
        print(f"  {run['run_id']}  {(run.get('created_at') or '')[:16].replace('T', ' ')}  "
              f"{run.get('changed', 0)} {run.get('label', '')}{undone}")


def _undo(args):
    run_id = args.run_id
    if not run_id:
        latest = _latest_changing_run(_request('GET', '/api/memory-health'))
        if latest is None:
            _fail('There is nothing to undo.')
        run_id = latest['run_id']
        question = (f"Undo run {run_id} ({latest.get('changed', 0)} "
                    f"{latest.get('label', 'changes')})?")
    else:
        question = f'Undo run {run_id}?'
    if not _confirmed(args, question):
        _fail('Nothing was changed.', 1)
    result = _request('POST', '/api/memory-health/action', {'action': 'undo', 'run_id': run_id})
    kept = result.get('conflicts') or 0
    text = f"Undone: {result.get('restored', 0)} changes restored."
    return result, text + (f' {kept} kept because they were changed since.' if kept else '')


def _fix(args):
    name = args.repair.replace('-', ' ')
    undoable = args.repair not in NO_UNDO
    question = (f'Fix {name} now? You can undo it later.' if undoable else
                f"Fix {name} now? This can't be undone; a safety copy is taken first.")
    if not _confirmed(args, question):
        _fail('Nothing was changed.', 1)
    body = {'action': 'repair', 'repair': args.repair}
    if not undoable:
        body['confirm'] = True
    if getattr(args, 'include_undone', False):
        body['include_undone'] = True
    result = _request('POST', '/api/memory-health/action', body)
    done = f"Fixed {result.get('fixed', 0)} of {result.get('found', 0)}."
    if not undoable:
        return result, f"{done} This can't be undone."
    return result, f"{done} Undo with: slm enrich undo {result.get('run_id', '')}".rstrip()


def cmd_enrich(args):
    action = args.enrich_action
    if action == 'status':
        result, text = _request('GET', '/api/memory-health'), None
    elif action == 'undo':
        result, text = _undo(args)
    elif action == 'fix':
        result, text = _fix(args)
    else:
        result = _request('POST', '/api/memory-health/action',
                          {'action': action, 'run_id': args.run_id})
        text = 'Paused. Your memories still work.' if action == 'pause' else 'Resumed.'
    if getattr(args, 'json', False):
        print(json.dumps(result))
    elif text is None:
        _print_status(result)
    else:
        print(text)
