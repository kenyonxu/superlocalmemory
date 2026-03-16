# Compliance

SuperLocalMemory is designed for regulatory compliance from the ground up. This page covers EU AI Act, GDPR, data retention, access control, and audit capabilities.

## EU AI Act (Regulation 2024/1689)

### Mode A: Full Compliance by Architecture

Mode A operates as a zero-LLM retrieval system. All memory operations — storage, encoding, retrieval, and lifecycle management — execute locally without any cloud AI service dependency.

This architecture naturally satisfies data sovereignty requirements under the EU AI Act by ensuring personal data never leaves the user's device during memory operations.

Key compliance points:

- **Data sovereignty:** Mode A data never leaves the device (Article 10 data governance)
- **Transparency:** All retrieval decisions are auditable — vector similarity, keyword matching, graph traversal. No black-box LLM decisions.
- **Risk classification:** Local retrieval is minimal risk. No AI system makes autonomous decisions.
- **Right to explanation:** You can trace exactly why a memory was recalled using `slm recall --trace`

### Mode B: Full Compliance (Local LLM)

Mode B uses a local LLM via Ollama. All data stays on-device. The same compliance properties as Mode A apply.

### Mode C: Partial Compliance

Mode C sends data to a cloud LLM provider. This means:

- Data leaves your device (transmitted to the provider's servers)
- You need a Data Processing Agreement (DPA) with your provider
- The cloud provider's compliance status affects your overall compliance
- Audit logs show which data was sent and when

**Recommendation:** Use Mode A or B for EU AI Act-regulated environments. Use Mode C only where cloud AI is explicitly approved by your organization.

## GDPR

### Right to Erasure (Article 17)

Delete all memories for a user or context:

```bash
slm erasure --user <identifier>
```

This permanently removes all matching memories, graph connections, and metadata. Because data is stored locally, there are no cloud logs to purge — deletion is immediate and complete.

### Right to Access (Article 15)

Export all stored data:

```bash
slm export > my-data.json
```

The export includes all memories, metadata, profiles, and graph data in JSON format.

### Data Minimization (Article 5)

Configure retention policies to automatically delete old memories:

```bash
slm retention set --days 365
```

Memories older than the retention period are automatically purged.

### Data Portability (Article 20)

The export command produces standard JSON that can be imported into other systems.

## Retention Policies

Set global retention:

```bash
slm retention set --days 365
```

Set per-category retention:

```bash
slm retention set --category decisions --days 730
slm retention set --category debug --days 90
```

View current policy:

```bash
slm retention
```

Memories past their retention date are automatically removed during the lifecycle management cycle.

## Access Control

### Profile Isolation

Profiles provide complete data isolation:

```bash
slm profile create client-a
slm profile switch client-a
```

Memories in `client-a` are invisible to other profiles. There is no cross-profile data leakage.

### Trust Scoring

Every agent that interacts with SuperLocalMemory has a trust score (0.0 to 1.0):

- Agents below 0.3 trust are blocked from write and delete operations
- Trust is updated based on outcome reports
- You can view trust scores: `slm status --trust`

### Rate Limiting

Built-in rate limiting prevents memory flooding from misbehaving agents. Configurable per-profile.

## Audit Trail

View the audit log:

```bash
slm audit
```

The audit trail records:
- Every memory stored (who, what, when)
- Every memory recalled (query, results, agent)
- Every deletion (what was deleted, by whom)
- Profile switches
- Mode changes
- Retention policy enforcement

Export the audit log:

```bash
slm audit --export > audit-log.json
```

## HIPAA Considerations

SuperLocalMemory does not process Protected Health Information (PHI) by default. If you store PHI:

- Use Mode A only (zero cloud)
- Enable strict retention policies
- Use profile isolation for patient contexts
- Review audit logs regularly

SuperLocalMemory does not provide BAA (Business Associate Agreement) coverage. Consult your compliance team before storing PHI.

---
*Part of [Qualixar](https://qualixar.com) | Created by [Varun Pratap Bhardwaj](https://varunpratap.com)*
