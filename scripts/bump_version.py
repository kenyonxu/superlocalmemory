#!/usr/bin/env python3
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Set the release version in every place that declares it.

WHY THIS EXISTS
---------------
``tests/test_version_consistency.py`` knows all eleven places a version is
declared and fails when they disagree. Nothing UPDATED them, so every release
was a manual sweep — and 4.0.6 shipped with eight of them still reading 4.0.5:

    plugin-src/manifest.json           4.0.5
    plugin-src/requirements.txt        4.0.5
    package-lock.json (x2)             4.0.5
    plugin/.claude-plugin/plugin.json  4.0.5
    plugin/requirements.txt            4.0.5
    CITATION.cff                       4.0.5
    uv.lock                            4.0.5
    plugin-src/AGENTS.md               4.0.4

That is not cosmetic. ``requirements.txt`` installs the wrong release,
``plugin.json`` makes the editor plugin advertise the wrong version, and
CITATION.cff is the metadata academic citations resolve against.

USAGE
    python3 scripts/bump_version.py 4.0.7          # write
    python3 scripts/bump_version.py 4.0.7 --check  # report, change nothing

``--check`` is the CI-friendly mode: it exits non-zero when anything disagrees
with the target, without touching the tree.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _sub_json(rel: str, version: str, *, package_root: bool = False) -> tuple[str, str]:
    """Rewrite a JSON ``version`` field, preserving formatting where possible."""
    path = _ROOT / rel
    data = json.loads(path.read_text(encoding="utf-8"))
    if package_root:
        old = data["packages"][""]["version"]
        data["packages"][""]["version"] = version
    else:
        old = data["version"]
        data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return old, version


def _sub_regex(rel: str, pattern: str, replacement: str, version: str,
               reported: str | None = None) -> tuple[str, str]:
    """Rewrite the first regex match, returning (old, new) for reporting.

    ``reported`` overrides what gets printed as the new value. Needed for
    date-released, whose new value is a date, not the version — without it the
    log claimed ``2026-08-17 -> 4.0.7``, which reads like the date was replaced
    by a version string.
    """
    path = _ROOT / rel
    text = path.read_text(encoding="utf-8")
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        raise SystemExit(f"{rel}: pattern not found: {pattern}")
    old = m.group(1)
    path.write_text(
        re.sub(pattern, replacement.format(v=version), text, count=1, flags=re.MULTILINE),
        encoding="utf-8",
    )
    return old, (reported if reported is not None else version)


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _sub_all(rel: str, pattern: str, replacement: str, version: str) -> tuple[str, str]:
    """Rewrite EVERY match. For files that stamp the version more than once."""
    path = _ROOT / rel
    text = path.read_text(encoding="utf-8")
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        raise SystemExit(f"{rel}: pattern not found: {pattern}")
    old = m.group(1)
    path.write_text(
        re.sub(pattern, replacement.format(v=version), text, flags=re.MULTILINE),
        encoding="utf-8",
    )
    return old, version


