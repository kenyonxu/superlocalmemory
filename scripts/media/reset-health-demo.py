#!/usr/bin/env python3
"""Put a SANDBOX store back to "four hidden memories wait for a Fix" for the Memory health GIF.

Usage: reset-health-demo.py <sandbox-data-dir>   (stop the sandbox daemon first, restart it after)
Never point this at your own data folder.
"""
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
if root == (Path.home() / ".superlocalmemory").resolve():
    sys.exit("refusing to touch the default data folder")
db = sqlite3.connect(root / "memory.db", timeout=30)
for table in ("memory_health_repairs", "enrichment_changes"):
    db.execute(f"DELETE FROM {table}")
db.execute("DELETE FROM enrichment_runs WHERE action != 'enrichment'")
picks = db.execute(
    "SELECT fact_id, profile_id FROM atomic_facts WHERE content LIKE '%Redis%' "
    "OR content LIKE '%gateway handles%' OR content LIKE '%Fastify%' "
    "OR content LIKE '%nightly report%' LIMIT 4").fetchall()
for fid, pid in picks:
    db.execute("INSERT OR REPLACE INTO fact_retention(fact_id, profile_id, retention_score, "
               "memory_strength, access_count, lifecycle_zone) VALUES (?,?,?,?,?,?)",
               (fid, pid, 0.91, 1.0, 4, "archive"))
    db.execute("UPDATE atomic_facts SET lifecycle='archived' WHERE fact_id=?", (fid,))
db.commit()
print(f"{len(picks)} memories hidden again")
