"""Sketch-only maintenance, with a read-only preview and no model startup."""

from __future__ import annotations

import shlex
import sqlite3
import sys
from argparse import Namespace
from dataclasses import replace
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from superlocalmemory.core.config import PolarQuantConfig, QuantizationConfig, SLMConfig


def print_plan(plan: dict, *, file: TextIO | None = None) -> None:
    print("Before applying quantization changes (counts from this preview):", file=file)
    print(f"  QJL repair needed: {plan.get('repair_needed', 0)}", file=file)
    print(f"  Need re-embedding: {plan.get('unrecoverable', 0)}", file=file)
    print(f"  Downgrades:  {plan.get('downgrades', 0)}", file=file)
    print(f"  Upgrades:    {plan.get('upgrades', 0)}", file=file)
    print(f"  Forgotten:   {plan.get('deleted', 0)} (vectors kept)", file=file, flush=True)


def cmd_repair_sketches(args: Namespace) -> None:
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.memory_write import memory_read
    from superlocalmemory.storage.qjl_repair import repair_status

    dry_run, use_json = getattr(args, "dry_run", True), getattr(args, "json", False)
    try:
        config = SLMConfig.load()
        pid = getattr(args, "profile", "") or config.active_profile
        with memory_read(config.db_path) as conn:
            quantization = repair_quantization(config, conn, pid)
            plan = {**repair_status(conn, pid, quantization), "downgrades": 0,
                    "upgrades": 0, "deleted": 0}
        result = {**plan, "repaired": 0, "repair_errors": 0}
        if not dry_run:
            print_plan(plan, file=sys.stderr if use_json else None)
            from superlocalmemory.core.admission import gate_cli_mutation
            from superlocalmemory.core.operation_request import OperationKind

            gate_cli_mutation(OperationKind.CONSOLIDATE)
            if plan["repair_needed"] > plan["unrecoverable"]:
                result = {**result, **_apply_sketches(config, quantization, pid)}
    except Exception as exc:
        if not use_json:
            raise
        from superlocalmemory.cli.json_output import json_print

        json_print("quantize", error={"code": "QJL_REPAIR_ERROR", "message": str(exc)})
        sys.exit(1)
    _show_repair(args, dry_run=dry_run, use_json=use_json, plan=plan, result=result)


def _apply_sketches(config: SLMConfig, quantization: QuantizationConfig,
                    pid: str) -> dict[str, int]:
    from superlocalmemory.math.polar_quant import PolarQuantEncoder
    from superlocalmemory.math.qjl import QJLEncoder
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.qjl_repair import repair_sketches
    from superlocalmemory.storage.quantized_store import QuantizedEmbeddingStore

    matrix = _check_rotation(quantization.polar)
    db = DatabaseManager(config.db_path)
    try:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        import numpy as np

        # Freeze the existing matrix outside the data root. Even
        # a concurrent file replacement cannot make codec startup
        # generate or overwrite the store's base geometry.
        with TemporaryDirectory(prefix="slm-sketch-repair-") as tmp:
            rotation = Path(tmp) / "rotation.npy"
            np.save(rotation, matrix)
            polar = PolarQuantEncoder(replace(
                quantization.polar, rotation_matrix_path=str(rotation)))
            store = QuantizedEmbeddingStore(db, polar, QJLEncoder(
                quantization.qjl), quantization)
            return repair_sketches(db, store, pid, quantization)
    finally:
        db.close()


def _show_repair(args: Namespace, *, dry_run: bool, use_json: bool,
                 plan: dict[str, int], result: dict[str, int]) -> None:
    if use_json:
        from superlocalmemory.cli.json_output import json_print

        command = ["slm", "quantize", "--repair-sketches", "--execute", "--json"]
        if getattr(args, "profile", ""):
            command += ["--profile", args.profile]
        json_print("quantize", data={"dry_run": dry_run, "repair_only": True,
                                     "plan": plan, **result}, next_actions=[
            {"command": shlex.join(command), "description": "Repair sketches only"}])
        return
    print(f"QJL sketch repair only (dry_run={dry_run})")
    print(f"  QJL repair needed: {result['repair_needed']}")
    print(f"  QJL repaired:      {result['repaired']}")
    print(f"  QJL repair errors: {result['repair_errors']}")
    print(f"  Need re-embedding: {result['unrecoverable']}")
    print("  Downgrades:  0\n  Upgrades:    0\n  Forgotten:   0")
    if dry_run:
        command = ["slm", "quantize", "--repair-sketches", "--execute"]
        if getattr(args, "profile", ""):
            command += ["--profile", args.profile]
        print("To repair sketches only: " + shlex.join(command))


def repair_quantization(config: SLMConfig, conn: sqlite3.Connection,
                        profile_id: str) -> QuantizationConfig:
    """Use the stored space while a desired model change is still pending."""
    from superlocalmemory.core.embedding_live import live_signature
    from superlocalmemory.storage.embedding_codec import decode_embedding

    signature, _ = live_signature(config, conn)
    dimension = int(signature.rpartition("::")[2]) if signature else 0
    if not dimension:
        # Pre-space-record stores can still have retained originals. A codec
        # reads both the legacy JSON and current float32 representation.
        row = conn.execute("SELECT embedding FROM atomic_facts WHERE profile_id=? "
                           "AND embedding IS NOT NULL LIMIT 1", (profile_id,)).fetchone()
        vector = decode_embedding(row[0]) if row else None
        dimension = len(vector) if vector is not None else config.embedding.dimension
    return replace(config.quantization, polar=replace(
        config.quantization.polar, dimension=dimension))


def _check_rotation(polar: PolarQuantConfig) -> NDArray:
    """A sketch-only repair must never generate or replace base geometry."""
    from pathlib import Path

    import numpy as np

    from superlocalmemory.infra.data_root import state_path

    prefix = "turbo" if polar.codebook_method == "turbo" else "polar"
    path = (Path(polar.rotation_matrix_path) if polar.rotation_matrix_path
            else state_path(f"{prefix}_rotation_{polar.dimension}.npy"))
    if not polar.rotation_matrix_path and prefix == "turbo" and not path.exists():
        # TurboQuant can reuse the original polar geometry on legacy stores.
        path = state_path(f"polar_rotation_{polar.dimension}.npy")
    try:
        matrix = np.load(path, allow_pickle=False)
        valid = matrix.shape == (polar.dimension, polar.dimension) and np.isfinite(matrix).all()
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise ValueError("The stored rotation matrix is missing or incompatible. "
                         "Restore the original rotation matrix from backup, "
                         "then retry sketch repair.")
    return matrix
