# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
"""SuperLocalMemory V3 - Compliance Routes
 - AGPL-3.0-or-later

Routes: /api/compliance/status, /api/compliance/audit,
        /api/compliance/retention-policy
Uses V3 compliance modules: ABACEngine, AuditChain, RetentionEngine.
"""
import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from superlocalmemory.infra.data_root import DynamicStatePath
from superlocalmemory.server.route_mutations import authorize_route_mutation
from superlocalmemory.storage.memory_write import memory_read, memory_write

from .export_delivery import export_response
from .helpers import DB_PATH, get_active_profile, get_engine_lazy

logger = logging.getLogger("superlocalmemory.routes.compliance")
router = APIRouter()

# The engine's audit post-hooks write to <data_root>/audit_chain.db
# (core/engine_wiring.py). The route MUST read that same file — an earlier
# "audit.db" path pointed at a file nothing ever wrote, so the Compliance tab
# always showed zero events.
AUDIT_DB = DynamicStatePath("audit_chain.db")

# Feature detection
COMPLIANCE_AVAILABLE = False
try:
    from superlocalmemory.compliance.abac import ABACEngine
    from superlocalmemory.compliance.audit import AuditChain
    from superlocalmemory.compliance.gdpr import GDPRCompliance
    from superlocalmemory.compliance.retention import RetentionEngine
    COMPLIANCE_AVAILABLE = True
except ImportError:
    logger.info("V3 compliance engine not available")


def _require_manage(request) -> None:
    """Require MANAGE on the active workspace, where the workspace has roles.

    A store with no roles configured has nobody to check against, and refusing
    there would break every personal install; the loopback boundary is the
    control in that case.
    """
    try:
        from superlocalmemory.access.rbac import Permission
        from superlocalmemory.server.rbac_enforce import require_permission

        require_permission(request, Permission.MANAGE, profile=get_active_profile())
    except ImportError:  # pragma: no cover -- roles are always present in-tree
        return


COMPLIANCE_CACHE_S = 5.0
_AUDIT_CHAINS: dict = {}
_AUDIT_LOCK = threading.Lock()


def _audit():
    """One opened audit chain per trail. Opening one checks and upgrades the
    trail's layout under its write lock; doing that on every dashboard poll
    cost a second under load and competed with the daemon's own audit writes."""
    key = str(AUDIT_DB)
    with _AUDIT_LOCK:
        chain = _AUDIT_CHAINS.get(key)
        if chain is None:
            chain = AuditChain(key)
            _AUDIT_CHAINS[key] = chain
        return chain


@router.get("/api/compliance/status")
def compliance_status(request: Request):
    """Get compliance engine status for active profile."""
    # This returns which operations ran and what categories of memory exist.
    # That is operational metadata about someone's store; it had no gate at all.
    from superlocalmemory.server.write_identity import require_http_mutation_actor

    require_http_mutation_actor(
        request, getattr(request.app.state, "daemon_descriptor", None),
        actor_kind="compliance-read",
    )
    if not COMPLIANCE_AVAILABLE:
        return {"available": False, "message": "Compliance engine not available"}
    from superlocalmemory.server.view_cache import cached

    # Checked above for this caller; the summary itself is shared a few seconds.
    profile = get_active_profile()
    try:
        return cached("compliance-status", (str(AUDIT_DB), str(DB_PATH), profile),
                      COMPLIANCE_CACHE_S, lambda: _compliance_status(profile))
    except Exception:
        logger.exception("compliance_status error")
        return {"available": False, "error": "Internal server error"}


