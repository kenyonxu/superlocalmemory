# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""One owned idle worker for automatic memory health, including enrichment."""

from __future__ import annotations

import contextlib
import logging
import threading

from superlocalmemory.core.enrichment import EnrichmentRunner
from superlocalmemory.core.recall_gate import background_work, in_flight

logger = logging.getLogger(__name__)


class _OnDeviceKinds:
    """The user's kind settings, minus any backend that sends text off the device.

    Turning kinds off still turns them off. A Jev choice (memory text leaves the
    machine, even with consent) becomes the rules here: background improvement
    only ever uses the rules, the local Laya judge or a loopback Ollama.
    """

    def __init__(self, live):
        self._live = live

    def __getattr__(self, name):
        value = getattr(self._live, name)
        if name == 'backend' and isinstance(value, str) and value.strip().lower() == 'jev':
            return 'rules'
        if name == 'jev_consent':
            return False
        return value


def local_kind_classifier(engine):
    """A kind classifier for idle re-labelling that never calls a hosted model."""
    from superlocalmemory.core.memory_kind_wiring import (
        LiveKindConfig,
        engine_mode,
        live_judge,
        llm_available,
    )
    from superlocalmemory.encoding.memory_kind_classifier import KindClassifier

    # The LLM branch hands off to LocalDescriptionKinds, which asks only an
    # Ollama on a loopback address and otherwise keeps the rules' answer.
    return KindClassifier(config=_OnDeviceKinds(LiveKindConfig(engine)),
                          mode=engine_mode(engine),
                          judge_supplier=lambda: live_judge(engine),
                          llm_available=llm_available(engine),
                          llm_supplier=lambda: getattr(engine, '_llm', None))


class MemoryHealthService:
    name = 'memory-health'

    def __init__(self, state, *, idle):
        self.state = state
        self.idle = idle
        self._stop = threading.Event()
        self._thread = None
        self._detail = ''

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='slm-memory-health', daemon=True)
        self._thread.start()

    def stop(self, timeout_s=10.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
        return self._thread is None or not self._thread.is_alive()

    def health(self):
        alive = self._thread is not None and self._thread.is_alive()
        return {'state': 'running' if alive else 'stopped', 'detail': self._detail}

    def can_work(self):
        runtime = getattr(self.state, 'profile_runtime', None)
        paused = runtime is not None and (
            runtime.background_paused or getattr(runtime, 'transitioning', False))
        foreground = (getattr(runtime, '_active_operations', 0)
                      > getattr(runtime, '_background_active', 0))
        return not (self._stop.is_set() or paused or foreground or in_flight()) and self.idle()

    def tick(self):
        if not self.can_work():
            return False
        engine = getattr(self.state, 'engine', None)
        if engine is None or getattr(engine, '_db', None) is None:
            return False
        runtime = getattr(self.state, 'profile_runtime', None)
        lease = (runtime.operation_nowait()
                 if runtime and hasattr(runtime, 'operation_nowait')
                 else contextlib.nullcontext(True))
        try:
            from superlocalmemory.core.ram_lock import ram_reservation
            with lease as admitted:
                if admitted is None or not self.can_work():
                    return False
                with ram_reservation('memory-health', timeout_s=0, required_mb=64):
                    with background_work(preempt_requested=lambda: not self.can_work()):
                        self._improve(engine)
            self._detail = ''
            return True
        except Exception as exc:
            self._detail = type(exc).__name__
            logger.debug('Memory health is waiting (%s)', self._detail)
            return False

    def _improve(self, engine):
        runner = EnrichmentRunner(engine._db, idle=self.can_work,
                                  classifier=local_kind_classifier(engine))
        runner.run_batch(limit=25)
        if self.can_work():
            from superlocalmemory.core.health_repairs import run_safe_repairs
            run_safe_repairs(engine, idle=self.can_work)

    def _loop(self):
        while not self._stop.wait(5.0):
            self.tick()


def start_memory_health(application, registry, idle):
    service = MemoryHealthService(application.state, idle=idle)
    application.state.memory_health_service = service
    registry.register(service)
    registry.start(service.name)
    return service
