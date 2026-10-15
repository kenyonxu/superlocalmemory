# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Hash complete checker output. Receipts never carry the output itself.

Redacting secrets out of arbitrary text cannot be complete (any encoding of a
key slips past a pattern list), so receipts keep only hashes and the exit status.
To read a check's output, run the same pinned command yourself.
"""

import base64
import hashlib


def _bytes(value):
    return value if isinstance(value, bytes) else value.encode('utf-8', errors='replace')


def _secret_forms(secret_material):
    values = []
    for key in secret_material:
        values.extend((key, key.hex().encode(), repr(key).encode()))
        for encoded in (base64.b64encode(key), base64.urlsafe_b64encode(key)):
            values.extend((encoded, encoded.rstrip(b'=')))
    return tuple(value for value in values if value)


def output_evidence(stdout, stderr, secret_material=()):
    out, err = _bytes(stdout), _bytes(stderr)
    forms = _secret_forms(secret_material)
    full = len(out).to_bytes(8, 'big') + out + len(err).to_bytes(8, 'big') + err
    return dict(
        stdout_sha256=hashlib.sha256(out).hexdigest(),
        stderr_sha256=hashlib.sha256(err).hexdigest(),
        output_sha256=hashlib.sha256(full).hexdigest(),
        key_exposed=any(value in out or value in err for value in forms),
    )
