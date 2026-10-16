#!/usr/bin/env python3
"""Generate reproducible, evidence-level-aware integration compatibility data."""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

SCHEMA = "superlocalmemory.integration-compatibility/v1"
REQUIRED_MEMORY_TOOLS = {"remember", "recall", "update_memory", "forget"}


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the source manifest and expand each client's contract template."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    templates = raw.get("contract_templates", {})
    clients: list[dict[str, Any]] = []
    for source in raw.get("clients", []):
        client = copy.deepcopy(source)
        template_name = client.pop("template")
        if template_name not in templates:
            raise ValueError(f"unknown integration contract template: {template_name}")
        contracts = copy.deepcopy(templates[template_name])
        for name, override in client.pop("contract_overrides", {}).items():
            contracts[name] = {**contracts.get(name, {}), **override}
        client["contracts"] = contracts
        clients.append(client)
    return {"schema": raw["schema"], "clients": clients}


def _parse_config(path: Path, fmt: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if fmt == "json":
        value = json.loads(text)
    elif fmt == "toml":
        value = tomllib.loads(text)
    elif fmt == "yaml":
        value = yaml.safe_load(text)
    else:
        raise ValueError(f"unsupported config format: {fmt}")
    if not isinstance(value, dict):
        raise ValueError(f"{path} did not parse to an object")
    return value


def _extract_slm_block(data: dict[str, Any], descriptor: Any) -> dict[str, Any]:
    container = data
    for key in descriptor.server_key.split("."):  # OpenClaw: mcp.servers
        container = container[key]
    if descriptor.fmt == "yaml":
        matches = [
            item for item in container
            if isinstance(item, dict) and item.get("name") == "superlocalmemory"
        ]
        if len(matches) != 1:
            raise ValueError("Continue config must contain exactly one SLM provider")
        return matches[0]
    return container["superlocalmemory"]


def _validate_start_command(block: dict[str, Any]) -> None:
    """Accept only a pinned SLM entry point or Python module argv, never a shell.

    Do not resolve symlinks: a venv interpreter must retain its venv identity.
    This checks launch shape, not whether an external host executed the file.
    """
    command = block.get("command")
    args = block.get("args", [])
    if isinstance(command, list) and command and args == []:
        command, args = command[0], command[1:]
    if not isinstance(command, str) or not isinstance(args, list):
        raise AssertionError("unsafe start command: expected executable and argv")
    if any(ord(char) < 32 or char in ";&|`$<>" for char in command):
        raise AssertionError("unsafe start command: shell syntax or control characters")
    posix, windows = PurePosixPath(command), PureWindowsPath(command)
    path = windows if windows.is_absolute() else posix
    if not path.is_absolute() or ".." in path.parts:
        raise AssertionError("unsafe start command: executable must be absolute")
    name = path.name.lower()
    expected = ["mcp"] if name in {"slm", "slm.exe"} else [
        "-m", "superlocalmemory.cli", "mcp"
    ]
    if (name not in {"slm", "slm.exe"}
            and not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", name)) or args != expected:
        raise AssertionError("unsafe start command: expected pinned SLM MCP argv")


def _portable_config(repo: Path, client_id: str, work: Path) -> str:
    from superlocalmemory.hooks.portable_kit import IDE_MATRIX, connect_ide

    descriptor = IDE_MATRIX[client_id]
    home = work / client_id
    connect_kwargs: dict[str, Any] = {"home": home}
    if client_id == "vscode-copilot":
        # VS Code/Copilot owns project-local .vscode and .github surfaces;
        # generating them in a synthetic home would advertise an unsupported
        # global installation contract.
        connect_kwargs.update({"here": True, "project": home})
    first = connect_ide(client_id, **connect_kwargs)
    second = connect_ide(client_id, **connect_kwargs)
    if first["error"] or second["error"] or second["mcp_config"] != "unchanged":
        raise AssertionError(f"non-idempotent generated config: {first!r}, {second!r}")
    path = Path(first["mcp_path"])
    data = _parse_config(path, descriptor.fmt)
    block = _extract_slm_block(data, descriptor)
    _validate_start_command(block)
    # The generated path is host-dependent for clients such as Claude Desktop
    # (macOS Application Support vs Linux .config). Release evidence records
    # the portable contract that was executed, not a host-specific temp path.
    return f"{client_id}:portable-config"


def _declared_profile_tools(repo: Path) -> set[str]:
    """Extract the core MCP profile from source without starting MCP threads.

    The profile frozensets live in the pure-data ``mcp/profiles.py`` module
    (``mcp/server.py`` re-imports them), so parse that. Accept both plain and
    annotated assignments — ``_PROFILE_CORE = frozenset({...})`` and
    ``_PROFILE_CORE: frozenset[str] = frozenset({...})``. Fall back to the
    pre-3.8 location (server.py) so older checkouts still resolve.
    """
    source_path = repo / "src/superlocalmemory/mcp/profiles.py"
    if not source_path.exists():
        source_path = repo / "src/superlocalmemory/mcp/server.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = {node.target.id}
            value = node.value
        else:
            continue
        if "_PROFILE_CORE" not in names:
            continue
        if not isinstance(value, ast.Call) or not value.args:
            break
        literal = ast.literal_eval(value.args[0])
        return set(literal)
    raise ValueError("could not extract _PROFILE_CORE")


def _mcp_surface(repo: Path, _client_id: str, _work: Path) -> str:
    tools = _declared_profile_tools(repo)
    missing = REQUIRED_MEMORY_TOOLS - tools
    if missing:
        raise AssertionError(f"core MCP profile is missing: {sorted(missing)}")

    sources = {
        "remember": repo / "src/superlocalmemory/mcp/tools_core.py",
        "recall": repo / "src/superlocalmemory/mcp/tools_core.py",
        "update_memory": repo / "src/superlocalmemory/mcp/tools_core.py",
        "forget": repo / "src/superlocalmemory/mcp/tools_v33.py",
    }
    for tool, path in sources.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definitions = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if tool not in definitions:
            raise AssertionError(f"declared MCP tool {tool} has no implementation")
    return ",".join(sorted(REQUIRED_MEMORY_TOOLS))


def _active_adapter(repo: Path, client_id: str, work: Path) -> str:
    from superlocalmemory.hooks.antigravity_adapter import AntigravityAdapter
    from superlocalmemory.hooks.copilot_adapter import CopilotAdapter
    from superlocalmemory.hooks.cursor_adapter import CursorAdapter

    base = work / f"adapter-{client_id}"
    base.mkdir(parents=True, exist_ok=True)
    db = base / "memory.db"
    recall: Callable[..., list[Any]] = lambda *_args, **_kwargs: []
    if client_id == "cursor":
        adapter = CursorAdapter(
            scope="project", base_dir=base, sync_log_db=db, recall_fn=recall
        )
    elif client_id == "antigravity":
        adapter = AntigravityAdapter(
            scope="workspace", base_dir=base, sync_log_db=db, recall_fn=recall
        )
    elif client_id == "vscode-copilot":
        (base / ".github").mkdir(exist_ok=True)
        adapter = CopilotAdapter(base_dir=base, sync_log_db=db, recall_fn=recall)
    else:
        raise ValueError(f"no active adapter checker for {client_id}")

    if not adapter.sync() or not adapter.target_path.exists():
        raise AssertionError(f"{client_id} adapter did not inject its owned surface")
    injected = adapter.target_path.read_text(encoding="utf-8")
    if "SuperLocalMemory" not in injected or "remember" not in injected or "recall" not in injected:
        raise AssertionError(f"{client_id} adapter output lacks the memory protocol")

    # A second adapter process over the same path/db must reconnect without a rewrite.
    if client_id == "cursor":
        restarted = CursorAdapter(scope="project", base_dir=base, sync_log_db=db, recall_fn=recall)
    elif client_id == "antigravity":
        restarted = AntigravityAdapter(scope="workspace", base_dir=base, sync_log_db=db, recall_fn=recall)
    else:
        restarted = CopilotAdapter(base_dir=base, sync_log_db=db, recall_fn=recall)
    if restarted.sync():
        raise AssertionError(f"{client_id} reconnect rewrote identical owned content")

    restarted.disable()
    remaining = restarted.target_path.read_text(encoding="utf-8") if restarted.target_path.exists() else ""
    if "SuperLocalMemory" in remaining:
        raise AssertionError(f"{client_id} disable left SLM-owned content behind")
    return restarted.target_path.relative_to(work).as_posix()


#: Whether files here carry a POSIX executable bit (not on Windows).
HAS_EXECUTABLE_BIT = os.name != "nt"


def _ships_executable(repo: Path, path: Path) -> bool:
    """Whether ``path`` ships with its executable bit set.

    Windows files have no executable bit (``st_mode`` is always ``0o666``), so
    there the answer is the mode Git recorded, ``100755``, which is what a
    release build packs.
    """
    if HAS_EXECUTABLE_BIT:
        return bool(path.stat().st_mode & stat.S_IXUSR)
    done = subprocess.run(
        ["git", "ls-files", "--stage", "--", path.relative_to(repo).as_posix()],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return done.returncode == 0 and done.stdout.split(maxsplit=1)[:1] == ["100755"]


def _plugin_package(repo: Path, _client_id: str, _work: Path) -> str:
    config = repo / "plugin/.mcp.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    block = data["mcpServers"]["superlocalmemory"]
    command = block.get("command", "")
    prefix = "${CLAUDE_PLUGIN_ROOT}/"
    # Since #139 one entry serves every platform: "${ComSpec:-<launcher>}"
    # runs cmd.exe on Windows (args start the installed slm) and the bundled
    # launcher everywhere else. The launcher is still what POSIX hosts start.
    split = re.fullmatch(r"\$\{ComSpec:-(\$\{CLAUDE_PLUGIN_ROOT\}/[^}]+)\}", command)
    args = block.get("args")
    if split:
        command = split.group(1)
        if (not isinstance(args, list) or args[:3] != ["/d", "/s", "/c"]
                or len(args) != 4 or not str(args[3]).rstrip().endswith("slm mcp")):
            raise AssertionError(f"invalid Claude plugin start command: {block!r}")
    elif args != []:
        raise AssertionError(f"invalid Claude plugin start command: {block!r}")
    if not command.startswith(prefix):
        raise AssertionError(f"invalid Claude plugin start command: {block!r}")
    launcher = repo / "plugin" / command.removeprefix(prefix)
    if not launcher.is_file() or not _ships_executable(repo, launcher):
        raise AssertionError("Claude plugin launcher is missing or not executable")
    source = repo / "plugin-src/.mcp.json"
    if json.loads(source.read_text(encoding="utf-8")) != data:
        raise AssertionError("generated Claude plugin MCP config drifted from source")
    return launcher.relative_to(repo).as_posix()


def _hermes_package(repo: Path, _client_id: str, _work: Path) -> str:
    """Validate the native package locally without launching a Hermes host."""
    package = repo / "hermes-plugin"
    manifest = _parse_config(package / "plugin.yaml", "yaml")
    if manifest.get("name") != "superlocalmemory" or manifest.get("api_version") != 1:
        raise AssertionError("invalid Hermes native plugin manifest")
    inventory = _parse_config(package / "command-inventory.json", "json")
    if not {"remember", "recall", "update", "forget"} <= set(inventory["primary_commands"]):
        raise AssertionError("Hermes command inventory lacks the memory surface")
    tree = ast.parse((package / "__init__.py").read_text(encoding="utf-8"))
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    if "register" not in functions:
        raise AssertionError("Hermes package lacks its native register entry point")
    hooks = manifest.get("provides_hooks", [])
    if not hooks or not set(hooks) <= functions:
        raise AssertionError("Hermes manifest declares an unimplemented lifecycle hook")
    tools = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register_tool" and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    if set(manifest.get("provides_tools", [])) != tools:
        raise AssertionError("Hermes manifest and native tool registrations disagree")
    for skill in inventory["skills"]:
        if not (package / "skills" / skill / "SKILL.md").is_file():
            raise AssertionError(f"Hermes package is missing skill: {skill}")
    ast.parse((package / "remote.py").read_text(encoding="utf-8"))
    return "hermes-plugin/plugin.yaml"


def _hermes_config(_repo: Path, _client_id: str, work: Path) -> str:
    """Round-trip the optional dashboard MCP setup independently of its plugin."""
    from superlocalmemory.server.local_host_setup import configure_hermes

    home = work / "dashboard-hermes"
    path = home / ".hermes/config.yaml"
    if not configure_hermes("core", home=home):
        raise AssertionError("Hermes dashboard config generation failed")
    first = path.read_bytes()
    if not configure_hermes("core", home=home) or path.read_bytes() != first:
        raise AssertionError("Hermes dashboard config is not idempotent")
    data = _parse_config(path, "yaml")
    _validate_start_command(data["mcp_servers"]["superlocalmemory"])
    return "hermes:dashboard-config"


CHECKERS: dict[str, Callable[[Path, str, Path], str]] = {
    "portable_config": _portable_config,
    "mcp_surface": _mcp_surface,
    "active_adapter": _active_adapter,
    "plugin_package": _plugin_package,
    "hermes_package": _hermes_package,
    "hermes_config": _hermes_config,
}


def _validate_artifacts(repo: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for client in manifest["clients"]:
        for artifact in client.get("artifacts", []):
            path = repo / artifact
            if not path.is_file():
                raise FileNotFoundError(f"{client['id']} artifact is missing: {artifact}")
            if artifact.startswith("ide/configs/"):
                suffix = path.suffix.lower()
                fmt = {".json": "json", ".toml": "toml", ".yaml": "yaml"}.get(suffix)
                if fmt:
                    _parse_config(path, fmt)
            results.append({"client_id": client["id"], "artifact": artifact})
    return results


def validate_local_contracts(
    repo: Path, manifest: dict[str, Any], *, work_dir: Path | None = None
) -> dict[str, Any]:
    """Execute every `proven_local` checker and validate all shipped artifacts."""
    repo = repo.resolve()
    src = str(repo / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    owned_temp = None
    if work_dir is None:
        owned_temp = tempfile.TemporaryDirectory(prefix="slm-integration-contract-")
        work = Path(owned_temp.name).resolve()
    else:
        work = work_dir.resolve()
        work.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    try:
        _validate_artifacts(repo, manifest)
        cache: dict[tuple[str, str], str] = {}
        for client in manifest["clients"]:
            for contract_name, contract in client["contracts"].items():
                if contract["status"] != "proven_local":
                    continue
                checker_name = contract.get("checker", "")
                checker = CHECKERS.get(checker_name)
                if checker is None:
                    failures.append({
                        "client_id": client["id"], "contract": contract_name,
                        "error": f"unknown checker: {checker_name}",
                    })
                    continue
                cache_key = (client["id"], checker_name)
                try:
                    if cache_key not in cache:
                        cache[cache_key] = checker(repo, client["id"], work)
                    detail = cache[cache_key]
                    checks.append({
                        "client_id": client["id"], "contract": contract_name,
                        "status": "passed", "checker": checker_name,
                        "detail": detail,
                    })
                except Exception as exc:  # release evidence must report, not abort
                    failures.append({
                        "client_id": client["id"], "contract": contract_name,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
    finally:
        if owned_temp is not None:
            owned_temp.cleanup()
    return {"checks_run": len(checks), "checks": checks, "failures": failures}


def build_evidence(
    repo: Path, manifest: dict[str, Any], *, work_dir: Path | None = None
) -> dict[str, Any]:
    validation = validate_local_contracts(repo, manifest, work_dir=work_dir)
    if validation["failures"]:
        raise RuntimeError(f"integration contract failures: {validation['failures']!r}")
    # Evidence is a release artifact. Canonicalize list order so equivalent
    # manifests produce byte-stable JSON across Python versions and test
    # collection orders, rather than making CI depend on insertion order.
    clients = sorted(manifest["clients"], key=lambda client: client["id"])
    checks = sorted(
        validation["checks"],
        key=lambda check: (
            check["client_id"],
            check["contract"],
            check["checker"],
            check["detail"],
        ),
    )
    return {
        "schema": SCHEMA,
        "summary": {
            "named_clients": len(clients),
            "locally_contract_tested": sum(
                any(c["status"] == "proven_local" for c in client["contracts"].values())
                for client in clients
            ),
            "external_host_runtime_proven": sum(
                bool(client["external_host_runtime_proven"]) for client in clients
            ),
            "static_or_manual_only": sum(
                not any(c["status"] == "proven_local" for c in client["contracts"].values())
                for client in clients
            ),
            "checks_run": validation["checks_run"],
        },
        "clients": clients,
        "checks": checks,
    }


def _render_evidence(evidence: dict[str, Any]) -> str:
    """Keep each client/check on one line so release diffs remain bounded."""
    fields = []
    for key, value in sorted(evidence.items()):
        if isinstance(value, list):
            rows = ["    " + json.dumps(row, sort_keys=True) for row in value]
            rendered = "[\n" + ",\n".join(rows) + "\n  ]"
        else:
            rendered = json.dumps(value, indent=2, sort_keys=True).replace("\n", "\n  ")
        fields.append(f"  {json.dumps(key)}: {rendered}")
    return "{\n" + ",\n".join(fields) + "\n}\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    manifest = load_manifest(args.manifest or repo / "ide/integration-contracts.json")
    evidence = build_evidence(repo, manifest)
    rendered = _render_evidence(evidence)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
