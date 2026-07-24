# Observability Core Design

**Date:** 2026-07-24
**Status:** Approved design direction; pending written-spec review
**Owner:** Trafficlab maintainers

## Purpose

Implement Observability roadmap STEP 1.1 as a bounded structured-event library.
Packet-path producers validate and admit events without synchronous sink I/O,
waiting, serialization, or flushing. A caller-owned non-packet worker drains
the queues to injected JSON Lines and optional console sinks.

The library does not own an application thread, choose an application event
vocabulary, persist metrics, rotate files, inspect packet payloads, or infer
whether an arbitrary caller is a packet path. Applications must call `emit`
from packet paths and `drain` only from a proven non-packet context.

## Source Requirements

This design implements OBS-FR-001 through OBS-FR-003, OBS-IF-001,
OBS-NFR-001 through OBS-NFR-003, OBS-SEC-001, OBS-ERR-001, OBS-TST-001, and
OBS-AC-001 through OBS-AC-003. It refines the system logging policy's
nonblocking packet-path, bounded-memory, reserved-severity, coalescing, and
deterministic-draining rules.

## Considered Approaches

### Library-owned background writer

Starting a thread makes application integration convenient, but introduces
hidden lifecycle, shutdown, and ordering behavior. It also makes deterministic
unit tests depend on timing.

### Synchronous producer writes

Serializing and writing directly makes output order obvious, but violates the
packet-path nonblocking requirement at every severity.

### Caller-driven drain shell

The selected design keeps the event-routing core deterministic and bounded.
`emit` uses nonblocking queue admission only; an application-owned worker or
explicit lifecycle boundary calls `drain`. This makes packet-path guarantees
testable without taking thread ownership from applications.

## Package Structure

```text
src/trafficlab/libs/observability/
  __init__.py    public facade
  errors.py      typed validation and sink failures
  values.py      severity, immutable events, policy, summaries
  core.py        bounded routing, filtering, accounting, deterministic drain plan
  sinks.py       canonical JSONL and concise console rendering
  service.py     caller-driven imperative drain shell
tests/libs/observability/
  __init__.py
  test_values.py
  test_core.py
  test_sinks.py
  test_service.py
  test_defensive_paths.py
```

`values.py`, validation, accounting, event rendering, and drain planning are
functional core code. The drain shell calls injected sinks and clock only after
an event has left packet-path admission.

## Public Values

`Severity` is a `StrEnum` with exactly `DEBUG`, `INFO`, `WARNING`, `ERROR`, and
`CRITICAL`. `severity_rank(severity) -> int` maps them to 10, 20, 30, 40, and
50 respectively; filtering and deterministic sorting use that function rather
than Python enum comparison. `DEBUG` and `INFO` are normal severity; `WARNING`,
`ERROR`, and `CRITICAL` are reserved severity.

`StructuredEvent` is a frozen, slotted value with fields in this order:

```text
timestamp: datetime
severity: Severity
application: str
run_id: str
event_name: str
fields: tuple[tuple[str, Scalar], ...]
```

Its timestamp must be timezone-aware UTC and is normalized to microsecond UTC
precision. `application`, `run_id`, `event_name`, and each field name are
non-empty single-line ASCII identifiers no longer than 128 characters. Field
names are unique and serialized in lexical order. `Scalar` is exactly `None`,
`bool`, finite `int`, finite `float`, or UTF-8 `str`; containers and bytes are
rejected. There are at most 32 fields and every string value is at most 1,024
UTF-8 bytes. The core rejects names containing `secret`, `token`, `password`,
`credential`, `authorization`, or `cookie`, case-insensitively. These limits
exclude payload bytes, nested unbounded values, and obvious secret-bearing
fields from this boundary.

`ObservabilityPolicy` is frozen and slotted:

```text
normal_capacity: int
reserved_capacity: int
coalesce_capacity: int
minimum_severity: Severity = INFO
```

All capacities are positive. Normal capacity admits only DEBUG and INFO;
reserved capacity admits WARNING, ERROR, and CRITICAL. The values are explicit
per logger instance, so maximum retained event and coalescing memory is
bounded by policy plus the fixed field limits.

## Routing and Accounting

`EventRouter(policy, *, application, run_id)` owns one bounded normal queue,
one bounded reserved queue, and one bounded severe-coalescing map. Its identity
is validated with the same rules as an event; every emitted event must match
that identity. This gives summaries mandatory identity fields even when no
event remains queued. `emit(event) -> EmitResult` performs no JSON encoding,
sink call, flush, console operation, blocking queue operation, or clock read.

