#!/usr/bin/env python3
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Repeat the synthetic extraction run; no SLM daemon or user store is involved.

Use PYTHONPATH to select the candidate or an isolated baseline checkout.
Artifacts must be under TMPDIR. Model inference is serial, uses installed
Ollama models only, and never changes SLM/Ollama configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

from superlocalmemory.core.config import EncodingConfig
from superlocalmemory.encoding.fact_extractor import FactExtractor
from superlocalmemory.storage.models import Mode


class LocalModel:
    def __init__(self, model: str):
        self.model, self.seconds, self.calls = model, 0.0, 0

    def is_available(self):
        return True

    def generate(self, prompt, system="", temperature=None, max_tokens=None, think=None):
        start = time.perf_counter()
        try:
            response = httpx.post(
                "http://localhost:11434/api/chat",
                timeout=180,
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": temperature or 0, "num_predict": max_tokens or 1024},
                },
            )
            response.raise_for_status()
            return response.json()["message"]["content"]
        finally:
            self.seconds += time.perf_counter() - start
            self.calls += 1


def run(args, dataset, output):
    with output.open("w") as stream:
        for mode in (Mode.A, Mode.B):
            llm = LocalModel(args.model) if mode is Mode.B else None
            extractor = FactExtractor(EncodingConfig(), llm=llm, mode=mode)
            for note, text in enumerate(dataset["notes"], 1):
                start = time.perf_counter()
                before = llm.seconds if llm else 0
                facts = extractor.extract_facts([text], "wp5", dataset["session_date"])
                stream.write(
                    json.dumps(
                        {
                            "mode": mode.value,
                            "note": note,
                            "source": text,
                            "facts": [f.content for f in facts],
                            "entities": [f.entities for f in facts],
                            "date_fields": [
                                {
                                    "referenced_date": f.referenced_date,
                                    "interval_start": f.interval_start,
                                    "interval_end": f.interval_end,
                                }
                                for f in facts
                            ],
                            "elapsed": time.perf_counter() - start,
                            "llm_seconds": (llm.seconds - before) if llm else 0,
                        }
                    )
                    + "\n"
                )
                stream.flush()
                print(f"{mode.value} note {note}: {len(facts)} facts", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="llama3.2-cpu")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    temp = Path(os.environ["TMPDIR"]).resolve()
    output = args.output.resolve()
    if not output.is_relative_to(temp):
        parser.error("output must be under TMPDIR")
    dataset = json.loads(
        (
            Path(__file__).resolve().parents[2] / "tests/fixtures/wp5-extraction/notes.json"
        ).read_text()
    )
    run(args, dataset, output)


if __name__ == "__main__":
    main()
