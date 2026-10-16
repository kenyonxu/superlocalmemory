"""An Ollama "cloud" model is a hosted model behind a loopback URL (review M4).

Kind suggestions must never send memory text to one: refused by tag, refused
when /api/show reports a remote model, and refused when that cannot be checked.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from superlocalmemory.storage.models import AtomicFact

SHOWS = {
    'llama3.2:latest': {'details': {'family': 'llama'}, 'model_info': {}},
    'gpt-oss:120b': {'remote_host': 'https://ollama.com:443', 'remote_model': 'gpt-oss:120b'},
}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'] or 0)) or b'{}')
        self.server.seen.append((self.path, body.get('model') or body.get('name')))
        if self.path == '/api/show' and body.get('model') in SHOWS:
            payload, code = SHOWS[body['model']], 200
        else:
            payload, code = {'error': 'not found'}, 404
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def ollama():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _suggest(server, model):
    from superlocalmemory.encoding import local_description_kinds as kinds
    kinds._VERDICTS.clear()
    llm = SimpleNamespace(provider='ollama', model=model,
                          _ollama_host=f'http://127.0.0.1:{server.server_address[1]}')
    request = Mock(return_value={'kind': 'fact'})
    classifier = kinds.LocalDescriptionKinds(delegate=None, llm_supplier=lambda: llm,
                                             request=request)
    result = classifier.suggest([AtomicFact(content='Zephyr uses PostgreSQL.')],
                                caller_kind=None)
    return result[0], request


@pytest.mark.parametrize('model', ['gpt-oss:120b-cloud', 'qwen3-coder:480b-cloud',
                                   'deepseek-v3.1:671b:cloud', 'kimi-k2:cloud', 'glm-cloud'])
def test_cloud_tags_are_refused_without_asking(ollama, model):
    assignment, request = _suggest(ollama, model)
    assert assignment.source.value == 'rules'
    request.assert_not_called()
    assert ollama.seen == []


def test_a_model_the_server_reports_as_remote_is_refused(ollama):
    assignment, request = _suggest(ollama, 'gpt-oss:120b')
    assert assignment.source.value == 'rules'
    request.assert_not_called()
    assert ollama.seen == [('/api/show', 'gpt-oss:120b')]


def test_an_unverifiable_model_is_refused(ollama):
    assignment, request = _suggest(ollama, 'mystery:latest')
    assert assignment.source.value == 'rules'
    request.assert_not_called()


def test_a_verified_local_model_is_used(ollama):
    assignment, request = _suggest(ollama, 'llama3.2:latest')
    assert assignment.source.value == 'model:llm'
    request.assert_called_once()