Events below `minimum_severity` return `FILTERED` and do not enter drop
accounting. A normal event enters its FIFO queue if space exists; otherwise it
increments an exact per-severity low-drop counter and returns `DROPPED`.
Reserved events enter their FIFO queue if space exists. If full, the router
coalesces by exact `(severity, event_name)`: an existing key increments count
and updates only its last timestamp; a new key records count one and first/last
timestamps while map capacity remains. A novel key after map saturation
increments one exact global severe-overflow counter. These outcomes return
`COALESCED` or `OVERFLOWED` respectively.

The router uses `queue.Queue.put_nowait`/`get_nowait` and a short accounting
lock. Its public contract is nonblocking: it never invokes `wait`, a sink,
serializer, clock, console, or file operation from `emit`. Python's standard
library queue provides thread-safe bounded admission under the supported
Python 3.12 runtime. Applications remain responsible for never supplying
semantic secret values; the library rejects bytes and prohibited field names
at this enforceable boundary.

## Deterministic Drain and Summaries

`plan_drain(router, summary_timestamp) -> DrainBatch` atomically snapshots
available state and returns values in this exact order:

1. reserved FIFO events;
2. normal FIFO events;
3. severe-coalescing summaries ordered by `Severity`, then `event_name`;
4. low-drop summaries ordered by `Severity`;
5. one global severe-overflow summary, when nonzero.

`summary_timestamp` is an explicit UTC value supplied by the non-packet
caller. Each summary is a normal `StructuredEvent` with names
`observability.severe_coalesced`, `observability.low_severity_dropped`, or
`observability.severe_overflow`. Fields carry only exact counts, severity,
event name where applicable, and first/last timestamp where applicable.
Summary generation happens outside packet producers and does not re-enqueue a
summary, so saturation cannot lose its own accounting.

A severe-coalesced summary retains its coalesced severity. A low-drop summary
uses `WARNING`; a global severe-overflow summary uses `ERROR`. Every summary's
event timestamp is `summary_timestamp`; coalesced first/last timestamps remain
separate fields.

The router is single-consumer for drain operations. Concurrent producer
admission while a batch is being planned belongs to the next batch. FIFO order
is preserved inside each queue; the stated priority is intentionally stronger
than a cross-queue arrival-order claim.

## Canonical Rendering and Sinks

`render_jsonl(event) -> bytes` emits one UTF-8 JSON object followed by LF. It
uses fixed top-level key order `timestamp`, `severity`, `application`,
`run_id`, `event_name`, `fields`; field keys are lexical; strings are compact
JSON; `allow_nan` is disabled; CR, BOM, and payload bytes cannot occur. UTC
timestamps use RFC 3339 microseconds with `Z`.

`render_console(event) -> str` emits one concise single-line rendering:
`<timestamp> <severity> <application>/<run_id> <event_name> <canonical fields>`.
It escapes controls and never renders bytes.

The imperative API is:

```python
drain(
    router: EventRouter,
    *,
    clock: Callable[[], datetime],
    jsonl_sink: Callable[[bytes], None],
    console_sink: Callable[[str], None] | None = None,
    flush: Callable[[], None] | None = None,
) -> DrainResult
```

It reads the injected clock once, creates one batch, serializes and writes every
JSONL line in batch order, optionally writes console lines in the same order,
and invokes `flush` once after successful writes. Sinks are injected; path
opening, file rotation, and worker scheduling remain application-owned. A sink,
console, serialization, or flush exception raises `SinkWriteError` with the
original failure as cause and does not recursively log. `DrainResult` reports
only successfully completed batches and event count.

## Errors

`ObservabilityError` is the public base class. Specific typed errors are
`InvalidSeverityError`, `InvalidEventError`, `InvalidPolicyError`,
`InvalidSinkError`, and `SinkWriteError`. Messages are deterministic,
single-line, and contain no event payload or secret value.

## Test Strategy

Tests use public values plus spy queues/sinks; no test performs host logging.

- Value tests cover severity order, UTC timestamps, canonical field ordering,
  limits, non-finite floats, containers, bytes, controls, and secret names.
- Core tests cover filtering, normal and reserved capacity, exact low drops,
  repeated severe coalescing, saturated novel severe keys, bounded state, and
  deterministic batch order.
- Concurrency tests use many producer threads and prove retained plus summary
  counts equal admitted attempts. Packet producer spies prove no wait, render,
  sink, flush, console, file, or clock call.
- Sink tests parse every emitted JSONL record, assert mandatory fields and
  canonical rendering, and verify concise console output.
- Service tests prove injected sink and flush order, no recursive event on
  failure, and no claimed successful `DrainResult` after failure.
- Focused tests, complete suite, 100% coverage, Ruff, Pyright, corpus,
  whitespace, documentation, and wheel-build checks pass before completion.

## Compatibility and Limits

This is the first public Observability API. It changes no existing runtime
contract. It supports Python 3.12 only and deliberately excludes background
thread ownership, file rotation, metrics, nested structured fields, binary
payloads, secret detection beyond prohibited field names, and persistence
guarantees across abrupt termination.
