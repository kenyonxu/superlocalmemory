# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Bounded on-device kind suggestions; memory text never reaches a hosted URL."""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from urllib.parse import urlsplit

from superlocalmemory.storage.memory_kinds import KindAssignment, KindSource, parse_kind

logger = logging.getLogger(__name__)
MODEL_BUDGET_SECONDS = 0.8
SHOW_TIMEOUT_SECONDS = 0.5
VERDICT_TTL_SECONDS = 600.0
#: (host, model) -> (is_on_device, checked_at). A model's location does not
#: change between idle ticks; re-checked after the TTL.
_VERDICTS: dict[tuple[str, str], tuple[bool, float]] = {}
_REMOTE_FIELDS = ('remote_host', 'remote_model')


def loopback_host(host: str) -> bool:
    parsed = urlsplit(host)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        return False
    if parsed.hostname == 'localhost':
        return True
    try:
        return ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        return False


def cloud_tag(model: str) -> bool:
    """Ollama's hosted models are named like ``gpt-oss:120b-cloud`` or ``kimi-k2:cloud``."""
    name = (model or '').strip().lower()
    return name.endswith('cloud') or ':cloud' in name or '-cloud' in name


def _show(host, model, timeout):
    """Ollama's model metadata. Carries no memory text, so it runs in-process."""
    from superlocalmemory.core.outbound_http import post_json

    response = post_json(host.rstrip('/') + '/api/show', {'model': model}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _remote(data) -> bool:
    if not isinstance(data, dict):
        return True
    details = data.get('details') if isinstance(data.get('details'), dict) else {}
    return any(data.get(f) or details.get(f) for f in _REMOTE_FIELDS)


def on_device_model(host, model, *, show=_show) -> bool:
    """True only for a model this machine runs itself. Uncertain means no (rules).

    A loopback URL is not enough: Ollama serves hosted "cloud" models through
    the same local endpoint, so memory text would leave the machine.
    """
    if not model or cloud_tag(model) or not loopback_host(host):
        return False
    key = (host, model)
    cached = _VERDICTS.get(key)
    if cached and time.monotonic() - cached[1] < VERDICT_TTL_SECONDS:
        return cached[0]
    try:
        verdict = not _remote(show(host, model, SHOW_TIMEOUT_SECONDS))
    except Exception as exc:  # unreachable, old server, unknown model: refuse
        logger.debug('Local kind model not verified (%s)', type(exc).__name__)
        verdict = False
    _VERDICTS[key] = (verdict, time.monotonic())
    return verdict


def _ollama_request(host, model, content, *, timeout):
    from superlocalmemory.core.background_process import run_in_child

    return run_in_child(_post, host, model, content, timeout, timeout=timeout)


def _post(host, model, content, timeout):
    from superlocalmemory.core.outbound_http import post_json
    from superlocalmemory.storage.memory_kinds import MemoryKind

    if not loopback_host(host) or cloud_tag(model):
        raise ValueError('kind classification requires an on-device model')
    choices = ', '.join(k.value for k in MemoryKind)
    payload = {'model': model, 'stream': False, 'format': 'json', 'think': False,
               'keep_alive': '5m', 'options': {'num_predict': 30, 'num_ctx': 2048},
               'messages': [{'role': 'system', 'content':
                             'Classify the memory. Return only {"kind": "choice"}. '
                             'Choices: ' + choices},
                            {'role': 'user', 'content': content[:4000]}]}
    # Through the outbound gate: the memory text is screened like any other exit.
    response = post_json(host.rstrip('/') + '/api/chat', payload, timeout=timeout)
    response.raise_for_status()
    return json.loads(response.json()['message']['content'])


class LocalDescriptionKinds:
    """Reuse existing suggestions; fill rules-only kinds from local Ollama."""

    def __init__(self, *, delegate, llm_supplier, request=None, show=None):
        self.delegate = delegate
        self.llm_supplier = llm_supplier
        self.request = request or _ollama_request
        self.show = show or _show

    def suggest(self, facts, *, caller_kind):
        from superlocalmemory.encoding.memory_kind_classifier import _rules_for

        if caller_kind is not None:
            return [KindAssignment(caller_kind, KindSource.CALLER, None, 'caller')] * len(facts)
        out = (self.delegate.suggest(facts, caller_kind=None) if self.delegate
               else [_rules_for(f) for f in facts])
        llm = self.llm_supplier()
        if getattr(llm, 'provider', None) != 'ollama':
            return out
        host = getattr(llm, '_ollama_host', '')
        if not on_device_model(host, getattr(llm, 'model', ''), show=self.show):
            return out
        deadline = time.monotonic() + MODEL_BUDGET_SECONDS
        for i, fact in enumerate(facts[:8]):
            if out[i] is None or out[i].source.value != 'rules':
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = self.request(host, llm.model, fact.content, timeout=remaining)
                kind = parse_kind(raw.get('kind'))
                if kind is not None:
                    from superlocalmemory.encoding.memory_kind_classifier import decide
                    from superlocalmemory.encoding.memory_kind_rules import cue_kind

                    model = KindAssignment(kind, KindSource.MODEL_LLM, None, 'local-kind-v1')
                    out[i] = decide(model, out[i], cue_kind(fact.content)) or out[i]
            except Exception as exc:  # best-effort description must never fail a save
                logger.debug('Local memory description deferred (%s)', type(exc).__name__)
                break
        return out
