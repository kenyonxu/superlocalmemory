# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Operator-owned, content-addressed definitions frozen at registration."""

import hashlib
import json
import os
from pathlib import Path

from superlocalmemory.loops.artifact_revision import copy_inputs, selectors
from superlocalmemory.loops.private_workspace import private_dir, temporary


def normalized(command, cwd):
    """Freeze project aliases before execution can observe any later retargeting."""
    root = Path(cwd)
    executable = Path(command[0])
    if ((executable.is_absolute() and (executable.is_relative_to(root)
                                      or executable.resolve().is_relative_to(root)))
            or (not executable.is_absolute() and ('/' in command[0] or '\\' in command[0]))):
        raise ValueError('Check executable must be operator-owned outside the project')
    argv = []
    for arg in command:
        prefix, value = '', arg
        if arg.startswith('-') and '=' in arg:
            option, value = arg.split('=', 1)
            prefix = option + '='
        path = Path(value)
        if '..' in path.parts:
            raise ValueError('Check command paths must be confined without traversal')
        if path.is_absolute():
            resolved = path.resolve()
            if resolved.is_relative_to(root):
                value = str(resolved)
        argv.append(prefix + value)
    return argv


def relocated(command, cwd, destination):
    root = Path(cwd)
    argv = []
    for arg in command:
        prefix, value = ('', arg)
        if arg.startswith('-') and '=' in arg:
            option, value = arg.split('=', 1)
            prefix = option + '='
        path = Path(value)
        if '..' in path.parts:
            raise ValueError('Check command paths must be confined without traversal')
        if path.is_absolute() and path.is_relative_to(root):
            value = str(Path(destination) / path.relative_to(root))
        argv.append(prefix + value)
    return argv


def definition_digest(command, manifest, inputs):
    value = json.dumps({'command': command, 'files': json.loads(manifest),
                        'artifact_paths': inputs}, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def pin(command, cwd, check_paths, artifact_paths, store):
    directory = private_dir(Path(store) / 'definitions')
    with temporary(directory) as stage:
        manifest, _ = copy_inputs(cwd, check_paths, stage, allow_empty=True)
        normalized = relocated(command, cwd, '.')
        digest = definition_digest(normalized, manifest, artifact_paths)
        final = directory / digest
        if final.exists():
            with temporary() as verify:
                existing, _ = copy_inputs(final, [r['path'] for r in json.loads(manifest)],
                                          verify, allow_empty=True)
            if existing != manifest:
                raise ValueError('Pinned check definition has been modified')
        else:
            os.rename(stage, final)
    return manifest, digest


def materialize(job, destination):
    expected = job['definition_json']
    source = Path(job['store_path']) / 'definitions' / job['definition_digest']
    try:
        manifest, _ = copy_inputs(source, [r['path'] for r in json.loads(expected)],
                                  destination, allow_empty=True)
    except ValueError as error:
        raise ValueError('Pinned check definition is unavailable or modified') from error
    digest = definition_digest(relocated(json.loads(job['command_json']), job['cwd'], '.'),
                               manifest, json.loads(job['artifact_paths_json']))
    if manifest != expected or digest != job['definition_digest']:
        raise ValueError('Pinned check definition has been modified')


def prepare(db_job, command, cwd, check_paths, artifact_paths, store):
    inputs = selectors(cwd, artifact_paths)
    with temporary() as stage:
        artifacts, revision = copy_inputs(cwd, inputs, stage, allow_empty=True)
    definition, digest = pin(command, cwd, check_paths, inputs, store)
    if {r['path'] for r in json.loads(definition)} & {r['path'] for r in json.loads(artifacts)}:
        raise ValueError('Check definition and mutable artifact inputs must have distinct paths')
    db_job.update(artifact_paths_json=json.dumps(inputs), artifacts_json=artifacts,
                  revision_digest=revision, definition_json=definition,
                  definition_digest=digest, store_path=str(store))
