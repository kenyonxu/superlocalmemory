# Compliance

SuperLocalMemory provides technical controls that may support regulatory programs. This page describes local storage, erasure, retention, access-policy, and audit capabilities; it is not legal advice or a certification of any deployment.

## EU AI Act (Regulation 2024/1689)

EU AI Act obligations depend on the deployed use case, operator role, risk classification, configuration, and surrounding systems.

### Mode A and B: local processing controls

Mode A can run the core memory path without a cloud model provider. Mode B can
add an operator-configured local Ollama model. Optional connectors, cloud
backup, proxy providers, remote embedding endpoints, dependency/model
downloads, and other integrations have separate network behavior.

| Requirement | Mode A | Mode B | Mode C |
|:------------|:------:|:------:|:------:|
| Data-location control | Deployment assessment required | Deployment assessment required | Provider and deployment assessment required |
| Right-to-erasure support | Local commands and propagation tests required | Local commands and propagation tests required | Local plus provider/connector procedures required |
| Transparency support | Trace, provenance, and audit controls available | Same, plus local-model assessment | Same, plus provider assessment |
| Core memory path without a cloud model provider | Available | Available with local Ollama | No |

Key compliance points for Mode A/B:

- **Data location:** Core state is local, but operators must inventory every
  enabled provider, connector, backup, download, and remote endpoint.
- **Transparency:** `slm trace` exposes recorded channel contributions and
  score fields. It is diagnostic evidence, not a legal explanation guarantee.
- **Risk classification:** Classification depends on the deployed use case and
  actor role; local storage alone does not determine it.

### Mode C: Partial Compliance

Mode C sends data to a cloud LLM provider. This means:

- Data leaves your device (transmitted to the provider's servers)
- You need a Data Processing Agreement (DPA) with your provider
- The cloud provider's compliance status affects your overall compliance
- Audit logs show which data was sent and when

**Recommendation:** prefer the smallest necessary data path, inventory optional providers and integrations, and obtain deployment-specific legal and security review.

## GDPR

### Right to Erasure (Article 17)

Delete memories matching a query:

```bash
slm forget "query matching memories to delete"
```

The command initiates deletion through the supported mutation path. A release
claim of complete erasure requires the lifecycle propagation matrix to cover
relational state, graph/index projections, cache, export, backup, optional
backends, and enabled external systems.

Do not remove only `memory.db` as a complete-erasure procedure. Configuration,
logs, queues, models, derived indexes, credentials, optional backend state, and
backups can live elsewhere in the data root. Use the documented export,
erasure, and uninstall procedures for the installed release.

### Right to Access (Article 15)

The database is a standard SQLite file at `~/.superlocalmemory/memory.db`. You can copy it, query it directly with any SQLite tool, or use the dashboard to browse all stored data:

```bash
slm dashboard    # Visual browser at http://localhost:8765
```

### Data Minimization (Article 5)

Automatic observations are admitted by explicit capture rules. Once accepted,
raw evidence and a queryable projection can be durable before enrichment runs;
entropy and consolidation are not an unconditional pre-storage guarantee.

### Data Portability (Article 20)

Core memory is SQLite-backed, but configuration, logs, queues, models, derived indexes, and optional backend state also live in the data root. Use the documented export or migration procedure rather than copying only one database file.

## Access Control

### Profile Isolation

Profiles provide default personal-memory separation inside one installation:

```bash
slm profile create client-a
slm profile switch client-a
```

Shared and global scopes are opt-in. Profiles are not an operating-system,
process, or tenant-security boundary; deployment isolation and the scope
matrix remain required for sensitive workloads.

### Trust Scoring

Every agent has a Bayesian trust score from 0.0 to 1.0. A new score starts at 0.5. A write is refused when the score is below 0.3, and a delete when it is below 0.5. Those limits are `SLM_TRUST_WRITE_THRESHOLD` and `SLM_TRUST_DELETE_THRESHOLD`. Reads stay open unless `SLM_TRUST_READ_GATE=1`; the read limit then defaults to 0.1 (`SLM_TRUST_READ_THRESHOLD`). The score moves when a signal is recorded, including a successful store, a recall hit, a confirmation, a contradiction or an access. An outcome report is a learning record and does not change this score. The dashboard shows the scores under Governance, on the Trust tab.

### Hash-chained audit trail

Audit records can be hash-chained so later modification is detectable during
verification. This is tamper-evidence, not tamper-proof storage, and coverage
must be checked for the deployed operation set.

```bash
slm dashboard    # Compliance tab shows audit trail
```

## HIPAA Considerations

SuperLocalMemory does not process Protected Health Information (PHI) by default. If you store PHI:

- Inventory and restrict every provider, connector, backup, and download path
- Use profile isolation for patient contexts
- Review audit logs regularly via dashboard

SuperLocalMemory does not provide BAA (Business Associate Agreement) coverage. Consult your compliance team before storing PHI.

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
