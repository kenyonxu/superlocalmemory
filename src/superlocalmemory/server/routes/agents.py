# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
"""SuperLocalMemory V3 - Agent Registry + Trust Routes
 - MIT License

Routes: /api/agents, /api/agents/stats, /api/trust/stats, /api/trust/signals/{agent_id}
Uses V3 TrustScorer and core.registry.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from .helpers import DB_PATH

logger = logging.getLogger("superlocalmemory.routes.agents")
router = APIRouter()

# Feature flag: V3 trust scorer
TRUST_AVAILABLE = False
try:
    from superlocalmemory.trust.scorer import TrustScorer
    TRUST_AVAILABLE = True
except ImportError:
    pass

REGISTRY_AVAILABLE = False
try:
    from superlocalmemory.core.registry import AgentRegistry
    REGISTRY_AVAILABLE = True
except ImportError:
    pass


@router.get("/api/agents")
async def get_agents(
    request: Request,
    protocol: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    """List registered agents with optional protocol filter."""
    if not REGISTRY_AVAILABLE:
        return {"agents": [], "count": 0, "message": "Agent registry not available"}
    try:
        engine = getattr(request.app.state, "engine", None)
        if engine and hasattr(engine, '_db'):
            registry = AgentRegistry(engine._db)
            agents = registry.list_agents(protocol=protocol, limit=limit)
            stats = registry.get_stats()
            return {"agents": agents, "count": len(agents), "stats": stats}
        return {"agents": [], "count": 0, "message": "Engine not initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent registry error: {str(e)}")


@router.get("/api/agents/stats")
async def get_agent_stats(request: Request):
    """Get agent registry statistics."""
    if not REGISTRY_AVAILABLE:
        return {"total_agents": 0, "message": "Agent registry not available"}
    try:
        engine = getattr(request.app.state, "engine", None)
        if engine and hasattr(engine, '_db'):
            registry = AgentRegistry(engine._db)
            return registry.get_stats()
        return {"total_agents": 0, "message": "Engine not initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent stats error: {str(e)}")


@router.get("/api/trust/stats")
async def get_trust_stats(request: Request):
    """Get trust scoring statistics."""
    if not TRUST_AVAILABLE:
        return {"total_signals": 0, "message": "Trust scorer not available"}
    try:
        engine = getattr(request.app.state, "engine", None)
        if engine and engine._trust_scorer:
            scorer = engine._trust_scorer
            return scorer.get_trust_stats()
        return {"total_signals": 0, "message": "Trust scorer not initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trust stats error: {str(e)}")


@router.get("/api/trust/signals/{agent_id}")
async def get_agent_trust_signals(
    request: Request, agent_id: str,
    limit: int = Query(50, ge=1, le=200),
):
    """Get trust signal history for a specific agent."""
    if not TRUST_AVAILABLE:
        return {"signals": [], "count": 0}
    try:
        engine = getattr(request.app.state, "engine", None)
        if engine and engine._trust_scorer:
            scorer = engine._trust_scorer
            signals = scorer.get_signals(agent_id, limit=limit)
            score = scorer.get_trust_score(agent_id)
            return {
                "agent_id": agent_id, "trust_score": score,
                "signals": signals, "count": len(signals),
            }
        return {"agent_id": agent_id, "signals": [], "count": 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trust signals error: {str(e)}")