def _compliance_status(profile: str) -> dict:
    try:

        # Audit events (hash-chained, from the engine's live audit_chain.db).
        # Scope the count + recent list to the active profile.
        audit_events_count = 0
        recent_audit_events = []
        try:
            from superlocalmemory.server.audit_view import present_event

            audit = _audit()
            recent_audit_events = [present_event(e)
                                   for e in audit.query(profile_id=profile, limit=30)]
            # A COUNT, not 100,000 rows loaded per pane view (G-M4).
            audit_events_count = audit.count(profile_id=profile)
        except Exception as exc:
            logger.debug("audit chain: %s", exc)

        # Retention policies (scoped to the active profile).
        # RetentionEngine.__init__ runs DDL (CREATE TABLE IF NOT EXISTS) so even
        # list_rules() is a write at the constructor level — use memory_write().
        retention_policies = []
        try:
            retention_policies = _retention_rules(profile)
        except Exception as exc:
            logger.debug("retention engine: %s", exc)

        # ABAC policies
        abac_policies_count = 0
        try:
            abac = ABACEngine()
            abac_policies_count = len(abac._policies)
        except Exception:
            pass

        return {
            "available": True,
            "active_profile": profile,
            "audit_events_count": audit_events_count,
            "recent_audit_events": recent_audit_events,
            "retention_policies": retention_policies,
            "abac_policies_count": abac_policies_count,
        }
    finally:
        pass  # failures raise: compliance_status answers them, uncached


def _retention_rules(profile: str) -> list:
    """Rules for one workspace. Reading them does not take the database write lock.

    Only a store whose rules table is missing or old needs a writer (the engine
    creates and migrates it); every later look is a plain read, so this screen
    never waits behind a background job that holds the writer.
    """
    with memory_read(DB_PATH) as conn:
        rules = RetentionEngine.peek_rules(conn, profile)
    if rules is not None:
        return rules
    with memory_write(DB_PATH) as conn:
        return RetentionEngine(conn).list_rules(profile)


def warm_audit_integrity() -> None:
    """Check the audit trail once at start, so the first visit to the pane is instant."""
    if not COMPLIANCE_AVAILABLE:
        return
    from superlocalmemory.compliance.audit_verify_cache import warm

    warm(_audit())


@router.get("/api/compliance/audit")
def query_audit_trail(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    event_type: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
):
    """Query audit trail events with optional filters, a page at a time.

    ``total`` is how many events match (counted by the database); ``events`` is
    the page of at most ``limit`` starting at ``offset``, newest first.
    """
    if not COMPLIANCE_AVAILABLE:
        return {"available": False, "error": "Compliance engine not available"}
    # The audit trail reveals operations, actors, and fact_ids. This GET is not
    # covered by the mutation middleware; the same-origin dashboard reads it via
    # a plain GET (no token header), so gate on the loopback-trusted mutation
    # boundary — local owner allowed, remote uncredentialed caller fails closed.
    from superlocalmemory.server.write_identity import require_http_mutation_actor
    require_http_mutation_actor(request, getattr(request.app.state, "daemon_descriptor", None),
                                actor_kind="audit-read")
    try:
        from superlocalmemory.server.rbac_enforce import has_global_manage

        profile = get_active_profile()
        # Installation-wide events (accounts, the login policy, sign-in) carry
        # no workspace; whoever holds authority over the whole installation
        # sees them next to the active workspace's (PR 248 review, finding 5).
        installation = has_global_manage(request)
        from superlocalmemory.server.view_cache import cached

        # Permission is decided above for this caller; the page is shared a few
        # seconds between callers with the same view of the trail.
        return cached("compliance-audit",
                      (str(AUDIT_DB), profile, installation, limit, event_type, since, offset),
                      COMPLIANCE_CACHE_S,
                      lambda: _audit_page(profile, installation, limit, event_type, since, offset))
    except Exception:
        logger.exception("query_audit_trail error")
        return {"available": False, "error": "Internal server error"}


