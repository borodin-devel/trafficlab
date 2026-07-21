# Logging Strategy

## Requirements

Each application writes UTF-8 JSON Lines diagnostics to its run directory and
may write concise human-readable console output. Every record contains UTC
timestamp, severity, application, run identifier, event name, and structured
fields. Records must not contain payload bytes, secrets, or unbounded packet
content.

The [observability library](../libs/observability/README.md) implements this
policy; applications own their event vocabularies and run-local sinks.

Supported severities are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
Default is `INFO`; configuration may change severity under the shared
[configuration rules](../CONFIGURATION.md).

## Performance Rules

Packet-path code performs no synchronous serialization, flushing, console/file
I/O, or waiting at any severity. It emits bounded events through a nonblocking
queue. On normal-capacity exhaustion, `DEBUG` and `INFO` may be dropped with an
exact counted summary. Capacity reserved for `WARNING`, `ERROR`, and `CRITICAL`
is drained first; when it is full, repeated severe events are coalesced by a
bounded `(severity, event_name)` counter retaining first/last timestamps and
count. Novel-key exhaustion increments one global severe-overflow counter.

The writer drains normal, reserved, and coalesced state deterministically and
emits exact aggregate counts when capacity returns and at orderly shutdown.
Synchronous fallback is permitted only on a thread proven not to execute a
packet path. Batch summaries replace per-packet informational logs.

## Required Events

Applications record start, resolved configuration identity, input validation,
resource admission, artifact validation, publication, child process outcome,
and terminal status. Failed runs retain logs and `launch.toml`.

## Reading

Follow [project architecture](README.md) before changing logging behavior.
