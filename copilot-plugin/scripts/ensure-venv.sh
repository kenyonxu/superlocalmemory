#!/usr/bin/env bash
# ensure-venv.sh — SuperLocalMemory plugin venv bootstrap
#
# Called by Claude Code SessionStart hook. Idempotent: fast-path exits in <100ms
# on repeat invocations when requirements.txt is unchanged.
#
# Environment (set by Claude Code plugin runtime):
#   CLAUDE_PLUGIN_ROOT  — plugin installation dir (ephemeral, contains scripts/ + requirements.txt)
#   CLAUDE_PLUGIN_DATA  — persistent data dir (venv lives here, survives plugin updates)
#
# Exit codes: 0 = venv ready   non-0 = failure (logged to stderr)
# All output goes to stderr only (stdout reserved for MCP stdio protocol).

set -euo pipefail

# ---------------------------------------------------------------------------
# :? guard — fail loudly if required env vars are unset or empty
# ---------------------------------------------------------------------------
: "${CLAUDE_PLUGIN_ROOT:?CLAUDE_PLUGIN_ROOT must be set (plugin installation directory)}"
: "${CLAUDE_PLUGIN_DATA:?CLAUDE_PLUGIN_DATA must be set (plugin persistent data directory)}"

# ---------------------------------------------------------------------------
# Redirect all output to stderr (MCP uses stdout for protocol messages)
# ---------------------------------------------------------------------------
exec 1>&2

# ---------------------------------------------------------------------------
# Python >= 3.11 + ensurepip resolver
# Debian/Ubuntu splits ensurepip into python3.x-venv, so a bare `python3`
# may exist but be unable to create venvs. SLM_VENV_PYTHON overrides discovery.
# ---------------------------------------------------------------------------
PY_BIN=""
for _cand in ${SLM_VENV_PYTHON:-} python3.14 python3.13 python3.12 python3.11 python3; do
    [ -n "${_cand}" ] || continue
    command -v "${_cand}" >/dev/null 2>&1 || continue
    if "${_cand}" -c "import sys, ensurepip; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
        PY_BIN="${_cand}"
        break
    fi
done
if [ -z "${PY_BIN}" ]; then
    PY_VER=$(python3 --version 2>&1 || echo "unknown")
    echo "ERROR: SuperLocalMemory plugin requires Python >= 3.11 with ensurepip (venv support); python3 is: ${PY_VER}" >&2
    echo "Install python3-venv (Debian/Ubuntu) or set SLM_VENV_PYTHON to a suitable interpreter." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REQ="${CLAUDE_PLUGIN_ROOT}/requirements.txt"
VENV="${CLAUDE_PLUGIN_DATA}/venv"
SENTINEL="${CLAUDE_PLUGIN_DATA}/.venv-reqs.sha256"
VENV_TMP="${CLAUDE_PLUGIN_DATA}/venv.tmp"

# ---------------------------------------------------------------------------
# Compute sha256 of requirements.txt (cross-platform: prefer sha256sum, fall back to shasum -a 256)
# ---------------------------------------------------------------------------
hash_req() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${REQ}" | awk '{print $1}'
    else
        shasum -a 256 "${REQ}" | awk '{print $1}'
    fi
}

NEW_HASH=$(hash_req)

# ---------------------------------------------------------------------------
# Fast-path: venv python exists AND sentinel matches current requirements hash
# venv/bin/python3 (or python) is always present after `python3 -m venv`;
# the sentinel guards against stale requirements.
# In production, superlocalmemory also installs venv/bin/slm; both checks pass.
# ---------------------------------------------------------------------------
VENV_PYTHON="${VENV}/bin/python3"
if [ ! -x "${VENV_PYTHON}" ]; then
    VENV_PYTHON="${VENV}/bin/python"
fi
if [ -x "${VENV_PYTHON}" ] && [ -f "${SENTINEL}" ] && [ "$(cat "${SENTINEL}")" = "${NEW_HASH}" ]; then
    echo "SLM plugin: venv up-to-date (sha256=${NEW_HASH:0:12}…), skipping install." >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Rebuild venv atomically: install to venv.tmp, rename to venv, write sentinel LAST
# ---------------------------------------------------------------------------
echo "SLM plugin: bootstrapping Python venv at ${VENV} …" >&2
echo "  requirements: ${REQ}" >&2
echo "  python: ${PY_BIN} ($(${PY_BIN} --version 2>&1))" >&2

# Clean up any partial previous attempt
rm -rf "${VENV_TMP}"

# Create fresh venv
"${PY_BIN}" -m venv "${VENV_TMP}"

# Upgrade pip first (prefer binary to avoid source builds)
"${VENV_TMP}/bin/pip" install --upgrade pip --prefer-binary --quiet

# Install requirements
"${VENV_TMP}/bin/pip" install \
    --require-virtualenv \
    --prefer-binary \
    --quiet \
    -r "${REQ}"

echo "SLM plugin: install complete, activating venv." >&2

# Atomic swap: remove old venv (if any), rename tmp into place
rm -rf "${VENV}"
mv "${VENV_TMP}" "${VENV}"

# Write sentinel LAST — guarantees that a crash before this line triggers rebuild
echo "${NEW_HASH}" > "${SENTINEL}"

echo "SLM plugin: venv ready at ${VENV}/bin/slm" >&2