def _audit_page(profile, installation, limit, event_type, since, offset) -> dict:
    from superlocalmemory.server.audit_view import integrity_summary, present_event

    # Failures raise: query_audit_trail answers them, and nothing is cached.
    scope = (profile, "") if installation else profile
    audit = _audit()
    events = [present_event(e) for e in audit.query(
        profile_id=scope, operation=event_type, start_date=since,
        limit=limit, offset=offset,
    )]
    matching = audit.count(profile_id=scope, operation=event_type, start_date=since)
    integrity = integrity_summary(audit, cached=True)
    return {
        "available": True, "events": events, "total": matching,
        "returned": len(events), "offset": offset,
        "has_more": offset + len(events) < matching,
        "chain_verified": integrity["verified"],
        "integrity_label": integrity["label"],
        "integrity": integrity["report"],
        "active_profile": profile,
        "includes_installation_events": installation,
        "filters": {"event_type": event_type, "since": since, "limit": limit},
    }


def _erasure_succeeded(result: dict) -> bool:
    """One place that decides, so the surfaces cannot disagree.

    The verdict lives in ``compliance.erasure_outcome`` and is shared with the
    command line: two questions (is the data gone; can that be shown afterwards)
    and every ``*_failed`` / ``*_failures`` / residue marker the erasure leaves.
    """
    from superlocalmemory.compliance.erasure_outcome import erasure_succeeded

    return erasure_succeeded(result)


def _erasure_response(profile_key: str, profile: str, result: dict, **extra) -> dict:
    """The body every erasure route returns: the counts, plus a statement of the outcome.

    ``erasure_report`` says what was removed and what remains and how to retry
    it; ``error`` carries one sentence of the same when the erasure is not
    complete, which is where the dashboard reads a failure from.
    """
    from superlocalmemory.compliance import erasure_outcome

    result = dict(result or {})
    ok = erasure_outcome.erasure_succeeded(result)
    body = {"success": ok, profile_key: profile, **extra, **result,
            "erasure_report": erasure_outcome.describe(result)}
    if not ok:
        body["error"] = erasure_outcome.failure_message(result) or "The erasure did not finish."
    return body


