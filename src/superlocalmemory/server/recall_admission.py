"""Bound fresh admission and retain admitted requests without blocking drain."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar

from superlocalmemory.server.profile_lease import acquire_request_lease, current_request_lease

_RECALL_LEASE = ContextVar("slm_recall_lease", default=None)


def current_recall_lease():
    return _RECALL_LEASE.get()


class RecallAdmission:
    def __init__(self, lease):
        self.lease = lease
        self._dispatches = []

    def fork(self):
        return self.lease.fork()

    def track(self, dispatch):
        self._dispatches.append(dispatch)

    def close(self):
        for dispatch in self._dispatches:
            dispatch.abandon()
        self._dispatches.clear()
        self.lease.release()


async def _acquire(runtime, deadline):
    parent = current_request_lease()
    if parent is not None and parent.runtime is runtime:
        return parent.fork()
    return await acquire_request_lease(runtime, deadline)


@asynccontextmanager
async def foreground_admission(runtime, deadline):
    # Minimal test/adapter runtimes predating transferable admission have no
    # acquire_lease; the authoritative daemon runtime always implements it.
    if not callable(getattr(runtime, "acquire_lease", None)):
        yield True
        return
    try:
        lease = await _acquire(runtime, deadline)
    except asyncio.TimeoutError:
        yield False
        return
    admission = RecallAdmission(lease)
    token = _RECALL_LEASE.set(admission)
    try:
        yield True
    finally:
        _RECALL_LEASE.reset(token)
        admission.close()
