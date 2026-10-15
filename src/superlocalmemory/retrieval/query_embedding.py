# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Bound every foreground query embedding, including warm queues and reloads.

An unfinished query continues off the caller's clock and populates the cache.
Cold/reloading models report warming; an already-warm slow worker reports
 timeout. Background work remains inline to retain its priority context.
"""

from __future__ import annotations

import concurrent.futures
import inspect
import logging
import threading
from typing import Any, Callable

from superlocalmemory.retrieval import channel_status as chstat

logger = logging.getLogger(__name__)

__all__ = ["QueryEmbedder", "embed_as_query"]


def embed_as_query(embedder: Any, text: str) -> list[float] | None:
    """Use the query prompt when available, otherwise the provider's plain embed.

    Models that take different prompts for questions and memories (the managed text
    model) define ``embed_query``; every other embedder is called exactly as before.
    Looked up statically so a duck-typed stand-in does not pretend to have it.
    """
    if inspect.getattr_static(embedder, "embed_query", None) is not None:
        return embedder.embed_query(text)
    return embedder.embed(text)


class QueryEmbedder:
    """Bounded, single-flight, cached query embedding for one retrieval engine.

    ``embed`` returns ``(vector, status)``. ``status`` is ``None`` when the
    embedder answered (the vector may still be ``None`` if the embedder itself
    returned nothing — that stays the caller's ``no_embedding`` case), and
    ``WARMING`` when a not-yet-ready model made the recall stop waiting.
    """

    def __init__(
        self,
        embedder: Callable[[], Any],
        *,
        cache_max_size: int = 512,
    ) -> None:
        # A provider, not the object: the owning engine's embedder can be
        # swapped after construction and every call must see the current one.
        self._provider = embedder
        self._cache: dict[str, list[float]] = {}
        self._cache_max_size = cache_max_size
        self._lock = threading.Lock()
        self._inflight: dict[tuple[int, str], concurrent.futures.Future] = {}
        self._waiters: dict[concurrent.futures.Future, int] = {}
        # Created on first use: an engine with no embedder (Mode A without
        # vectors) or one only ever used from background work owns no threads.
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._closed = False
        self._owner = None
        self._generation = 0

    @property
    def cache(self) -> dict[str, list[float]]:
        return self._cache

    def _current_provider(self):
        embedder = self._provider()
        with self._lock:
            if embedder is not self._owner:
                self._owner = embedder
                self._generation += 1
                self._cache.clear()
                # Keep old running/queued work counted until the executor
                # actually drains it; a generation swap must not reset capacity.
            return embedder, self._generation

    def _remember(self, query: str, vector: list[float] | None, generation: int) -> None:
        if vector is None:
            return
        with self._lock:
            if self._closed or generation != self._generation:
                return
            if query not in self._cache and len(self._cache) >= self._cache_max_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[query] = vector

    def _compute(self, query: str, embedder: Any, generation: int) -> list[float] | None:
        with self._lock:
            pending = self._inflight.get((generation, query))
            if (self._closed or generation != self._generation
                    or (pending is not None and self._waiters.get(pending, 0) == 0)):
                return None  # abandoned queued work never invokes the provider
        vector = embed_as_query(embedder, query)
        self._remember(query, vector, generation)
        return vector

    def _future_for(self, query: str, embedder: Any,
                    generation: int) -> concurrent.futures.Future | None:
        with self._lock:
            if self._closed:
                raise RuntimeError("query embedder is closed")
            key = (generation, query)
            fut = self._inflight.get(key)
            if fut is not None:
                self._waiters[fut] = self._waiters.get(fut, 0) + 1
                return fut
            if len(self._inflight) >= 2:
                return None  # two worker slots, across all provider generations
            if self._executor is None:
                if self._closed:
                    raise RuntimeError("query embedder is closed")
                # Two workers: one may be parked behind a cold model load
                # while a second question still gets its turn once it is up.
                self._executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="slm-query-embed",
                )
            fut = self._executor.submit(self._compute, query, embedder, generation)
            self._inflight[generation, query] = fut
            self._waiters[fut] = 1

        def _done(_f, q=(generation, query)) -> None:
            with self._lock:
                if self._inflight.get(q) is _f:
                    del self._inflight[q]
                self._waiters.pop(_f, None)

        # Outside the lock: a future that is already done runs the callback
        # inline, and the callback takes the same (non-reentrant) lock.
        fut.add_done_callback(_done)
        return fut

    def _current_result(self, vector, generation):
        self._current_provider()
        with self._lock:
            return (vector, None) if generation == self._generation else (None, chstat.WARMING)

    def _not_ready(self) -> bool:
        """A cold or reloading provider explicitly reports not warm."""
        embedder = self._provider()
        return getattr(embedder, "is_warm", None) is False

    def embed(self, query: str, wait_seconds: float) -> tuple[list[float] | None, str | None]:
        """See ``_embed``. Afterwards the recall on this thread will not ask the
        embedder again (the vector is cached for the question), so background
        embeds may use it while the recall runs on (``recall_gate``)."""
        try:
            return self._embed(query, wait_seconds)
        finally:
            from superlocalmemory.core.recall_gate import mark_query_embedded

            mark_query_embedded()

    def _embed(self, query: str, wait_seconds: float) -> tuple[list[float] | None, str | None]:
        """Embed ``query``; ``wait_seconds`` bounds every foreground wait, including reloads
        and warm workers queued behind ingestion.

        Raises whatever the embedder raised, exactly as the direct call did.
        """
        embedder, generation = self._current_provider()
        if embedder is None:
            return None, None
        from superlocalmemory.retrieval.query_lexicon import requires_lexical

        if requires_lexical(embedder, query):
            return None, chstat.EMPTY  # lexical retrieval still runs; not a model outage
        with self._lock:
            cached = self._cache.get(query) if generation == self._generation else None
        if cached is not None:
            return self._current_result(cached, generation)
        if getattr(embedder, "needs_service", False) is True:
            return None, chstat.NEEDS_SERVICE  # no point waiting: nothing is loading
        from superlocalmemory.core.recall_gate import is_background_work

        if is_background_work():
            # 4.1.19 behaviour, inline on the caller's thread (background
            # callers keep their thread-local priority marker). An embed for
            # this question started while the model was loading is joined,
            # not repeated.
            with self._lock:
                pending = self._inflight.get((generation, query))
            if pending is not None:
                return self._current_result(pending.result(), generation)
            vector = self._compute(query, embedder, generation)
            return self._current_result(vector, generation)
        return self._foreground(query, embedder, generation, wait_seconds)

    def _foreground(self, query, embedder, generation, wait_seconds):
        warming = self._not_ready()
        fut = self._future_for(query, embedder, generation)
        if fut is None:
            return None, chstat.WARMING if warming else chstat.TIMEOUT
        try:
            vector = fut.result(timeout=max(0.0, wait_seconds))
            return self._current_result(vector, generation)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "Embedding model not ready within %.1fs; this recall runs "
                "without the channels that need it and is marked incomplete",
                wait_seconds,
            )
            return None, chstat.WARMING if warming or self._not_ready() else chstat.TIMEOUT
        finally:
            with self._lock:
                if fut in self._waiters:
                    self._waiters[fut] -= 1

    def worker_threads(self) -> list[threading.Thread]:
        """This embedder's pool threads (none until a cold embed created it)."""
        from superlocalmemory.core.thread_join import executor_threads

        with self._lock:
            return executor_threads(self._executor)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
