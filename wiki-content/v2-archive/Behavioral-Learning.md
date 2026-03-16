# Behavioral Learning

SuperLocalMemory learns from what happens after memories are recalled — tracking which memories lead to successful outcomes and surfacing them more often.

## How It Works

1. **You recall a memory** — search finds a relevant memory for your task
2. **You take action** — use the information, write code, make decisions
3. **Outcome is recorded** — explicitly via `report_outcome` or implicitly from your behavior
4. **Patterns emerge** — over time, the system learns which memories are most useful

## Reporting Outcomes

### Explicit Reporting
```
report_outcome memory_id="abc123" outcome="success" context="Used this API pattern, it worked"
```

Outcome values: `success`, `failure`, `partial`

### Implicit Inference
The system also infers outcomes from your behavior:
- Recalled a memory and continued working? Likely **success**
- Recalled a memory then immediately searched again? Likely **failure**
- Asked a follow-up question? Likely **partial**

## Viewing Patterns

```
get_behavioral_patterns
```

Shows learned patterns like:
- "API documentation memories have 85% success rate in this project"
- "Architecture decisions from Project A transfer well to Project B"

## Cross-Project Transfer

Behavioral patterns learned in one project can improve recommendations in similar projects. This happens automatically and preserves privacy — only pattern metadata transfers, never raw memory content.

## Privacy Guarantees

- All learning happens 100% locally
- No data leaves your machine
- No LLM inference calls — pure statistical pattern recognition
- Learning data is stored in `learning.db` (separate from memories, GDPR-erasable)

---

**See also:** [[Memory-Lifecycle]] · [[Enterprise-Compliance]] · [[Learning-System]]
