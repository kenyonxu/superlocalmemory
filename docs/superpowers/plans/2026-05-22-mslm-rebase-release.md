# MSLM Rebase & Independent Release — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebase fork onto upstream v3.4.56, squash 101 commits into 6 functional groups, establish `mslm` as independent package on PyPI/npm with full backward compatibility.

**Architecture:** Two-branch model — `main` (6 squash commits on top of upstream/main, tagged, for release) and `develop` (full 101-commit history, for ongoing development + upstream rebase). Internal Python paths, data dir, and env vars unchanged; only PyPI/npm package names and CLI command change.

**Tech Stack:** Python 3.11+, npm (thin wrapper), bash, git

**Spec:** docs/superpowers/specs/2026-05-22-mslm-rebase-release-design.md

---

## Chunk 1: Git Repository Restructure

### Task 1: Verify prerequisites and fetch upstream

**Files:**
- None (git operations only)

- [ ] **Step 1: Verify clean working tree**

```bash
git status
```
Expected: clean, on main branch.

- [ ] **Step 2: Ensure upstream remote exists**

```bash
git remote add upstream https://github.com/qualixar/superlocalmemory.git 2>/dev/null || true
git fetch upstream
git log upstream/main --oneline -3
```
Expected: shows latest upstream commits including v3.4.56.

- [ ] **Step 3: Record current HEAD for rollback safety**

```bash
git rev-parse HEAD > /tmp/mslm-rebase-previous-head
```

- [ ] **Step 4: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: pre-rebase checkpoint" || echo "Nothing to commit"
```

### Task 2: Create develop branch and rebase onto upstream/main

**Files:**
- None (git operations only)

- [ ] **Step 1: Create develop branch from current main**

```bash
git branch develop main
```
Expected: branch created, still on main.

- [ ] **Step 2: Switch to develop and rebase**

```bash
git checkout develop
git rebase upstream/main
```
Expected: rebase begins. Resolve conflicts as they arise. See conflict strategy below.

**Conflict resolution per file:**

| File | Strategy |
|------|----------|
| `engine.py` | Keep upstream parallel channels/FSRS; merge our scope/backfill into correct functions |
| `recall_pipeline.py` | Keep upstream FSRS decay formula; merge our scope filtering into `recall()` |
| `unified_daemon.py` | Keep upstream pre-warm/JSON sanitize; merge our materializer/health/engine-recovery |
| `engine_wiring.py` | Keep upstream Ollama priority; merge our ScopeWeights/ProxyConfig |
| `retrieval/engine.py` | Keep upstream perf tuning; merge our scope weights/RRF |
| `spreading_activation.py` | Keep upstream fan-out reduction; merge our scope-aware activation |
| `tools_active.py` | Keep upstream emergency FTS5/age-gate; merge our scope filtering |
| `commands.py` | Keep upstream mode switch; merge our entity/scope commands |
| Others (`__init__.py`, `cli/daemon.py`, etc.) | Combine both changes (trivial) |

For each merge conflict:
```bash
# After resolving:
git add <resolved-file>
git rebase --continue
```

If a conflict is too complex, abort and reassess:
```bash
git rebase --abort  # only as last resort
```

- [ ] **Step 3: Verify rebase completed**

```bash
git log --oneline -5
```
Expected: upstream commits at bottom, our commits atop, no merge commits.

### Task 3: Verify tests pass on develop

**Files:**
- None (test verification only)

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -q --tb=short 2>&1 | tail -30
```
Expected: all passing (some skips for slow/ollama markers are OK).

- [ ] **Step 2: Run functional smoke test**

```bash
slm health
slm status
```
Expected: daemon responds, engine initialized, status shows version.

- [ ] **Step 3: Commit test verification checkpoint**

```bash
git add -A
git commit -m "chore: rebase verification — all tests passing" || echo "Nothing to commit"
```

### Task 4: Identify commit ranges for 6 squash groups

**Files:**
- None (analysis only)

