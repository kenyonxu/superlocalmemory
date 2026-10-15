# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""A revision describes bytes copied once into an owned directory, never stamps."""

import hashlib
import json
import os
import stat
from contextlib import ExitStack
from pathlib import Path

from superlocalmemory.loops.private_workspace import private_dir, private_file, temporary


def selectors(cwd, paths):
    root = Path(cwd)
    names = []
    for value in paths:
        path = Path(value)
        if path.is_absolute():
            try:
                path = path.relative_to(root)
            except ValueError as error:
                raise ValueError(
                    'Gate inputs must be inside the approved working directory') from error
        if '..' in path.parts or not path.parts or path.parts[0] == '.slm-runtime':
            raise ValueError('Gate inputs require confined, non-runtime relative paths')
        names.append(path.as_posix())
    return sorted(set(names))


def _selected(root, names):
    files = set()
    for name in names:
        found = list(root.glob(name))
        if not found:
            raise ValueError(f'No declared input matches {name}')
        for path in found:
            if path.is_symlink():
                raise ValueError('Gate inputs must be regular files; symlinks are refused')
            if path.is_dir():
                children = list(path.rglob('*'))
                if any(child.is_symlink() for child in children):
                    raise ValueError('Gate input trees cannot contain symlinks')
                files.update(child.relative_to(root).as_posix() for child in children
                             if not child.is_dir())
            else:
                files.add(path.relative_to(root).as_posix())
    return sorted(files)


def _open_regular(stack, root, name):
    """Anchor every ancestor with a no-follow handle; final open cannot block."""
    directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stack.callback(os.close, directory)
    for part in (*root.parts[1:], *Path(name).parts[:-1]):
        directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
        stack.callback(os.close, directory)
    fd = os.open(Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=directory)
    stack.callback(os.close, fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError('Gate inputs must be regular files; devices and FIFOs are refused')
    return stack.enter_context(os.fdopen(os.dup(fd), 'rb'))


def copy_inputs(cwd, paths, destination, *, allow_empty=False):
    """Hash exactly the bytes written from a single read of each live input."""
    root, target = Path(cwd), Path(destination)
    records = []
    try:
        for name in _selected(root, selectors(root, paths)):
            output = target / name
            private_dir(output.parent)
            digest = hashlib.sha256()
            with ExitStack() as stack:
                source = _open_regular(stack, root, name)
                sink = stack.enter_context(private_file(output))
                while chunk := source.read(1024 * 1024):
                    sink.write(chunk)
                    digest.update(chunk)
            records.append({'path': name, 'sha256': digest.hexdigest()})
    except OSError as error:
        raise ValueError('Gate inputs require regular files and no-follow directories') from error
    if not records and not allow_empty:
        raise ValueError('Register at least one checked artifact')
    encoded = json.dumps(records, sort_keys=True)
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


def snapshot(cwd, paths):
    """Use the same copy/hash operation for admission and execution."""
    with temporary() as destination:
        return copy_inputs(cwd, paths, destination, allow_empty=True)


def matches(job):
    _, digest = snapshot(job['cwd'], json.loads(job['artifact_paths_json']))
    return digest == job['revision_digest']
