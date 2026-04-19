#!/usr/bin/env bash
# Stage-5b contract gate — enforces LLD-00 integration contracts at CI time.
#
# Per IMPLEMENTATION-MANIFEST-v3.4.21-FINAL.md §P0.1 and LLD-00 §13.
# Scans src/ for retired patterns and contract violations. Exit 0 = clean,
# exit 1 = violation found (specific failure printed to stdout).
#
# Uses POSIX grep for portability (see MANIFEST-DEVIATION entry for P0.1).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
SCAN_DIR="${SCAN_DIR:-src}"

FAIL=0
check() {
  local pattern="$1" msg="$2"
  [ -d "$SCAN_DIR" ] || return 0
  local matches
  matches="$(grep -rEn --include='*.py' --include='*.sql' \
       --include='*.js' --include='*.ts' --include='*.sh' --include='*.toml' \
       "$pattern" "$SCAN_DIR" 2>/dev/null || true)"
  if [ -n "$matches" ]; then
    echo "STAGE5B GATE FAILED: $msg"
    echo "  pattern: $pattern"
    echo "$matches" | sed 's/^/  /'
    FAIL=1
  fi
}

# LLD-00 §1.2 — pending_observations retired, use pending_outcomes (LLD-08 §M007).
check "pending_observations" \
  "LLD-00 §1.2 — pending_observations retired, use pending_outcomes"

# LLD-00 §2 — finalize_outcome takes outcome_id only (kwarg), not query_id.
check "finalize_outcome\(query_id" \
  "LLD-00 §2 — wrong finalize_outcome signature"

# LLD-00 §3 — hook must validate HMAC marker, not substring-scan fact_id.
check " fid in response_text" \
  "LLD-00 §3 — bare substring scan, use HMAC validator"

# MASTER-PLAN D2 + LLD-00 §5 — no Opus in any SLM-initiated LLM call.
check "claude-opus-4" \
  "LLD-00 + MASTER-PLAN D2 — no Opus in SLM-initiated LLM calls"

# LLD-00 §1.1 + SEC-C-05 — action_outcomes writes must go through canonical API.
check "action_outcomes.*INSERT.*VALUES" \
  "SEC-C-05 — every action_outcomes INSERT must include profile_id"

exit $FAIL