- [ ] **Step 1: Map commits to functional groups**

Identify the exact commit ranges for each group by reviewing the log:

```bash
git log upstream/main..develop --oneline
```

Group definitions (SHA ranges to be determined from log output):

| # | Group | Description | Key Files |
|---|-------|-------------|-----------|
| 1 | **core infrastructure** | scope columns, _scope_where(), engine/pipeline/7channels scope, MCP tools, M014, ScopeWeights, cross-agent fix | `storage/schema.py`, `storage/database.py`, `storage/migrations/M014_*`, `storage/models.py`, `core/engine.py`, `core/recall_pipeline.py`, `core/store_pipeline.py`, `retrieval/*.py`, `mcp/tools_core.py`, `core/config.py` |
| 2 | **domain tags** | domain_mapping, M015, skill_tags, LLM classifier, add/remove_domain_mapping | `storage/schema.py`, `storage/migrations/M015_*`, `storage/seed_domain_mapping.py`, `core/store_pipeline.py`, `retrieval/*.py`, `mcp/tools_core.py`, `encoding/entity_resolver.py` |
| 3 | **global entities** | Phase 3 global-first entity resolution, cross-scope merge | `encoding/entity_resolver.py`, `storage/database.py`, `mcp/tools_core.py` |
| 4 | **scope-e2e wiring** | CLI/Dashboard/materializer scope, entity merge, scope weights, scope-r2 | `cli/commands.py`, `server/unified_daemon.py`, `server/routes/*.py`, `core/config.py`, `retrieval/engine.py`, `tests/test_scope_*.py` |
| 5 | **daemon reliability** | health recovery, zombie reaper, backfill, materializer extraction, get_engine cooldown | `server/unified_daemon.py`, `core/health_monitor.py`, `core/engine.py`, `core/maintenance.py`, `mcp/tools_active.py` |
| 6 | **proxy + environment** | ProxyConfig, proxy env vars, HF_ENDPOINT removal, WAL, logger.exception | `core/embeddings.py`, `core/engine_wiring.py`, `storage/database.py`, `server/unified_daemon.py` |

- [ ] **Step 2: Record commit ranges**

```bash
# For each group, record first and last commit SHA
git log upstream/main..develop --oneline > /tmp/mslm-commit-list.txt
# Manually identify boundaries and save to /tmp/mslm-group-boundaries.txt
```

### Task 5: Build clean main branch with 7 squash commits

**Note:** The spec references 6 functional groups; the plan produces 7 commits (6 functional + 1 docs/cleanup).

**Files:**
- None (git operations, squash commits only)

- [ ] **Step 1: Create new-main from upstream/main**

```bash
git checkout -b new-main upstream/main
```

- [ ] **Step 2: Apply Group 1 — core infrastructure**

```bash
git diff --binary --full-index upstream/main..<group-1-last-sha> -- . > /tmp/mslm-group1.patch
git apply --check /tmp/mslm-group1.patch   # dry-run first
git apply --index /tmp/mslm-group1.patch
git commit -m "$(cat <<'EOF'
feat(multiscope): core infrastructure

Add scope/shared_with columns to core tables with M014 migration.
Implement scope-aware _scope_where() in DatabaseManager for
personal/global/shared filtering. Thread scope parameter through
MemoryEngine, RecallPipeline, StorePipeline, and all 7 retrieval
channels. Add scope support to MCP tools and WorkerPool IPC.
Add ScopeWeights config for configurable multi-scope RRF weights.
Fix cross-agent global recall scope filtering across all channels.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Apply Group 2 — domain tags**

```bash
git diff <group-1-last-sha>..<group-2-last-sha> -- . > /tmp/mslm-group2.patch
git apply --index /tmp/mslm-group2.patch
git commit -m "$(cat <<'EOF'
feat(multiscope): domain tags

