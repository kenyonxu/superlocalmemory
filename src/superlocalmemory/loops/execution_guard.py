# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Authenticate registered measurements; live-tree metadata is not evidence."""

import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path

from superlocalmemory.loops.private_workspace import directory_handle, private_dir, private_file


def admission(job):
    """Public identity descriptor alone cannot mint an admitted receipt."""
    return {'job_id': job['job_id'], 'revision_digest': job['revision_digest'],
            'definition_digest': job['definition_digest'], 'validation': 'hermetic-copy-v1'}


def _read_key(path):
    with directory_handle(path.parent) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Admission key must be an owner-only 0600 regular file')
        key = stream.read(33)
    if len(key) != 32:
        raise ValueError('Invalid admission key')
    return key


def _key(store, *, create=False):
    path = private_dir(Path(store).parent / 'loop-admission') / 'admission.key'
    legacy = Path(store) / 'admission.key'
    if create:
        try:
            previous = _read_key(legacy)
        except FileNotFoundError:
            previous = None
        try:
            stream = private_file(path)
        except FileExistsError:
            pass
        else:
            with stream:
                stream.write(previous if previous is not None else secrets.token_bytes(32))
        key = _read_key(path)
        if previous is not None:
            if not hmac.compare_digest(key, previous):
                raise ValueError('Conflicting admission key locations; operator repair required')
            with directory_handle(store) as directory:
                try:
                    os.unlink(legacy.name, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return key
    return _read_key(path)


def _payload(receipt):
    value = dict(receipt)
    measurement = json.loads(value['evidence'])
    measurement['admission'].pop('signature', None)
    value['evidence'] = json.dumps(measurement, sort_keys=True)
    value.pop('evidence_digest', None)
    return json.dumps(value, sort_keys=True).encode()


def _sign_receipt(job, receipt):
    measurement = json.loads(receipt['evidence'])
    measurement['admission'] = admission(job)
    receipt['evidence'] = json.dumps(measurement, sort_keys=True)
    measurement['admission']['signature'] = hmac.new(
        _key(job['store_path']), _payload(receipt), hashlib.sha256).hexdigest()
    receipt['evidence'] = json.dumps(measurement, sort_keys=True)
    receipt['evidence_digest'] = hashlib.sha256(receipt['evidence'].encode()).hexdigest()


def verify_admission(job, receipt):
    try:
        proof = json.loads(receipt['evidence'])['admission']
        signature = proof.pop('signature')
        expected = admission(job)
        expected['revision_digest'] = proof.get('revision_digest')
        return (proof == expected and isinstance(signature, str)
                and hmac.compare_digest(signature, hmac.new(
                    _key(job['store_path']), _payload(receipt), hashlib.sha256).hexdigest()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
