# Compliance

SuperLocalMemory is designed for regulatory compliance from the ground up. This page covers EU AI Act, GDPR, data retention, access control, and audit capabilities.

## EU AI Act (Regulation 2024/1689)

The EU AI Act takes full effect **August 2, 2026**. To the best of our knowledge, no existing agent memory system addresses EU AI Act compliance.

### Mode A & B: Full Compliance by Architecture

Mode A operates as a zero-LLM retrieval system. Mode B adds a local LLM via Ollama. In both modes, **all memory operations — storage, encoding, retrieval, and lifecycle management — execute locally without any cloud dependency.**

| Requirement | Mode A | Mode B | Mode C |
|:------------|:------:|:------:|:------:|
| Data sovereignty (Art. 10) | **Pass** | **Pass** | Requires DPA |
| Right to erasure (GDPR Art. 17) | **Pass** | **Pass** | **Pass** |
| Transparency (Art. 13) | **Pass** | **Pass** | **Pass** |
| No network calls during memory ops | **Yes** | **Yes** | No |

Key compliance points for Mode A/B:

- **Data sovereignty:** No personal data leaves the device during any memory operation (Article 10 data governance)
- **Transparency:** All retrieval decisions are auditable — vector similarity, keyword matching, graph traversal. No black-box LLM decisions.
- **Risk classification:** Local retrieval is minimal risk. No AI system makes autonomous decisions.
- **Right to explanation:** You can trace exactly why a memory was recalled using `slm trace "query"`

### Mode C: Partial Compliance

Mode C sends data to a cloud LLM provider. This means:

- Data leaves your device (transmitted to the provider's servers)
- You need a Data Processing Agreement (DPA) with your provider
- The cloud provider's compliance status affects your overall compliance
- Audit logs show which data was sent and when

**Recommendation:** Use Mode A or B for EU AI Act-regulated environments. Use Mode C only where cloud AI is explicitly approved by your organization.

## GDPR

### Right to Erasure (Article 17)

Delete memories matching a query:

```bash
slm forget "query matching memories to delete"
```

This permanently removes all matching memories, graph connections, and metadata. Because data is stored locally, there are no cloud logs to purge — deletion is immediate and complete.

To delete everything, remove the database:

```bash
rm ~/.superlocalmemory/memory.db
```

### Right to Access (Article 15)

The database is a standard SQLite file at `~/.superlocalmemory/memory.db`. You can copy it, query it directly with any SQLite tool, or use the dashboard to browse all stored data:

```bash
slm dashboard    # Visual browser at http://localhost:8765
```

### Data Minimization (Article 5)

The entropy gate automatically filters out low-information messages during auto-capture. Only structured, high-value facts are stored — not raw conversation dumps.

### Data Portability (Article 20)

The SQLite database is a standard, portable format. It can be copied to any machine and used immediately.

## Access Control

### Profile Isolation

Profiles provide complete data isolation:

```bash
slm profile create client-a
slm profile switch client-a
```

Memories in `client-a` are invisible to other profiles. There is no cross-profile data leakage.

### Trust Scoring

Every agent that interacts with SuperLocalMemory has a Bayesian trust score (0.0 to 1.0):

- Agents below the trust threshold are blocked from write and delete operations
- Trust is updated based on outcome reports
- View trust scores via the dashboard (Trust tab)

### Tamper-Proof Audit Trail

All operations (store, recall, delete) are logged in a SHA-256 hash-chain audit trail. Each entry references the previous entry's hash — any tampering breaks the chain.

```bash
slm dashboard    # Compliance tab shows audit trail
```

## HIPAA Considerations

SuperLocalMemory does not process Protected Health Information (PHI) by default. If you store PHI:

- Use Mode A only (zero cloud)
- Use profile isolation for patient contexts
- Review audit logs regularly via dashboard

SuperLocalMemory does not provide BAA (Business Associate Agreement) coverage. Consult your compliance team before storing PHI.

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