Add domain_mapping table and domain_tags column with M015 migration.
Implement skill_tags property on Profile for domain-aware retrieval.
Wire skill_tags through SLMConfig, RetrievalEngine, and all retrieval
channels. Add domain-aware _scope_where() filtering with tag overlap
matching. Add add_domain_mapping and remove_domain_mapping MCP tools.
Add LLM-based domain classification fallback for unmapped entities.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Apply Group 3 — global entities**

```bash
git diff <group-2-last-sha>..<group-3-last-sha> -- . > /tmp/mslm-group3.patch
git apply --index /tmp/mslm-group3.patch
git commit -m "$(cat <<'EOF'
feat(multiscope): global authoritative entities

Implement global-first entity resolution with Tier 0 global lookup.
Add default scope configuration for cross-scope entity aliasing
and fuzzy matching. Support entity consolidation across scopes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Apply Group 4 — scope-e2e wiring**

```bash
git diff <group-3-last-sha>..<group-4-last-sha> -- . > /tmp/mslm-group4.patch
git apply --index /tmp/mslm-group4.patch
git commit -m "$(cat <<'EOF'
feat(multiscope): scope-e2e wiring

Wire scope/shared_with through CLI (--scope/--shared-with args),
Dashboard /api/import, and daemon materializer. Add entity merge
CLI and MCP tools. Add configurable ScopeWeights with persistence.
Defer recall scope-r2 for future implementation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Apply Group 5 — daemon reliability**

```bash
git diff <group-4-last-sha>..<group-5-last-sha> -- . > /tmp/mslm-group5.patch
git apply --index /tmp/mslm-group5.patch
git commit -m "$(cat <<'EOF'
fix: daemon reliability improvements

Add engine recovery to /health endpoint with automatic restart.
Add zombie child process reaping in HealthMonitor._check_once().
Add entity and embedding backfill during scheduled maintenance.
Extract entities in daemon materializer for KG edge building.
Add 5-second failure cooldown to MCP get_engine().
Use logger.exception for engine init failure tracebacks.
Set busy_timeout before journal_mode=WAL for correct timeout.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Apply Group 6 — proxy + environment**

```bash
git diff <group-5-last-sha>..<group-6-last-sha> -- . > /tmp/mslm-group6.patch
git apply --index /tmp/mslm-group6.patch
git commit -m "$(cat <<'EOF'
fix: proxy and environment hardening

Add ProxyConfig for embedding worker network access through proxy.
Preserve http_proxy/https_proxy/all_proxy in embedding worker subprocess.
Remove HF_ENDPOINT from embedding worker environment to prevent
SSL issues with mirror endpoints. Enable daemon startup behind
corporate/regional firewalls.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Apply remaining docs/cleanup commits**

