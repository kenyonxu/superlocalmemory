# Governance

Governance is the part of SuperLocalMemory that makes it a memory system you can run under rules, instead of a vector store you hope behaves. It decides who may write, what may be read across profiles, how memories age, how trust in an agent is earned and lost, how data is retained or erased, and how every one of those decisions is recorded in a log that can be checked for tampering. The design is described in the paper [SuperLocalMemory 4.0: The Governed Memory Operating System for AI Agents](https://arxiv.org/abs/2608.08253) (arXiv:2608.08253), which covers multi-scope isolation, role-based access, verified erasure, hash-chained audit and bi-temporal recall, with their measured cost.

In the dashboard, governance is one pane with five tabs: **Lifecycle**, **Access & Users**, **Trust**, **Compliance** and **Ingestion**. Nothing in the pane is read-only; each tab is also reachable from the CLI and the HTTP API.

## A note on the word "compliant"

SLM ships the controls that a GDPR programme needs: export of a profile's data, erasure with a receipt that can be verified, retention rules, an audit trail and access control. Whether a particular deployment is GDPR compliant also depends on how it is configured and operated, which is why the project documentation states that these controls are not legal advice or certification. The EU AI Act report in the Compliance tab says the same thing about its own output: the operating mode alone cannot establish a risk classification. See [Privacy and Data Flow](Privacy-and-Data-Flow) for what can leave the machine, feature by feature.

## Lifecycle

Every memory sits in one of four states: `active`, `warm`, `cold` or `archived`. By age and access the thresholds are 7, 30 and 90 days after last access: active for 7 days, warm to 30, cold to 90, then archived. A memory read more than ten times stays active. When the Langevin dynamics layer is on, state comes from a memory's position instead, with weight cut-offs of 0.7, 0.4 and 0.1. The dashboard also shows a `tombstoned` bucket for memories removed by retention rules.

Forgetting is a separate, scheduled process based on Ebbinghaus-style decay. It is on by default, recomputes retention every 30 minutes, archives a memory when its retention falls below 0.2 and marks it forgotten below 0.05. Why decay rather than a fixed expiry: a memory that keeps being recalled should stay strong, and one that never is should fade, without anyone setting a date. `POST /api/lifecycle/compact` applies the computed zones; it is a dry run unless you execute it, and executing needs mutation authorisation. The mechanics behind decay and quantization are in [Living Brain](Living-Brain).

## Access and users

Profiles partition a workspace, and each memory is `personal`, `shared` with named profiles, or `global`. Reads across profiles are default-deny. In company mode, administration needs a signed-in admin session, and roles gate every write, share and erase. The roles, the identities (install token, API key, daemon capability, dashboard session) and the decision order are in [Security, Company Mode and Teams](Security-Company-Mode-and-Teams).

## Trust

SLM keeps a Bayesian trust score for each agent and each fact, modelled as a Beta distribution. A new agent starts at 0.5. Events move the score:

| Signal | Effect |
|---|---|
| Store succeeded | +1 to the positive count |
| Store rejected | +2 to the negative count |
| Recalled and used (hit) | +0.5 to the positive count |
| Contradiction found | +3 to the negative count |
| Deletion | +1.5 to the negative count |

Scores drift back toward the prior at 5 percent per day after 30 idle days, so old reputation does not last forever. Facts inherit their source agent's trust, reduced by contradictions, and provenance is recorded per fact. Burst detection flags a sudden flood of writes from one source.

The trust gate then acts on the score. A write needs trust of at least 0.3 and a delete at least 0.5. Reads pass and are logged, unless you set `SLM_TRUST_READ_GATE=1`, which adds a read threshold (0.1 by default). Both thresholds can be tuned with `SLM_TRUST_WRITE_THRESHOLD`, `SLM_TRUST_DELETE_THRESHOLD` and `SLM_TRUST_READ_THRESHOLD`. One limit to know: loopback callers, meaning programs on the same computer, bypass the gate, so it protects against remote, credentialed agents. Why the write default is 0.3 and not higher: a brand-new agent scores 0.5, and a stricter default would block every new agent. The research behind this is in [SuperLocalMemory: Privacy-Preserving Multi-Agent Memory with Bayesian Trust Defense Against Memory Poisoning](https://arxiv.org/abs/2603.02240).

## Compliance

**Audit chain.** Governed actions are recorded in a SHA-256 hash chain that lives in its own SQLite connection, so it survives damage to the main database. `verify_integrity()` detects tampering, and it also checks for truncation against an anchor kept beside the chain: a chain that has entries but a missing or unreadable anchor is reported as failing, with the reason "no anchor (missing|unreadable): truncation cannot be checked", and the dashboard badge is not green. An empty chain needs no anchor. You can read it from the dashboard, from `GET /api/compliance/audit`, or through the MCP `audit_trail` tool (power and whole profiles).

**Retention.** A retention rule has a name, a number of days, a framework label (`custom` by default, or GDPR or HIPAA as labels), an action of `archive`, `tombstone` or `notify`, and an optional filter on scope, fact type, signal type or session. A background scheduler enforces rules every hour, and you can enforce on demand with `POST /api/compliance/retention/enforce`.

**GDPR operations.** All four run per profile and are available from the CLI, the API and the dashboard:

| Operation | Command | What it does |
|---|---|---|
| Status | `slm gdpr status` | Shows the compliance state |
| Export | `slm gdpr export --profile NAME --output FILE` | Subject access and portability, including a records-only `media` section for pictures and documents |
| Erase | `slm gdpr erase --profile NAME --dry-run`, then `--yes` | Irreversibly erases a profile; entity-level erasure is in the dashboard and API, and `slm forget` and `slm delete` remove individual facts |
| Verify | `slm gdpr verify RECEIPT_ID` | Checks an erasure receipt: exit code 0 if valid, 1 if tampered, 2 if not found |

Erasure receipts are signed with an HMAC, so a receipt proves what was erased and cannot be edited afterwards. Erasure also scrubs derived caches and tracks backup residue as an obligation, because deleting from `memory.db` alone does not remove copies in indexes, caches or backups. The project's lifecycle-propagation matrix checks this across facts, full-text, graph, temporal, vector and cache projections.

**Erasure reaches the copies SLM made from your memory.** SLM builds several things out of your memories: consolidated summaries, the persona that stitches community summaries together, core memory blocks, the soft prompts learned from those blocks, scene titles, the text shown for an entity in the Entity Explorer, and the context loaded at the start of a session. Each of these can quote a memory in words. So the moment an erasure starts, a marker is written for the memory, and from then on every reader withholds the memory and anything derived from it that quotes it: recall, the keyword and time channels, the local picture channel, exports, the dashboard's summaries and entity views, and the command-line context. The derived copies that SLM can rebuild are deleted at that moment (the summaries that quote the memory, the persona built on a community that quotes it, and the soft prompts built from a block that quotes it) and are rebuilt later from what remains. A scene title is the first 200 characters of its first memory, so when that memory is erased the title is recomputed from the first remaining member, or left blank if there is none. When the erasure completes, the scrub repeats the same steps, so no copy keeps the words after the original is gone. If SLM cannot prove a derived copy is clean, it withholds it rather than showing it.

**Other controls.** ABAC deny-list policies can block read, write, delete or admin actions per agent. PII redaction on ingest is opt-in with `SLM_PII_REDACTION`, or comes on with the enterprise deployment preset. Secrets are stripped from derived text regardless.

## Ingestion

The fifth tab shows the ingestion adapters (Gmail, Calendar and meeting transcripts) and a log of recent ingestion. See [Ingestion Adapters](Ingestion-Adapters).

Code: `compliance/` (`lifecycle.py`, `retention.py`, `gdpr.py`, `audit.py`, `abac.py`, `eu_ai_act.py`, `scheduler.py`), `trust/` (`scorer.py`, `gate.py`, `provenance.py`, `signals.py`), `learning/forgetting_scheduler.py`, `server/routes/compliance.py`, `server/routes/lifecycle.py`, `cli/main.py` (the `gdpr` command).
