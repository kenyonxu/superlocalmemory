"""Abandon queued callbacks without releasing a running worker's admission."""

import threading


class RetainedDispatch:
    def __init__(self, lease, hold, fn):
        self._lease, self._hold, self._fn = lease, hold, fn
        self._lock = threading.Lock()
        self._state = "queued"

    def run(self):
        with self._lock:
            if self._state != "queued":
                return None
            self._state = "running"
        try:
            return self._hold.run(self._fn)
        finally:
            with self._lock:
                self._state = "done"
            self._lease.release()

    def abandon(self):
        with self._lock:
            if self._state != "queued":
                return
            self._state = "abandoned"
        self._lease.release()