async def _leave_profile_to_erase(request: Request, profile: str) -> str:
    """Move the running service off ``profile``; returns "" or the reason it could not.

    Nothing has been erased when this fails, and the answer says so.
    """
    import asyncio

    from superlocalmemory.server.profile_runtime import (
        commit_daemon_profile_switch,
        get_profile_runtime,
    )

    try:
        runtime = get_profile_runtime(request.app.state)
        if runtime.snapshot.profile_id != profile:
            return ""
        await asyncio.to_thread(
            runtime.transition, "default",
            lambda prior, target: commit_daemon_profile_switch(request.app.state, prior, target),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not leave the profile to erase: %s", type(exc).__name__)
        return ("The service could not move off this profile (it is busy). Nothing was erased; "
                "try again in a moment.")
    return ""


@router.post("/api/compliance/retention-policy")
async def create_retention_policy(request: Request, data: dict):
    """Create a compliance retention policy.

    Body: {
        name: str,
        retention_days: int,
        category: str (maps to framework),
        action: "archive" | "tombstone" | "notify",
        applies_to: dict (optional)
    }
    """
    # A retention rule decides what is kept and for how long, so changing one
    # is a persistent configuration change on someone else's data. It is gated
    # on the same loopback-trusted boundary the audit read uses, and on MANAGE
    # in a workspace that has roles -- it had neither.
    from superlocalmemory.server.write_identity import require_http_mutation_actor

    require_http_mutation_actor(
        request, getattr(request.app.state, "daemon_descriptor", None),
        actor_kind="retention-policy",
    )
    _require_manage(request)
    if not COMPLIANCE_AVAILABLE:
        return {"success": False, "error": "Compliance engine not available"}

    name = data.get('name')
    retention_days = data.get('retention_days')
    framework = data.get('category', 'custom')
    action = data.get('action')
    applies_to = data.get('applies_to', {})

    if not name or not isinstance(name, str):
        return {"success": False, "error": "name is required (string)"}
    if not isinstance(retention_days, int) or retention_days < 1:
        return {"success": False, "error": "retention_days must be a positive integer"}

    valid_actions = ("archive", "tombstone", "notify")
    if action not in valid_actions:
        return {"success": False, "error": f"action must be one of: {valid_actions}"}

    try:
        profile = get_active_profile()
        with memory_write(DB_PATH) as conn:
            engine = RetentionEngine(conn)
            rule_id = engine.create_rule(
                name=name, framework=framework,
                retention_days=retention_days, action=action,
                applies_to=applies_to, profile_id=profile,
            )

        return {
            "success": True, "rule_id": rule_id,
            "active_profile": profile,
            "message": f"Retention policy '{name}' created ({retention_days}d, {action})",
        }
    except Exception:
        logger.exception("create_retention_policy error")
        return {"success": False, "error": "Internal server error"}


@router.delete("/api/compliance/retention-policy")
async def delete_retention_policy(request: Request, name: str = Query(...)):
    """Delete a retention policy by name for the active profile."""
    # A retention rule decides what is kept and for how long, so changing one
    # is a persistent configuration change on someone else's data. It is gated
    # on the same loopback-trusted boundary the audit read uses, and on MANAGE
    # in a workspace that has roles -- it had neither.
    from superlocalmemory.server.write_identity import require_http_mutation_actor

    require_http_mutation_actor(
        request, getattr(request.app.state, "daemon_descriptor", None),
        actor_kind="retention-policy",
    )
    _require_manage(request)
    if not COMPLIANCE_AVAILABLE:
        return {"success": False, "error": "Compliance engine not available"}
    try:
        profile = get_active_profile()
        with memory_write(DB_PATH) as conn:
            engine = RetentionEngine(conn)
            removed = engine.delete_rule(profile, name)
        if not removed:
            return {"success": False, "error": f"Policy '{name}' not found"}
        return {"success": True, "active_profile": profile,
                "message": f"Retention policy '{name}' deleted"}
    except Exception:
        logger.exception("delete_retention_policy error")
        return {"success": False, "error": "Internal server error"}


@router.post("/api/compliance/retention/enforce")
async def enforce_retention(request: Request):
    """Run all retention policies for the active profile now.

    Moves expired facts to their rule's terminal lifecycle zone (archive/
    tombstone) or counts them (notify). Soft-state only — never a raw delete.
    """
    # A retention rule decides what is kept and for how long, so changing one
    # is a persistent configuration change on someone else's data. It is gated
    # on the same loopback-trusted boundary the audit read uses, and on MANAGE
    # in a workspace that has roles -- it had neither.
    from superlocalmemory.server.write_identity import require_http_mutation_actor

    require_http_mutation_actor(
        request, getattr(request.app.state, "daemon_descriptor", None),
        actor_kind="retention-policy",
    )
    _require_manage(request)
    if not COMPLIANCE_AVAILABLE:
        return {"success": False, "error": "Compliance engine not available"}
    try:
        profile = get_active_profile()
        with memory_write(DB_PATH) as conn:
            engine = RetentionEngine(conn)
            result = engine.enforce(profile)
        return {"success": True, **result}
    except Exception:
        logger.exception("enforce_retention error")
        return {"success": False, "error": "Internal server error"}


# ── GDPR Art. 15/20 — Right to Access / Portability ─────────────────────────

@router.get("/api/compliance/gdpr/export")
async def gdpr_export(
    request: Request,
    include_embeddings: bool = Query(
        False, description="Include embeddings and other binary columns as base64"),
):
    """Export ALL data for the active profile (GDPR Art. 20 portability).

    Comprehensive export (memories, facts, entities, edges, trust, feedback,
    behavioral patterns, provenance, audit, media records, learned signals, …) as
    a downloadable JSON attachment. Read-only.

    The document is written to a temporary file and sent only when whole: an
    export that fails answers with an error status and sends no file, and the
    dashboard shows that error rather than a download that never came.
    """
    if not COMPLIANCE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Compliance engine not available")
    # A full-workspace data dump is an administrative action. Triggered by a
    # top-level navigation (a.href) that cannot carry a credential header, so
    # gate on the loopback-trusted mutation boundary (local owner allowed,
    # remote uncredentialed fails closed) AND require MANAGE — in company mode
    # a session cookie flows on navigation, so a non-admin user is still denied;
    # the machine owner keeps MANAGE.
    from superlocalmemory.server.rbac_enforce import require_manage
    from superlocalmemory.server.write_identity import require_http_mutation_actor
    require_http_mutation_actor(request, getattr(request.app.state, "daemon_descriptor", None),
                                actor_kind="gdpr-export")
    require_manage(request)
    engine = get_engine_lazy(request.app.state)
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    profile = get_active_profile()

    def write(fp) -> int:
        summary = GDPRCompliance(engine._db).write_profile_export(
            profile, fp, include_embeddings=include_embeddings)
        return summary["total_items"]

    return await export_response(
        write, media_type="application/json", filename=f"slm-export-{profile}.json")


# ── GDPR Art. 17 — Right to Erasure ─────────────────────────────────────────

@router.post("/api/compliance/gdpr/erase")
async def gdpr_erase(request: Request, data: dict = {}):
    """Permanently erase ALL data for the active profile (GDPR Art. 17).

    IRREVERSIBLE. Requires an explicit ``confirm`` field in the body that
    exactly matches the active profile name — this is the guard against an
    accidental one-click wipe. The 'default' profile can never be erased
    (enforced in GDPRCompliance.forget_profile). Mutation-authorized.
    """
    if not COMPLIANCE_AVAILABLE:
        return {"success": False, "error": "Compliance engine not available"}
    try:
        engine = get_engine_lazy(request.app.state)
        if engine is None:
            return {"success": False, "error": "Engine not initialized"}
        profile = get_active_profile()
        confirm = (data or {}).get("confirm", "")
        if confirm != profile:
            return {
                "success": False,
                "error": (
                    "Confirmation required: send {\"confirm\": \"" + profile +
                    "\"} to erase this profile. This is irreversible."
                ),
            }
        if profile == "default":
            return {"success": False,
                    "error": "The 'default' profile cannot be erased."}
        # Irreversible erasure is admin-only (beyond mutation auth).
        from superlocalmemory.server.rbac_enforce import require_manage
        require_manage(request, profile=profile)
        authorization = authorize_route_mutation(
            request,
            operation="delete",
            source_agent_id="http-gdpr-erase",
            profile_id=profile,
        )
        # Move the service off the profile BEFORE erasing it. Left active, every
        # request files the workspace row and its writes under that name again,
        # so the erased workspace came back at the next page load; and nothing
        # may be writing to a profile while it is erased.
        moved = await _leave_profile_to_erase(request, profile)
        if moved:
            return {"success": False, "error": moved}
        result = GDPRCompliance(engine._db, engine=engine).forget_profile(profile)
        authorization.complete()
        return _erasure_response("active_profile", profile, result)
    except HTTPException:
        # A refusal (401/403) is the answer, not a failure of the erase.
        raise
    except Exception:
        logger.exception("gdpr_erase error")
        return {"success": False, "error": "Internal server error"}


# ── GDPR Art. 17 — Entity-level Erasure ──────────────────────────────────────

@router.post("/api/compliance/gdpr/erase-entity")
async def gdpr_erase_entity(request: Request, data: dict = {}):
    """Erase all facts mentioning a named entity for the active profile.

    IRREVERSIBLE. Body must contain an ``entity_name`` and a ``confirm``
    field that exactly matches ``entity_name`` — same confirm-guard pattern
    as profile erasure. Mutation-authorized; requires MANAGE permission.
    """
    if not COMPLIANCE_AVAILABLE:
        return {"success": False, "error": "Compliance engine not available"}
    try:
        engine = get_engine_lazy(request.app.state)
        if engine is None:
            return {"success": False, "error": "Engine not initialized"}
        profile = get_active_profile()
        entity_name = ((data or {}).get("entity_name") or "").strip()
        confirm = (data or {}).get("confirm", "")
        if not entity_name:
            return {"success": False, "error": "entity_name is required"}
        if confirm != entity_name:
            return {
                "success": False,
                "error": (
                    "Confirmation required: send {\"confirm\": \"" + entity_name +
                    "\"} to erase this entity. This is irreversible."
                ),
            }
        from superlocalmemory.server.rbac_enforce import require_manage
        from superlocalmemory.server.write_identity import require_http_mutation_actor
        require_http_mutation_actor(
            request,
            getattr(request.app.state, "daemon_descriptor", None),
            actor_kind="gdpr-erase-entity",
        )
        require_manage(request, profile=profile)
        authorization = authorize_route_mutation(
            request,
            operation="delete",
            source_agent_id="http-gdpr-erase-entity",
            profile_id=profile,
        )
        result = GDPRCompliance(engine._db, engine=engine).forget_entity(entity_name, profile)
        authorization.complete()
        return _erasure_response("active_profile", profile, result, entity_name=entity_name)
    except HTTPException:
        # A refusal (401/403) is the answer, not a failure of the erase.
        raise
    except Exception:
        logger.exception("gdpr_erase_entity error")
        return {"success": False, "error": "Internal server error"}


# ── Erasure Receipts — list + cryptographic verify ───────────────────────────

@router.get("/api/compliance/receipts")
def list_erasure_receipts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    """List recent erasure receipts for the active profile.

    Returns summary rows from ``erasure_receipts`` (migration M035):
    erasure_id, subject_type, subject_id, state, all_erased, fact_count,
    requested_at, completed_at. Read-only; loopback-gated.
    """
    if not COMPLIANCE_AVAILABLE:
        return {"available": False, "error": "Compliance engine not available"}
    from superlocalmemory.server.write_identity import require_http_mutation_actor
    require_http_mutation_actor(
        request,
        getattr(request.app.state, "daemon_descriptor", None),
        actor_kind="erasure-receipts-read",
    )
    try:
        engine = get_engine_lazy(request.app.state)
        if engine is None:
            return {"available": False, "error": "Engine not initialized"}
        profile = get_active_profile()
        try:
            rows = engine._db.execute(
                "SELECT erasure_id, subject_type, subject_id, state, all_erased, "
                "fact_count, requested_at, completed_at "
                "FROM erasure_receipts WHERE profile_id = ? "
                "ORDER BY requested_at DESC LIMIT ?",
                (profile, limit),
            )
            receipts = [dict(r) for r in rows]
        except Exception:
            receipts = []
        return {
            "available": True, "active_profile": profile,
            "receipts": receipts, "total": len(receipts),
        }
    except Exception:
        logger.exception("list_erasure_receipts error")
        return {"available": False, "error": "Internal server error"}


@router.get("/api/compliance/receipts/{erasure_id}/verify")
def verify_erasure_receipt(request: Request, erasure_id: str):
    """Cryptographically verify an erasure receipt's audit hash.

    Returns ``{verified: bool}`` — True means the stored hash matches a
    recompute from the receipt fields (tamper-evident). Read-only;
    loopback-gated.
    """
    if not COMPLIANCE_AVAILABLE:
        return {"available": False, "error": "Compliance engine not available"}
    from superlocalmemory.server.write_identity import require_http_mutation_actor
    require_http_mutation_actor(
        request,
        getattr(request.app.state, "daemon_descriptor", None),
        actor_kind="erasure-receipt-verify",
    )
    try:
        engine = get_engine_lazy(request.app.state)
        if engine is None:
            return {"available": False, "error": "Engine not initialized"}
        profile = get_active_profile()
        from superlocalmemory.core.transactions.erasure import verify_receipt
        with engine._db.raw_connection() as conn:
            verified = verify_receipt(conn, erasure_id, profile_id=profile)
        return {"available": True, "erasure_id": erasure_id, "verified": verified}
    except Exception:
        logger.exception("verify_erasure_receipt error")
        return {"available": False, "error": "Internal server error"}
