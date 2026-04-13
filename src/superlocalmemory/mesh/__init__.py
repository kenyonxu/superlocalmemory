# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SLM Mesh — Python port of the P2P agent communication broker.

Provides peer registry, message relay, shared state, file locks, and event logging
for multi-agent coordination. Runs as FastAPI sub-routes inside the unified daemon.

Independent broker — same wire protocol as standalone slm-mesh npm package,
but separate SQLite tables with mesh_ prefix.
"""
