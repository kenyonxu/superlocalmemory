# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Private storage and portable refusal for unavailable no-follow primitives."""

import os
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path


def platform_reason():
    if (os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW')
            or not hasattr(os, 'O_DIRECTORY')):
        return ('Hermetic registered checks are unsupported on this platform: '
                'secure no-follow directory handles are required (including on Windows)')
    return None


@contextmanager
def directory_handle(path, *, create=False):
    """Hold every ancestor, refusing links before opening or creating children."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Private storage needs a no-follow path without traversal')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        try:
            fd = os.open(path.anchor, flags)
            stack.callback(os.close, fd)
            for part in path.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
        except OSError as error:
            raise ValueError(
                'Private check storage requires no-follow directories; symlinks refused') from error
        yield fd


def private_dir(path):
    path = Path(path).absolute()
    with directory_handle(path, create=True) as fd:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError('Private check storage must be a directory')
        os.fchmod(fd, 0o700)
    return path


def private_file(path):
    path = Path(path)
    with directory_handle(path.parent) as directory:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
    return os.fdopen(fd, 'wb')


@contextmanager
def temporary(parent=None):
    if parent is not None:
        parent = private_dir(parent)
    else:
        parent = Path(tempfile.gettempdir()).resolve()
        with directory_handle(parent):
            pass
    with tempfile.TemporaryDirectory(prefix='slm-check-', dir=parent) as name:
        path = private_dir(name)
        yield path


def execution_environment(cwd):
    root = private_dir(Path(cwd) / '.slm-runtime')
    paths = {name: str(private_dir(root / directory)) for name, directory in (
        ('HOME', 'home'), ('TMPDIR', 'tmp'), ('XDG_CACHE_HOME', 'cache'),
        ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
    )}
    paths.update(PATH=os.defpath, TMP=paths['TMPDIR'], TEMP=paths['TMPDIR'],
                 USERPROFILE=paths['HOME'], PYTHONNOUSERSITE='1',
                 PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    return paths


@contextmanager
def run_workspace(job):
    parent = Path(tempfile.gettempdir()).resolve()
    forbidden = (Path(job['cwd']), Path(job['store_path']).parent)
    if any(parent.is_relative_to(root) for root in forbidden):
        raise ValueError('Check temporary directory must be outside the project and data root')
    with directory_handle(parent):
        pass
    with tempfile.TemporaryDirectory(prefix='slm-check-', dir=parent) as name:
        yield private_dir(name)
