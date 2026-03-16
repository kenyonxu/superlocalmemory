# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
"""SuperLocalMemory V3 - Profile Routes
 - MIT License

Routes: /api/profiles, /api/profiles/{name}/switch,
        /api/profiles/create, DELETE /api/profiles/{name}
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from .helpers import (
    get_db_connection, get_active_profile, validate_profile_name,
    ProfileSwitch, MEMORY_DIR, DB_PATH,
)

logger = logging.getLogger("superlocalmemory.routes.profiles")
router = APIRouter()

# WebSocket manager reference (set by ui_server.py at startup)
ws_manager = None


def _load_profiles_config() -> dict:
    """Load profiles.json config."""
    config_file = MEMORY_DIR / "profiles.json"
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        'profiles': {'default': {'name': 'default', 'description': 'Default memory profile'}},
        'active_profile': 'default',
    }


def _save_profiles_config(config: dict) -> None:
    """Save profiles.json config."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    config_file = MEMORY_DIR / "profiles.json"
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)


def _get_memory_count(profile: str) -> int:
    """Get memory count for a profile (V3 atomic_facts or V2 memories)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Try V3 table first
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM atomic_facts WHERE profile_id = ?", (profile,),
            )
            count = cursor.fetchone()[0]
        except Exception:
            cursor.execute(
                "SELECT COUNT(*) FROM memories WHERE profile = ?", (profile,),
            )
            count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


@router.get("/api/profiles")
async def list_profiles():
    """List available memory profiles."""
    try:
        config = _load_profiles_config()
        active = config.get('active_profile', 'default')
        profiles = []

        for name, info in config.get('profiles', {}).items():
            count = _get_memory_count(name)
            profiles.append({
                "name": name,
                "description": info.get('description', ''),
                "memory_count": count,
                "created_at": info.get('created_at', ''),
                "last_used": info.get('last_used', ''),
                "is_active": name == active,
            })

        return {
            "profiles": profiles,
            "active_profile": active,
            "total_profiles": len(profiles),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Profile list error: {str(e)}")


@router.post("/api/profiles/{name}/switch")
async def switch_profile(name: str):
    """Switch active memory profile."""
    try:
        if not validate_profile_name(name):
            raise HTTPException(status_code=400, detail="Invalid profile name.")

        config = _load_profiles_config()

        if name not in config.get('profiles', {}):
            available = ', '.join(config.get('profiles', {}).keys())
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{name}' not found. Available: {available}",
            )

        previous = config.get('active_profile', 'default')
        config['active_profile'] = name
        config['profiles'][name]['last_used'] = datetime.now().isoformat()
        _save_profiles_config(config)

        count = _get_memory_count(name)

        if ws_manager:
            await ws_manager.broadcast({
                "type": "profile_switched", "profile": name,
                "previous": previous, "memory_count": count,
                "timestamp": datetime.now().isoformat(),
            })

        return {
            "success": True, "active_profile": name,
            "previous_profile": previous, "memory_count": count,
            "message": f"Switched to profile '{name}' ({count} memories).",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Profile switch error: {str(e)}")


@router.post("/api/profiles/create")
async def create_profile(body: ProfileSwitch):
    """Create a new memory profile."""
    try:
        name = body.profile_name
        if not validate_profile_name(name):
            raise HTTPException(status_code=400, detail="Invalid profile name")

        config = _load_profiles_config()

        if name in config.get('profiles', {}):
            raise HTTPException(status_code=409, detail=f"Profile '{name}' already exists")

        config['profiles'][name] = {
            'name': name, 'description': f'Memory profile: {name}',
            'created_at': datetime.now().isoformat(), 'last_used': None,
        }
        _save_profiles_config(config)

        return {"success": True, "profile": name, "message": f"Profile '{name}' created"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Profile create error: {str(e)}")


@router.delete("/api/profiles/{name}")
async def delete_profile(name: str):
    """Delete a profile. Moves its memories to 'default'."""
    try:
        if name == 'default':
            raise HTTPException(status_code=400, detail="Cannot delete 'default' profile")

        config = _load_profiles_config()

        if name not in config.get('profiles', {}):
            raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
        if config.get('active_profile') == name:
            raise HTTPException(status_code=400, detail="Cannot delete active profile.")

        conn = get_db_connection()
        cursor = conn.cursor()
        # Move memories to default (try V3 first, then V2)
        moved = 0
        try:
            cursor.execute(
                "UPDATE atomic_facts SET profile_id = 'default' WHERE profile_id = ?",
                (name,),
            )
            moved = cursor.rowcount
        except Exception:
            pass
        try:
            cursor.execute(
                "UPDATE memories SET profile = 'default' WHERE profile = ?",
                (name,),
            )
            moved += cursor.rowcount
        except Exception:
            pass
        conn.commit()
        conn.close()

        del config['profiles'][name]
        _save_profiles_config(config)

        return {
            "success": True,
            "message": f"Profile '{name}' deleted. {moved} memories moved to 'default'.",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Profile delete error: {str(e)}")
