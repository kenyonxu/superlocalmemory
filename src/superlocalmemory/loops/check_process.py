# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Execute a check, sweep observable survivors, and retain measured output."""

import os
import secrets
import signal
import subprocess
import time

import psutil

from superlocalmemory.loops.private_workspace import execution_environment
from superlocalmemory.loops.process_sweep import CheckProcesses, sweep, user_processes


class ExecutionResult(tuple):
    """Keep the text-triplet API while carrying exact bytes for receipt hashes."""

    def __new__(cls, exit_code, stdout, stderr, reason=None):
        result = super().__new__(cls, (exit_code, stdout.decode('utf-8', errors='replace'),
                                      stderr.decode('utf-8', errors='replace')))
        result.stdout_bytes, result.stderr_bytes, result.reason = stdout, stderr, reason
        return result


def _collect_until_exit(process, timeout, owner):
    deadline, stdout, stderr = time.monotonic() + timeout, b'', b''
    while True:
        try:
            owner.owned(user_processes())
        except ValueError as error:
            return -1, stdout, stderr, str(error)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return -1, stdout, stderr, 'Independent check timed out'
        try:
            stdout, stderr = process.communicate(timeout=min(remaining, 0.05))
            return process.returncode, stdout, stderr, None
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.output or stdout, error.stderr or stderr
            if process.poll() is not None:
                return process.returncode, stdout, stderr, None


def execute(command, cwd, timeout):
    try:
        before = user_processes()
    except ValueError as error:
        return ExecutionResult(-1, b'', b'', str(error))
    began = time.time()
    token = secrets.token_hex(32)
    environment = execution_environment(cwd)
    environment['SLM_LOOP_RUN_TOKEN'] = token
    try:
        process = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.DEVNULL, shell=False,
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        return ExecutionResult(-1, b'', b'', 'Independent check did not complete')
    reason = None
    try:
        # Use the OS creation timestamp, including its native clock resolution.
        # Prep-time processes are outside the check; forked children share this clock.
        began = psutil.Process(process.pid).create_time()
    except psutil.Error:
        reason = 'Cannot validate the checker process start time'
    owner = CheckProcesses(process.pid, began, token, before)
    try:
        exit_code, stdout, stderr, timed_out = _collect_until_exit(process, timeout, owner)
        reason = reason or timed_out
    finally:
        try:
            # Keep observed descendants even if group termination reparents them.
            owner.owned(user_processes())
        except ValueError as error:
            reason = reason or str(error)
        finally:
            try:
                os.killpg(owner.group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    try:
        escaped = sweep(owner)
    except ValueError as error:
        escaped = str(error)
    reason = escaped or reason
    if reason and exit_code == 0:
        exit_code = -1
    # Drain after the sweep has terminated escaped pipe holders as well.
    try:
        stdout, stderr = process.communicate(timeout=0.2)
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.output or b'', error.stderr or b''
    finally:
        process.stdout.close()
        process.stderr.close()
    return ExecutionResult(exit_code, stdout, stderr, reason)
