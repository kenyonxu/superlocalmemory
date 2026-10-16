# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Bind pytest configuration to a pinned file or a generated empty config."""

import json
import re
from pathlib import Path

from superlocalmemory.loops.artifact_revision import copy_inputs
from superlocalmemory.loops.private_workspace import private_file, temporary

_CONFIGS = ('pytest.ini', '.pytest.ini', 'pytest.toml', '.pytest.toml',
            'pyproject.toml', 'tox.ini', 'setup.cfg')


def _pytest(command):
    """Return the pytest argument boundary, preserving interpreter options."""
    executable = Path(command[0]).name
    if re.fullmatch(r'(pytest|py\.test)(-?\d+(\.\d+)*)?', executable):
        return 1
    if not re.fullmatch(r'(python|pypy)(\d+(\.\d+)*)?[dt]?', executable):
        return None
    index = 1
    while index < len(command):
        arg = command[index]
        if arg == '--check-hash-based-pycs':
            index += 2
            continue
        if arg == '--' or not arg.startswith('-') or arg == '-':
            return None
        if not arg.startswith('--'):
            for offset, option in enumerate(arg[1:], 1):
                if option == 'c':
                    return None  # Anything after -c belongs to that script.
                if option == 'm':
                    module = arg[offset + 1:] or (
                        command[index + 1] if index + 1 < len(command) else '')
                    boundary = index + (1 if arg[offset + 1:] else 2)
                    return boundary if module in ('pytest', 'pytest.__main__') else None
                if option in ('W', 'X'):
                    if not arg[offset + 1:]:
                        index += 1
                    break
        index += 1
    return None


def _options(command):
    clean, config = [], None
    args = iter(command)
    for arg in args:
        if arg == '--':
            clean.extend([arg, *args])
            break
        if arg in ('-c', '--config-file', '--rootdir'):
            value = next(args, None)
            if value is None:
                raise ValueError('Pytest configuration option needs a value')
            if arg != '--rootdir':
                config = value
        elif arg.startswith(('--config-file=', '--rootdir=')):
            if arg.startswith('--config-file='):
                config = arg.split('=', 1)[1]
        elif arg.startswith('-c') and len(arg) > 2:
            config = arg[2:]
        else:
            clean.append(arg)
    return clean, config


def prepare_pytest(command, run_dir, job):
    boundary = _pytest(command)
    if boundary is None:
        return command
    prefix = command[:boundary]
    clean, config = _options(command[boundary:])
    pinned = {row['path'] for row in json.loads(job['definition_json'])}
    if config is not None:
        path = Path(config)
        if path.is_absolute():
            try:
                config = path.relative_to(run_dir).as_posix()
            except ValueError as error:
                raise ValueError('Pytest config must be in the pinned definition') from error
        else:
            config = path.as_posix()
        if config not in pinned:
            raise ValueError('Pytest config must be in the pinned definition')
    else:
        config = next((name for name in _CONFIGS if name in pinned), None)
    if config is None:
        config = '.slm-pytest.ini'
        with private_file(Path(run_dir) / config) as stream:
            stream.write(b'[pytest]\n')
        with temporary() as copy:
            manifest, _ = copy_inputs(run_dir, [config], copy)
        job['_generated_config_manifest'] = manifest
    position = clean.index('--') if '--' in clean else len(clean)
    options = ['-c', str(Path(run_dir) / config), '--rootdir', str(run_dir)]
    return prefix + clean[:position] + options + clean[position:]


def validate_pytest_configuration(job, run_dir):
    expected = job.get('_generated_config_manifest')
    if expected is not None:
        names = [row['path'] for row in json.loads(expected)]
        with temporary() as copy:
            actual, _ = copy_inputs(run_dir, names, copy)
        if actual != expected:
            raise ValueError('Check modified its generated pytest configuration')
