# GDPR Compliance

SuperLocalMemory ships built-in controls that support GDPR compliance
programs. The wider picture is in [Governance](Governance) and [Compliance](Compliance).

These are engineering controls. Compliance depends on deployment configuration,
use case, and operator responsibility. SLM is not a legal certification.

## Controls summary

| Control | Where | CLI / Dashboard |
|---------|-------|-----------------|
| Data export (Art. 15/20) | JSON export of one profile. Embeddings are omitted unless you pass `--include-embeddings` | `slm gdpr export --profile PROFILE` · Dashboard → Governance → Compliance → Data privacy & GDPR |
| Erasure (Art. 17) | Profile erasure removes that profile's data. The `default` profile cannot be erased this way. There is no `slm profile delete` | `slm gdpr erase --profile PROFILE` (add `--yes` to run it; `--dry-run` previews). Dashboard → Governance → Compliance → Data privacy & GDPR. `slm forget` and `slm delete` remove individual memories, not a profile |
| Erasure audit log | The erasure event is written to the tamper-evident audit chain before any data is deleted | Dashboard → Governance → Compliance → Audit trail |
| Retention rules (Art. 5) | Time-based archive, tombstone or notify rules | Dashboard → Governance → Compliance |
| Audit trail (Art. 5(2)) | Hash-chained records of store, recall and delete, plus the erasure event written before a profile erase. Not every mutation is logged | Dashboard → Governance → Compliance → Audit trail |
| PII redaction | Redaction before memory content crosses a trust boundary. Off unless the enterprise deployment preset turns it on, or `SLM_PII_REDACTION` is set. `slm config set` has no key for it | Deployment preset or `SLM_PII_REDACTION` |

## Retention rules

A retention rule has a name, a number of days, a framework label (`custom` by default, or GDPR or HIPAA as labels), an action of `archive`, `tombstone` or `notify`, and an optional filter. Rules are managed in the dashboard's Governance pane and through the compliance API, and a background scheduler enforces them every hour. See [Governance](Governance).

## Erasure completeness

Erasing a profile deletes that profile's rows from every table in the memory
database that has a `profile_id` column, except the `profiles` table and the
erasure receipts. It also clears that profile from the context cache and from
the graph and vector projections. The fact-expansion search index is erased with the profile's facts, and write receipts are erased on their own path because a trigger blocks an ordinary delete. The in-database compliance audit rows for
the profile are deleted with the rest. The separate audit-chain file is
written before those deletes and is not part of the sweep.

`slm evidence export` is a different command. It writes a checksummed JSONL evidence bundle, not the Article 15 export. `slm diagnostics export` writes a content-free operational report. It does not verify an erasure.

After erasure, check the erasure receipt:

```bash
slm gdpr verify --receipt-id RECEIPT_ID
```

`slm gdpr verify --profile PROFILE` lists receipts when you do not pass a receipt id. A verified receipt means that receipt's HMAC is intact. It does not re-check the whole audit chain.

## Notes for operators

- RBAC must be configured (`require_login = true`) to enforce access boundaries
  in multi-user deployments.
- PII redaction is heuristic, not exhaustive. Verify coverage for your content.
- The tamper-evident audit chain is stored separately from the main database and
  is excluded from erasure operations by design.

## Related pages

- [Security, Company Mode and Teams](Security-Company-Mode-and-Teams)
- [Getting Started](Getting-Started)
