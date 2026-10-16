# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Fail closed on observable checker escape, including reparented processes."""

import os
import time

import psutil


def user_processes():
    """Read identities without launching a process that our sweep would kill."""
    found = {}
    for process in psutil.process_iter():
        try:
            if process.uids().real != os.getuid():
                continue
            if process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                continue
            identity = (process.pid, process.create_time())
            found[identity] = (process, process.name())
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            raise ValueError('Cannot validate the user process table') from error
    return found


class CheckProcesses:
    """Remember ownership independently of a process's later parent or session."""

    def __init__(self, pid, began, token, before):
        self.began, self.token, self.before = began, token, before
        # Popen(start_new_session=True) creates both IDs equal to its PID.
        self.session_id = self.group_id = pid
        self.known = {(pid, began)}

    def owned(self, current):
        """Attribute only new descendants, session members, or token holders."""
        candidates = {identity: value for identity, value in current.items()
                      if identity not in self.before and identity[1] >= self.began
                      and identity[0] != os.getpid()}
        identities = {identity[0]: identity for identity in current}
        parents = {}
        for identity, (process, _) in candidates.items():
            try:
                parents[identity] = identities.get(process.ppid())
                if (os.getsid(process.pid) == self.session_id
                        or os.getpgid(process.pid) == self.group_id):
                    self.known.add(identity)
            except (psutil.Error, OSError):
                # A disappearing process or unreadable IDs cannot prove ownership.
                pass
            if identity not in self.known:
                try:
                    if process.environ().get('SLM_LOOP_RUN_TOKEN') == self.token:
                        self.known.add(identity)
                except (psutil.Error, OSError):
                    # Environment visibility is best-effort, never global attribution.
                    pass
        # Follow every observed parent chain, irrespective of iteration order.
        while True:
            descendants = {identity for identity, parent in parents.items()
                           if parent in self.known and parent[1] <= identity[1]}
            if descendants <= self.known:
                break
            self.known.update(descendants)
        return [value for identity, value in candidates.items() if identity in self.known]


def sweep(owner):
    """Record every observed survivor; termination cannot turn it into a PASS."""
    reason = None
    deadline = time.monotonic() + 1
    while True:
        remaining = owner.owned(user_processes())
        if not remaining:
            return reason
        process, name = remaining[0]
        reason = reason or (
            f'a process started by the check is still running: {name} ({process.pid})')
        for process, _ in remaining:
            try:
                # psutil checks creation time before signalling to avoid PID reuse.
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                # The observed process already forbids PASS, even if it resists kill.
                pass
        if time.monotonic() >= deadline:
            return reason
        time.sleep(0.01)