```bash
git diff <group-6-last-sha>..develop -- . > /tmp/mslm-group7.patch
git apply --index /tmp/mslm-group7.patch
git commit -m "$(cat <<'EOF'
docs: guides, strategy, and project documentation

Add Hermes Agent guide, multi-scope memory guides, import guide.
Add upstream contribution strategy and release plan.
Add getting-started guide with proxy, embed, MCP integration.
Add AGENTS.md project navigation handbook.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: Verify new-main has correct structure**

```bash
git log new-main --oneline
```
Expected: upstream/main → 7 commits (6 functional + docs), no merge commits.

- [ ] **Step 10: Run tests on new-main**

```bash
git checkout new-main
pytest tests/ -q --tb=short 2>&1 | tail -10
```
Expected: all passing.

### Task 6: Replace main with new-main and push

**Files:**
- None (git operations only)

- [ ] **Step 1: Verify backup and replace main with new-main**

```bash
# Verify backup target doesn't already exist
git branch -D old-main 2>/dev/null; true
git branch -M main old-main        # backup current main
git rev-parse old-main             # verify backup exists
git branch -M new-main main        # promote new-main to main
```

- [ ] **Step 2: Push both branches**

```bash
git push origin main --force       # force-push clean main
git push origin develop            # push develop with full history
```

- [ ] **Step 3: Verify remote state and cleanup**

```bash
git log origin/main --oneline -5
# Cleanup temp files and stale branches
rm -f /tmp/mslm-*.patch /tmp/mslm-*.txt
git branch -D old-main 2>/dev/null; true
git branch -D new-main 2>/dev/null; true
```
Expected: 7 squashed commits on top of upstream/main. Temp branches removed.

---

## Chunk 2: Package Configuration (PyPI)

### Task 7: Update pyproject.toml for mslm

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Change package name and version**

Edit `pyproject.toml`:
```toml
[project]
name = "mslm"
version = "4.0.0"
description = "Multi-Scope Local Memory — local-first AI agent memory with personal/global/shared scopes"
authors = [
    {name = "Kenyon Xu", email = "kenyon1977@gmail.com"},
]
```

- [ ] **Step 2: Add both CLI entry points**

```toml
[project.scripts]
mslm = "superlocalmemory.cli.main:main"
slm = "superlocalmemory.cli.main:main"
```

- [ ] **Step 3: Update classifiers and keywords**

Add classifier:
```toml
"Topic :: Scientific/Engineering :: Artificial Intelligence",
```

Update keywords to include "multi-scope", "mslm":
```toml
keywords = [
    "ai-memory", "mcp-server", "local-first", "agent-memory",
    "multi-scope", "information-geometry", "privacy-first",
]
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: rename package to mslm v4.0.0, add slm alias"
```

### Task 8: Verify CLI works

**Files:**
- None (verification only)

- [ ] **Step 1: Install in development mode**

```bash
pip install -e .
```
Expected: installs as `mslm`, package name confirmed.

- [ ] **Step 2: Test new CLI**

```bash
mslm status
mslm health
```
Expected: both work, show version 4.0.0.

- [ ] **Step 3: Test legacy CLI alias**

```bash
slm status
```
Expected: works identically.

- [ ] **Step 4: Test import path unchanged**

```bash
python -c "from superlocalmemory import __version__; print(__version__)"
```
Expected: prints 4.0.0.

- [ ] **Step 5: Commit verification**

```bash
git add -A
git commit -m "chore: verify mslm CLI and import path"
```

---

## Chunk 3: npm Wrapper

### Task 9: Update package.json for mslm

**Files:**
- Modify: `package.json`

- [ ] **Step 1: Update name and version**

```json
{
  "name": "mslm",
  "version": "4.0.0",
  "description": "Multi-Scope Local Memory — local-first AI agent memory with personal/global/shared scopes. 7-channel retrieval, Fisher-Rao similarity, multi-scope RRF fusion.",
  "keywords": [
    "ai-memory", "claude-ai", "cursor-ide", "local-first",
    "mcp-server", "ai-assistant", "memory-system",
    "multi-scope", "knowledge-graph", "privacy-first",
    "agent-memory"
  ],
```

- [ ] **Step 2: Update repository and homepage URLs**

```json
"repository": {
  "type": "git",
  "url": "https://github.com/kenyonxu/superlocalmemory.git"
},
"homepage": "https://github.com/kenyonxu/superlocalmemory#readme",
```

- [ ] **Step 3: Update bin entry**

```json
"bin": {
  "mslm": "./scripts/postinstall.js",
  "slm": "./scripts/postinstall.js"
}
```

- [ ] **Step 4: Commit**

```bash
git add package.json
git commit -m "chore: rename npm package to mslm v4.0.0"
```

### Task 10: Update postinstall script

**Files:**
- Modify: `scripts/postinstall.js`

- [ ] **Step 1: Update branding text**

Replace all `SuperLocalMemory` references with `Multi-Scope Local Memory (MSLM)`:
```javascript
console.log('\n════════════════════════════════════════════════════════════');
console.log('  Multi-Scope Local Memory (MSLM)');
console.log('  https://github.com/kenyonxu/superlocalmemory');
```

- [ ] **Step 2: Update pip install target**

Change `pip install superlocalmemory` to `pip install mslm` in the postinstall script.

- [ ] **Step 3: Commit**

```bash
git add scripts/postinstall.js
git commit -m "chore: update npm postinstall for mslm branding"
```

### Task 11: Update install scripts

**Files:**
- Modify: `scripts/install.sh`
- Modify: `scripts/install.ps1`

- [ ] **Step 1: Update install.sh**

```bash
# Replace all superlocalmemory references with mslm
sed -i 's/superlocalmemory/mslm/g' scripts/install.sh
# Verify
grep -n "mslm\|superlocalmemory" scripts/install.sh
```

- [ ] **Step 2: Update install.ps1**

```powershell
# Replace all superlocalmemory references with mslm
(Get-Content scripts/install.ps1) -replace 'superlocalmemory', 'mslm' | Set-Content scripts/install.ps1
```

- [ ] **Step 3: Commit**

```bash
git add scripts/install.sh scripts/install.ps1
git commit -m "chore: update install scripts for mslm"
```

---

## Chunk 4: Documentation & Finalize

### Task 12: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add fork notice at top**

```markdown
# Multi-Scope Local Memory (MSLM)

> **Fork of [SuperLocalMemory](https://github.com/qualixar/superlocalmemory) by Qualixar**
> MSLM adds multi-scope memory (personal/global/shared) while tracking upstream
> for performance and reliability improvements. Current upstream base: v3.4.56.

## What Makes MSLM Different

- **Three-scope architecture**: personal/global/shared memory with scope-aware retrieval
- **Domain tags**: skill-domain classification with configurable scope weights
- **Global entity resolution**: cross-scope entity merging with alias/fuzzy matching
- All upstream SLM features included (7-channel retrieval, Fisher-Rao similarity, etc.)
```

- [ ] **Step 2: Update install instructions**

```bash
# Replace pip install superlocalmemory with pip install mslm
# Replace slm commands with mslm commands
```

- [ ] **Step 3: Tag v4.0.0**

```bash
git tag -a v4.0.0 -m "MSLM v4.0.0 — initial independent release

Multi-Scope Local Memory based on upstream v3.4.56.
Adds personal/global/shared scope architecture with scope-aware
7-channel retrieval, domain tags, and global entity resolution."
```

- [ ] **Step 4: Push tag**

```bash
git push origin v4.0.0
```

- [ ] **Step 5: Final commit**

```bash
git add README.md
git commit -m "docs: update README for MSLM v4.0.0 release"
git push origin main
```

---

## Chunk 5: Local Migration (Current Machine)

### Task 13: Migrate local development install

**Files:**
- None (local install only)

- [ ] **Step 1: Switch to new main**

```bash
git fetch origin
git checkout origin/main -b main-release 2>/dev/null || git checkout main
git reset --hard origin/main
```

- [ ] **Step 2: Reinstall**

```bash
pip install -e .
```

- [ ] **Step 3: Verify**

```bash
mslm status
mslm health
slm status
python -c "from superlocalmemory import __version__; print(__version__)"
```
Expected: all work, version 4.0.0, data intact in ~/.superlocalmemory/.

---

## Rollback Plan

If something goes wrong:

```bash
# Restore original main from backup
git checkout -b main-restore $(cat /tmp/mslm-rebase-previous-head)
git branch -M main-restore main
git push origin main --force

# Restore pip package
pip install -e .
```

## Completion Checklist

- [ ] develop branch rebased onto upstream v3.4.56
- [ ] All tests passing on develop
- [ ] main branch has clean history (upstream + 7 squash commits)
- [ ] pyproject.toml updated: name=mslm, version=4.0.0, slm alias
- [ ] package.json updated: name=mslm, version=4.0.0
- [ ] scripts updated for mslm branding
- [ ] README updated with fork notice and scope features
- [ ] v4.0.0 tag created and pushed
- [ ] Local install working with `mslm status` and `slm status`
- [ ] `~/.superlocalmemory/` data intact and functional
