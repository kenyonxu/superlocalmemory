#!/usr/bin/env bash
# LLD-06 §5.1 — POSIX build script for slm-hook onedir binary.
# Runs AST generator → PyInstaller → smoke test.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "[build-slm-hook] repo: ${REPO_ROOT}"

PY="${SLM_BUILD_PYTHON:-python3}"

# 1. Ensure PyInstaller is present (pinned in pyproject optional-deps build-hook).
"${PY}" -m pip install --quiet --upgrade \
    "pyinstaller==6.15.0" "pyinstaller-hooks-contrib==2026.1"

# 2. AST-extract entry → src/superlocalmemory/hook_binary_entry.py
DEST="${REPO_ROOT}/src/superlocalmemory/hook_binary_entry.py"
"${PY}" "${SCRIPT_DIR}/build_entry.py" \
    --repo-root "${REPO_ROOT}" \
    --dest "${DEST}"
echo "[build-slm-hook] entry emitted: ${DEST}"

# 3. Run PyInstaller.
cd "${REPO_ROOT}"
"${PY}" -m PyInstaller \
    "${SCRIPT_DIR}/slm-hook.spec" \
    --clean --noconfirm \
    --distpath "${REPO_ROOT}/dist" \
    --workpath "${REPO_ROOT}/build/pyi"

BINARY="${REPO_ROOT}/dist/slm-hook/slm-hook"
if [ ! -x "${BINARY}" ]; then
    echo "[build-slm-hook] ERROR: binary not produced at ${BINARY}" >&2
    exit 2
fi

# 4. Smoke test: empty stdin → '{}' exit 0.
echo "[build-slm-hook] smoke test ..."
out=$(echo '' | "${BINARY}" || true)
if [ "${out}" != "{}" ]; then
    echo "[build-slm-hook] ERROR: expected '{}', got: ${out}" >&2
    exit 3
fi
echo "[build-slm-hook] OK"
