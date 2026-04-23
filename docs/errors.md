# SLM Error Reference

Every error SLM surfaces to users carries a code from this catalog.
Use `slm doctor` to diagnose most issues automatically.

## Queue Error Codes

| Code | Message | Recovery | Exit | HTTP |
|------|---------|----------|------|------|
| `RATE_LIMITED` | Too many requests — back off and retry | Wait the `retry_after_ms` value in the error envelope, then retry | 1 | 429 |
| `QUEUE_FULL` | Recall queue is at capacity | Reduce concurrent callers or increase `SLM_QUEUE_MAX_PENDING` | 1 | 503 |
| `TIMEOUT` | Recall did not complete in time | Check `slm doctor` for daemon health; increase `SLM_RECALL_TIMEOUT_S` if legitimate | 1 | 504 |
| `CANCELLED` | Request was cancelled by the caller | No action needed — caller withdrew the request | 0 | 499 |
| `DEAD_LETTER` | Request failed after max retries | Run `slm doctor`; check daemon logs at `~/.superlocalmemory/logs/daemon.log` | 1 | 504 |
| `DAEMON_DOWN` | SLM daemon is not reachable | Run `slm restart` or `slm doctor` | 1 | 502 |
| `INTERNAL` | Unexpected internal error | Report at github.com/qualixar/superlocalmemory/issues with `slm doctor` output | 2 | 500 |

## Exception Types

| Exception | Module | When raised |
|-----------|--------|-------------|
| `PoolError` | `mcp._pool_adapter` | Worker pool returned an error envelope (`{"ok": false}`) — worker crashed or timed out |
| `CapabilityError` | `core.engine_capabilities` | A LIGHT-mode MCP engine was asked for a FULL-mode operation (recall/store). Route through the daemon instead |
| `SafeFsError` | `core.safe_fs` | File-system safety check failed — symlink detected, wrong owner, or cloud-synced directory |
| `QueueTimeoutError` | `core.recall_queue` | `poll_result` exceeded its deadline waiting for the worker to complete |
| `DeadLetterError` | `core.recall_queue` | Request exhausted `max_receives` retries and was moved to the dead-letter queue |
| `QueueCancelledError` | `core.recall_queue` | All subscribers withdrew before the worker completed |

## CLI Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Operational error (see error code above) |
| 2 | Internal / unexpected error |
| 130 | Interrupted by Ctrl-C (SIGINT) |

## Structured Error Envelope

All MCP tool errors and daemon HTTP errors return a JSON envelope:

```json
{
  "ok": false,
  "error_code": "RATE_LIMITED",
  "error": "too many requests — back off and retry",
  "request_id": "r-abc123",
  "retry_after_ms": 1200
}
```

Fields: `ok` (always `false`), `error_code` (from the table above),
`error` (human-readable), `request_id` (if applicable),
`retry_after_ms` (only for `RATE_LIMITED`).
