#!/usr/bin/env bash
# SuperLocalMemory plugin installer for Antigravity's `agy` CLI.
#
# Claude Code and Codex both have a real one-liner install for this plugin:
#   claude plugin marketplace add qualixar/superlocalmemory && claude plugin install superlocalmemory@qualixar
#   codex plugin marketplace add qualixar/superlocalmemory --ref main && codex plugin add superlocalmemory-codex@qualixar
#
# `agy` (Google's Antigravity CLI -- a third-party compiled binary, not ours,
# not patchable) has no equivalent: there is no `agy plugin marketplace add
# <git-url>` (confirmed: `agy plugin marketplace` -> "unknown command: marketplace")
# and `agy plugin install` refuses anything that is not already a local
# directory (confirmed: `agy plugin install qualixar/superlocalmemory` ->
# "Error: install target must be a directory: qualixar/superlocalmemory").
# `agy plugin install <target>` is the only supported form.
#
# This script closes that gap: it fetches a shallow, sparse checkout of just
# antigravity-plugin/ into a stable local cache, then runs
# `agy plugin install` against that directory -- giving Antigravity users the
# same one-command experience. Safe to re-run: it always updates the cache to
# the latest ref first, and re-installing a plugin agy already knows by name
# is a clean no-op re-registration, not a duplicate or an error (verified
# against a real agy install on this machine).
#
# This script manages the local git cache only. It does not install agy
# itself, and it does not touch anything under an existing agy config beyond
# what `agy plugin install` itself does.

set -euo pipefail

readonly REPO_URL_DEFAULT="https://github.com/qualixar/superlocalmemory.git"
readonly PLUGIN_SUBDIR="antigravity-plugin"
readonly CACHE_DIR_DEFAULT="${HOME}/.cache/superlocalmemory/antigravity-plugin-src"

REPO_URL="${SLM_ANTIGRAVITY_REPO_URL:-$REPO_URL_DEFAULT}"
REF="${SLM_ANTIGRAVITY_REF:-main}"
CACHE_DIR="${SLM_ANTIGRAVITY_CACHE_DIR:-$CACHE_DIR_DEFAULT}"
DRY_RUN=false

usage() {
    cat <<'EOF'
Usage: install-antigravity-plugin.sh [OPTIONS]

Install (or update) the SuperLocalMemory plugin for Antigravity's `agy` CLI.

`agy plugin install` only accepts a local directory -- there is no remote or
marketplace install for it -- so this script fetches a shallow, sparse
checkout of just antigravity-plugin/ into a local cache and installs from
there. Re-running always refreshes the cache to the latest ref first, then
reinstalls; this is safe and does not create duplicate plugin entries.

Options:
  --ref REF          Git ref to install (default: main)
  --repo URL         Git URL to install from (default: qualixar/superlocalmemory)
  --cache-dir DIR    Local cache location
                      (default: ~/.cache/superlocalmemory/antigravity-plugin-src)
  --dry-run          Print the commands without running them
  --help, -h         Show this help

Environment overrides (same effect as the flags above):
  SLM_ANTIGRAVITY_REPO_URL, SLM_ANTIGRAVITY_REF, SLM_ANTIGRAVITY_CACHE_DIR
EOF
}

fail() {
    local message="$1"
    local status="${2:-1}"
    printf 'Error: %s\n' "$message" >&2
    exit "$status"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref)
            [[ $# -ge 2 ]] || fail "--ref requires a value"
            REF="$2"
            shift 2
            ;;
        --ref=*)
            REF="${1#--ref=}"
            shift
            ;;
        --repo)
            [[ $# -ge 2 ]] || fail "--repo requires a value"
            REPO_URL="$2"
            shift 2
            ;;
        --repo=*)
            REPO_URL="${1#--repo=}"
            shift
            ;;
        --cache-dir)
            [[ $# -ge 2 ]] || fail "--cache-dir requires a value"
            CACHE_DIR="$2"
            shift 2
            ;;
        --cache-dir=*)
            CACHE_DIR="${1#--cache-dir=}"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

# Fail loudly and specifically -- `agy plugin install` on a missing binary
# would otherwise surface as a confusing "command not found" with no context
# on what to do about it.
command -v agy >/dev/null 2>&1 || fail \
"agy is not on PATH. Install Antigravity (https://antigravity.google) first, \
then re-run this script. This script only wires the SuperLocalMemory plugin \
into an existing agy install -- it does not install agy itself." 127

command -v git >/dev/null 2>&1 || fail "git is required but not on PATH" 127

PLUGIN_PATH="${CACHE_DIR}/${PLUGIN_SUBDIR}"

run() {
    if [[ "$DRY_RUN" == true ]]; then
        printf 'Dry run:'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

sync_cache() {
    if [[ -d "$CACHE_DIR/.git" ]]; then
        local existing_url
        existing_url="$(git -C "$CACHE_DIR" remote get-url origin 2>/dev/null || true)"
        if [[ "$existing_url" != "$REPO_URL" ]]; then
            fail "$CACHE_DIR already exists and is not a checkout of $REPO_URL \
(found remote: ${existing_url:-none}). Remove it, or pass a different \
--cache-dir."
        fi
        printf 'Updating cached checkout at %s (ref: %s)...\n' "$CACHE_DIR" "$REF"
        run git -C "$CACHE_DIR" fetch --quiet --depth 1 --force origin "$REF"
        run git -C "$CACHE_DIR" reset --hard --quiet FETCH_HEAD
        # Idempotent even if a prior run used a different subdir/ref shape.
        run git -C "$CACHE_DIR" sparse-checkout set "$PLUGIN_SUBDIR"
    else
        [[ -e "$CACHE_DIR" ]] && fail \
"$CACHE_DIR already exists and is not a git checkout. Remove it, or pass a \
different --cache-dir."
        printf 'Cloning %s (ref: %s) into %s...\n' "$REPO_URL" "$REF" "$CACHE_DIR"
        run mkdir -p "$(dirname -- "$CACHE_DIR")"
        run git clone --quiet --filter=blob:none --sparse --depth 1 \
            --branch "$REF" "$REPO_URL" "$CACHE_DIR"
        run git -C "$CACHE_DIR" sparse-checkout set "$PLUGIN_SUBDIR"
    fi
}

sync_cache

if [[ "$DRY_RUN" == true ]]; then
    printf 'Dry run: agy plugin install %s\n' "$PLUGIN_PATH"
    exit 0
fi

[[ -f "$PLUGIN_PATH/plugin.json" ]] || fail \
"$PLUGIN_PATH/plugin.json is missing after checkout -- the sparse checkout of \
$PLUGIN_SUBDIR did not produce a valid plugin. Check --repo/--ref and retry."

printf 'Installing SuperLocalMemory plugin into agy from %s...\n' "$PLUGIN_PATH"
agy plugin install "$PLUGIN_PATH"

printf '\nDone. Verify with: agy plugin list\n'
