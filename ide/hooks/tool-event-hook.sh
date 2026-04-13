#!/usr/bin/env bash
# SuperLocalMemory V3.4.7 — Tool Event Learning Hook
# Copyright (c) 2026 Varun Pratap Bhardwaj
# Licensed under AGPL-3.0-or-later
#
# PostToolUse hook that logs tool events to SLM for behavioral learning.
# Fires after every tool call. Lightweight — no LLM calls, just HTTP POST.
# All data stays 100% local.
#
# Installation: slm init (auto-registers) or manually add to settings.json:
#   { "type": "PostToolUse", "matcher": "*",
#     "command": "bash /path/to/tool-event-hook.sh",
#     "timeout": 5000 }

set -euo pipefail

# Read stdin (Claude Code passes JSON with tool_name, input, output)
INPUT=$(cat)

# Extract tool name and event info
TOOL_NAME=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_name', d.get('tool', 'unknown')))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")

# Skip logging for our own tools (avoid recursion)
case "$TOOL_NAME" in
    log_tool_event|get_assertions|reinforce_assertion|contradict_assertion)
        exit 0
        ;;
esac

# Get daemon port
PORT=8765
PORT_FILE="$HOME/.superlocalmemory/daemon.port"
[ -f "$PORT_FILE" ] && PORT=$(cat "$PORT_FILE" 2>/dev/null || echo 8765)

# Log event to SLM daemon (fire and forget, 2s timeout)
curl -s -m 2 -X POST "http://127.0.0.1:${PORT}/api/v3/tool-event" \
    -H "Content-Type: application/json" \
    -d "{\"tool_name\": \"${TOOL_NAME}\", \"event_type\": \"complete\"}" \
    >/dev/null 2>&1 || true
