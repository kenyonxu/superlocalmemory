# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
"""SuperLocalMemory V3 - Route Helpers
 - MIT License

Shared utilities for all route modules: DB connection, dict factory,
profile helper, validation, Pydantic models, config paths.
"""
import re
import json
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

# V3 paths (migrated from ~/.claude-memory to ~/.superlocalmemory)
MEMORY_DIR = Path.home() / ".superlocalmemory"
DB_PATH = MEMORY_DIR / "memory.db"
UI_DIR = Path(__file__).parent.parent / "ui"
PROFILES_DIR = MEMORY_DIR / "profiles"


def get_db_connection() -> sqlite3.Connection:
    """Get database connection."""
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail="Memory database not found. Run 'slm init' to initialize."
        )
    return sqlite3.connect(str(DB_PATH))


def dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    """Convert SQLite row to dictionary."""
    fields = [column[0] for column in cursor.description]
    return dict(zip(fields, row))


def get_active_profile() -> str:
    """Read the active profile from profiles.json. Falls back to 'default'."""
    config_file = MEMORY_DIR / "profiles.json"
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                pconfig = json.load(f)
            return pconfig.get('active_profile', 'default')
        except (json.JSONDecodeError, IOError):
            pass
    return 'default'


def validate_profile_name(name: str) -> bool:
    """Validate profile name (alphanumeric, underscore, hyphen only)."""
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))


# ============================================================================
# Pydantic Models (shared across routes)
# ============================================================================

class SearchRequest(BaseModel):
    """Advanced search request model."""
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    category: Optional[str] = None
    project_name: Optional[str] = None
    cluster_id: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class ProfileSwitch(BaseModel):
    """Profile switching request."""
    profile_name: str = Field(..., min_length=1, max_length=50)


class BackupConfigRequest(BaseModel):
    """Backup configuration update request."""
    interval_hours: Optional[int] = Field(None, ge=1, le=8760)
    max_backups: Optional[int] = Field(None, ge=1, le=100)
    enabled: Optional[bool] = None
