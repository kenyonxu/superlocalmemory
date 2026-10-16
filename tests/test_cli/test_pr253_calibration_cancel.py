"""PR-253 #6: calibration-cancel is distinct from embedding reindex cancel."""
import argparse

from superlocalmemory.cli import calibration_cmd, embedder_cmd


def test_calibration_cancel_parser_and_dispatch_use_calibration_endpoint(monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    embedder_cmd.register_embedder_parser(parser.add_subparsers())
    args = parser.parse_args(["embedder", "calibration-cancel", "--json"])
    calls = []

    def request(method, path, body, **kwargs):
        calls.append((method, path, body))
        return {"state": "cancelling"}

    monkeypatch.setattr(calibration_cmd, "daemon_request", request)
    embedder_cmd.cmd_embedder(args)
    assert calls == [("POST", "/api/v3/answer-check/calibration/cancel", {})]
    assert "cancelling" in capsys.readouterr().out
