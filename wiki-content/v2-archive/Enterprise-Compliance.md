# Enterprise Compliance

SuperLocalMemory provides enterprise-grade compliance features: access control, immutable audit trails, and retention policy management.

## Access Control (ABAC)

Attribute-Based Access Control lets you define policies for who (or which agent) can read, write, or delete memories.

### Default Behavior
When no policies are configured, all access is permitted (backward compatible with previous versions).

### Setting Policies
Policies are JSON files in `~/.claude-memory/policies/`. Each policy specifies:
- **Subjects**: Who is requesting access (agent name, role)
- **Resources**: What they're accessing (project, category)
- **Actions**: What they can do (read, write, delete)
- **Effect**: Allow or deny

Deny policies always override allow policies.

## Audit Trail

Every significant action is logged to an immutable audit trail (`audit.db`):
- Memory creation, updates, deletions
- Access control decisions
- Retention policy actions
- Search operations

### Querying the Audit Trail
```
audit_trail action="recall" limit=50
```

Filter by action type, time range, or agent.

## Retention Policies

Configure how long different types of memories are retained:

```
set_retention_policy category="credentials" max_age_days=30 action="tombstone"
```

### Supported Actions
- **archive**: Move to archived state after max age
- **tombstone**: Mark for deletion after max age
- **notify**: Alert but take no automatic action

### Compliance Alignment
- **GDPR**: Right to erasure supported via memory deletion + audit trail retention
- **HIPAA**: Access controls + audit logging for health-related data
- **EU AI Act**: Transparency via audit trails, human oversight via retention policies

## What This Means For You

- **Control who sees what** — per-agent, per-project access policies
- **Full audit history** — every action is logged and queryable
- **Automatic retention** — memories expire based on your policies
- **Compliance-ready** — designed for regulated environments

---

**See also:** [[Memory-Lifecycle]] · [[Behavioral-Learning]] · [[Upgrading-to-v2.8]]
