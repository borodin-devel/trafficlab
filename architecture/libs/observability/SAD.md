# Observability Software Architecture Document

## Context, Goals, and Boundaries

Every run needs diagnosable structured events without material packet-path
cost. The library owns event validation, queueing, JSONL serialization, drop
accounting, and console rendering. It does not define component metrics.

## Structure and Data Flow

Pure validation creates immutable bounded events. Every packet-path producer
enqueues without blocking at every severity. A writer shell serializes
canonical JSON Lines and flushes according to policy. The queue reserves
bounded severe-event capacity. After that capacity fills, a bounded map
coalesces repeated `(severity, event_name)` events with exact counts and
first/last timestamps; novel-key overflow increments one global exact counter.
Low-severity drop summaries and severe coalescing summaries preserve counts.
Only a non-packet thread may use bounded synchronous fallback.

## Errors, Security, Performance, and Resources

Invalid fields, unbounded values, serialization failure, and sink failure are
explicit. Payload bytes, secrets, and unbounded packet content are prohibited.
Queue size, field size, flush policy, and severity are configured and recorded.

## Testing, Decisions, Risks, and Limits

Concurrency tests cover ordering policy, normal/reserved saturation, drop and
coalescing summaries, global severe overflow, and proof that packet producers
never wait or perform sink I/O. Exact queue implementation, timestamp
precision, and file rotation remain unresolved. Logging cannot guarantee
persistence after abrupt power loss.