def _read(rel: str, pattern: str) -> str:
    m = re.search(pattern, (_ROOT / rel).read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else "<not found>"


def _read_json(rel: str, *, package_root: bool = False) -> str:
    data = json.loads((_ROOT / rel).read_text(encoding="utf-8"))
    return data["packages"][""]["version"] if package_root else data["version"]


#: (label, reader, writer). Kept deliberately parallel to the readers in
#: tests/test_version_consistency.py — if that file gains a source, add it here
#: too, or the next release silently drifts again.
def _plan(version: str):
    return [
        ("package.json",
         lambda: _read_json("package.json"),
         lambda: _sub_json("package.json", version)),
        ("pyproject.toml",
         lambda: _read("pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
         lambda: _sub_regex("pyproject.toml", r'^version\s*=\s*"([^"]+)"',
                            'version = "{v}"', version)),
        ("__init__.py",
         lambda: _read("src/superlocalmemory/__init__.py",
                       r'^__version__\s*=\s*["\']([^"\']+)["\']'),
         lambda: _sub_regex("src/superlocalmemory/__init__.py",
                            r'^__version__\s*=\s*["\']([^"\']+)["\']',
                            '__version__ = "{v}"', version)),
        ("plugin-src/manifest.json",
         lambda: _read_json("plugin-src/manifest.json"),
         lambda: _sub_json("plugin-src/manifest.json", version)),
        ("plugin-src/requirements.txt",
         lambda: _read("plugin-src/requirements.txt", r"superlocalmemory==([^\s]+)"),
         lambda: _sub_regex("plugin-src/requirements.txt",
                            r"superlocalmemory==([^\s]+)",
                            "superlocalmemory=={v}", version)),
        ("package-lock.json",
         lambda: _read_json("package-lock.json"),
         lambda: _sub_json("package-lock.json", version)),
        ("package-lock.json root",
         lambda: _read_json("package-lock.json", package_root=True),
         lambda: _sub_json("package-lock.json", version, package_root=True)),
        ("plugin/.claude-plugin/plugin.json",
         lambda: _read_json("plugin/.claude-plugin/plugin.json"),
         lambda: _sub_json("plugin/.claude-plugin/plugin.json", version)),
        ("plugin/requirements.txt",
         lambda: _read("plugin/requirements.txt", r"superlocalmemory==([^\s]+)"),
         lambda: _sub_regex("plugin/requirements.txt",
                            r"superlocalmemory==([^\s]+)",
                            "superlocalmemory=={v}", version)),
        ("CITATION.cff",
         # Quotes are REQUIRED, not optional: the release contract test matches
         # ^version:\s*"..." exactly. An unquoted value must read as drift, or
         # this entry reports "ok" and the rewrite never runs.
         lambda: _read("CITATION.cff", r'^version:\s*"([^"]+)"'),
         lambda: _sub_regex("CITATION.cff", r'^version:\s*["\']?([^"\'\n]+?)["\']?$',
                            'version: "{v}"', version)),
        # date-released must be present, quoted, and not in the future:
        # tests/release/test_v37_rc_candidate_contract.py asserts all three.
        ("CITATION.cff date-released",
         lambda: _read("CITATION.cff", r'^date-released:\s*"([^"]+)"'),
         lambda: _sub_regex("CITATION.cff", r'^date-released:\s*"([^"]+)"',
                            'date-released: "%s"' % _today(), version,
                            reported=_today())),
        ("uv.lock",
         lambda: _read("uv.lock",
                       r'name = "superlocalmemory"\nversion = "([^"]+)"'),
         lambda: _sub_regex("uv.lock",
                            r'(?<=name = "superlocalmemory"\n)version = "([^"]+)"',
                            'version = "{v}"', version)),
        # Two copies of the agent rules carry a version footer. codex-plugin/ is
        # the generated artifact the test checks; plugin-src/rules/ is its source.
        # Both sat at 4.0.4 through two releases.
        ("codex-plugin/AGENTS.md",
         lambda: _read("codex-plugin/AGENTS.md", r"SuperLocalMemory v([0-9.]+)"),
         lambda: _sub_regex("codex-plugin/AGENTS.md", r"SuperLocalMemory v([0-9.]+)",
                            "SuperLocalMemory v{v}", version)),
        # Carries the version three times (BEGIN marker, END marker, footer),
        # so this one needs replace-all rather than the first match.
        ("plugin-src/rules/CLAUDE.md.fragment",
         lambda: _read("plugin-src/rules/CLAUDE.md.fragment",
                       r"SuperLocalMemory v([0-9.]+)"),
         lambda: _sub_all("plugin-src/rules/CLAUDE.md.fragment",
                          r"SuperLocalMemory v([0-9.]+)",
                          "SuperLocalMemory v{v}", version)),
        ("plugin-src/rules/AGENTS.md",
         lambda: _read("plugin-src/rules/AGENTS.md", r"SuperLocalMemory v([0-9.]+)"),
         lambda: _sub_regex("plugin-src/rules/AGENTS.md", r"SuperLocalMemory v([0-9.]+)",
                            "SuperLocalMemory v{v}", version)),
        # The first thing anyone sees. It states the version three times — the
        # title, the summary line, and the release badge — and this script did
        # not know about any of them, so 4.1.0 was bumped everywhere the
        # consistency test looks and the front page still advertised 4.0.10.
        # The test does not read README, which is exactly why the script must.
        # Three parts required. ``V([0-9.]+)`` also matches "SuperLocalMemory V4"
        # in the prose and the diagram alt text, where V4 is the product line and
        # not a stamp -- a sweep on that pattern rewrote both.
        ("README.md title",
         lambda: _read("README.md", r"SuperLocalMemory V([0-9]+\.[0-9]+\.[0-9]+)"),
         lambda: _sub_all("README.md",
                          r"SuperLocalMemory V([0-9]+\.[0-9]+\.[0-9]+)",
                          "SuperLocalMemory V{v}", version)),
        # Each construct is matched exactly. A sweep of every ``vX.Y.Z`` in this
        # file would also rewrite its references to v3.8.1, v3.6.7 and v3.6.15,
        # which are history and not this release.
        ("README.md summary",
         lambda: _read("README.md", r"<code>v([0-9.]+)</code>"),
         lambda: _sub_regex("README.md", r"<code>v([0-9.]+)</code>",
                            "<code>v{v}</code>", version)),
        ("README.md release badge",
         lambda: _read("README.md", r"badge/v([0-9.]+)-Current_Release"),
         lambda: _sub_all("README.md",
                          r"v([0-9.]+)(-Current_Release| — Current Release)",
                          "v{v}\\2", version)),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", help="target version, e.g. 4.0.7")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit non-zero; change nothing")
    args = ap.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        raise SystemExit(f"not a release version: {args.version!r}")

    def _date_ok(value: str) -> bool:
        """date-released is acceptable when it parses and is not in the future."""
        from datetime import date
        try:
            return date.fromisoformat(value) <= date.today()
        except ValueError:
            return False

    _PREDICATES = {"CITATION.cff date-released": _date_ok}

    drift = []
    for entry in _plan(args.version):
        label, read, write = entry[0], entry[1], entry[2]
        accepts = _PREDICATES.get(label, lambda v: v == args.version)
        try:
            current = read()
        except Exception as exc:
            print(f"  !! {label}: unreadable ({exc})")
            drift.append(label)
            continue

        if accepts(current):
            print(f"  ok {label:36s} {current}")
            continue

        drift.append(label)
        if args.check:
            print(f"  ✗  {label:36s} {current}  (want {args.version})")
        else:
            old, new = write()
            print(f"  ->  {label:36s} {old} -> {new}")

    if args.check and drift:
        print(f"\n{len(drift)} source(s) disagree with {args.version}")
        return 1
    if drift and not args.check:
        print(f"\nupdated {len(drift)} source(s) to {args.version}")
    else:
        print(f"\nall sources already at {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
