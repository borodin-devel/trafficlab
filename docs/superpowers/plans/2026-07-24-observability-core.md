# Observability Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Observability roadmap STEP 1.1 with bounded, structured,
nonblocking packet-path event admission and caller-driven JSONL draining.

**Architecture:** Frozen values validate bounded public events and policies.
`EventRouter` owns bounded normal/reserved queues plus exact coalescing and
overflow accounting; its producer interface performs no sink or serializer
work. A caller-owned drain shell obtains one timestamp from an injected clock,
plans deterministic batches, renders canonical JSONL, and invokes injected
sinks only outside packet paths.

**Tech Stack:** Python 3.12 standard library (`datetime`, `enum`, `json`,
`queue`, `threading`); pytest, Pyright, Ruff.

---

### Task 1: Define immutable event, severity, policy, and errors

**Files:**
- Create: `src/trafficlab/libs/observability/errors.py`
- Create: `src/trafficlab/libs/observability/values.py`
- Create: `src/trafficlab/libs/observability/__init__.py`
- Test: `tests/libs/observability/test_values.py`

- [ ] **Step 1: Write RED value-contract tests**

  Test `Severity` ordering and frozen/slotted `StructuredEvent` and
  `ObservabilityPolicy` construction. Require UTC timestamps, sorted unique
  fields, finite scalar-only values, all configured bounds, safe identity
  names, positive capacities, and typed failures for bytes, containers,
  control characters, prohibited secret field names, and invalid severity.

  ```python
  event = StructuredEvent(utc, Severity.INFO, "convert", "run-1", "start", (("count", 1),))
  assert event.fields == (("count", 1),)
  with pytest.raises(InvalidEventError):
      StructuredEvent(utc, Severity.INFO, "convert", "run-1", "packet", (("payload", b"x"),))
  ```

- [ ] **Step 2: Run RED tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_values.py -q`

  Expected: import failure for missing `trafficlab.libs.observability`.

- [ ] **Step 3: Implement typed values and errors**

  Add `Severity` as a `StrEnum` plus `severity_rank`; `ObservabilityError` and
  the five documented typed subclasses; and frozen, slotted values. Materialize
  fields once, reject bool-as-int confusion only where a count is required,
  sort by field name, and leave no mutable mapping in a public event.

  ```python
  @dataclass(frozen=True, slots=True)
  class ObservabilityPolicy:
      normal_capacity: int
      reserved_capacity: int
      coalesce_capacity: int
      minimum_severity: Severity = Severity.INFO
  ```

- [ ] **Step 4: Run GREEN tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_values.py -q`

  Expected: all public value construction and rejection tests pass.

### Task 2: Implement bounded nonblocking event routing

**Files:**
- Create: `src/trafficlab/libs/observability/core.py`
- Modify: `src/trafficlab/libs/observability/__init__.py`
- Test: `tests/libs/observability/test_core.py`

- [ ] **Step 1: Write RED routing tests**

  Test severity filtering, normal FIFO admission/drop accounting, reserved FIFO
  admission, repeated severe coalescing, bounded novel-key overflow, exact
  `EmitResult`, and application/run identity mismatch. Assert a filtered event
  changes no counter and a full normal queue retains at most its declared
  capacity.

  ```python
  router = EventRouter(ObservabilityPolicy(1, 1, 1, Severity.DEBUG), "convert", "run-1")
  assert router.emit(info_event) is EmitResult.ADMITTED
  assert router.emit(second_info_event) is EmitResult.DROPPED
  assert router.low_drop_counts() == ((Severity.INFO, 1),)
  ```

- [ ] **Step 2: Run RED tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_core.py -q`

  Expected: import failure for `EventRouter` and `EmitResult`.

- [ ] **Step 3: Implement router and exact accounting**

  Use two `queue.Queue` instances with `put_nowait` and one lock-protected
  bounded coalescing map. Do not call a sink, renderer, clock, `wait`, or
  `get` from `emit`. Coalesce by `(severity, event_name)` only after reserved
  saturation; retain count, first timestamp, and last timestamp exactly.

  ```python
  if severity_rank(event.severity) < severity_rank(self._policy.minimum_severity):
      return EmitResult.FILTERED
  try:
      queue.put_nowait(event)
  except Full:
      return self._record_saturation(event)
  return EmitResult.ADMITTED
  ```

- [ ] **Step 4: Run GREEN tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_core.py -q`

  Expected: queue bounds and all counters match fixture attempts exactly.

### Task 3: Plan deterministic drains and canonical JSONL rendering

**Files:**
- Create: `src/trafficlab/libs/observability/sinks.py`
- Modify: `src/trafficlab/libs/observability/core.py`
- Modify: `src/trafficlab/libs/observability/__init__.py`
- Test: `tests/libs/observability/test_sinks.py`
- Test: `tests/libs/observability/test_core.py`

- [ ] **Step 1: Write RED rendering and drain-plan tests**

  Test canonical UTF-8 JSONL parses to exactly required keys and LF termination;
  test console control escaping; and verify drain order is reserved FIFO,
  normal FIFO, sorted coalescing summaries, low-drop summaries, then overflow.
  Use one explicit UTC summary timestamp and assert summary counts/identity.

  ```python
  batch = plan_drain(router, summary_timestamp=utc)
  assert [event.event_name for event in batch.events] == [
      "critical", "debug", "observability.severe_coalesced",
      "observability.low_severity_dropped", "observability.severe_overflow",
  ]
  assert json.loads(render_jsonl(batch.events[0]))["severity"] == "CRITICAL"
  ```

- [ ] **Step 2: Run RED tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_core.py tests/libs/observability/test_sinks.py -q`

  Expected: missing drain planner and renderers.

- [ ] **Step 3: Implement drain batch and renderers**

  Snapshot and clear state under the router lock, then create summaries outside
  packet admission. Sort coalesced keys by declared severity then event name;
  sort low-drop severities by declared severity. Render JSON with fixed top
  level insertion order, lexical fields, compact separators, `ensure_ascii=False`,
  `allow_nan=False`, and one LF.

  ```python
  return (json.dumps(record, ensure_ascii=False, allow_nan=False,
      separators=(",", ":"), sort_keys=False).encode("utf-8") + b"\n")
  ```

- [ ] **Step 4: Run GREEN tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_core.py tests/libs/observability/test_sinks.py -q`

  Expected: exact batch order and parsed canonical JSONL fixtures pass.

### Task 4: Add caller-driven drain shell and defensive concurrency coverage

**Files:**
- Create: `src/trafficlab/libs/observability/service.py`
- Modify: `src/trafficlab/libs/observability/__init__.py`
- Test: `tests/libs/observability/test_service.py`
- Test: `tests/libs/observability/test_defensive_paths.py`

- [ ] **Step 1: Write RED shell and boundary tests**

  Use injected list-backed sinks and clock spies. Test one clock read, JSONL and
  console write order, one successful flush, typed sink failure without a
  recursive event, invalid sinks, and concurrent producers. Spies attached to
  packet producers must observe no renderer, sink, flush, console, clock, or
  blocking wait call.

  ```python
  result = drain(router, clock=clock, jsonl_sink=lines.append, flush=flush)
  assert result.event_count == len(lines)
  assert clock.calls == 1
  with pytest.raises(SinkWriteError):
      drain(router, clock=clock, jsonl_sink=raising_sink)
  ```

- [ ] **Step 2: Run RED tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_service.py tests/libs/observability/test_defensive_paths.py -q`

  Expected: missing `drain` shell and typed failure paths.

- [ ] **Step 3: Implement drain shell**

  Validate injected callables before obtaining a batch. Read `clock()` exactly
  once, call `plan_drain`, render/write JSONL and optional console records in
  order, and flush only after all writes. Wrap sink/flush exceptions in
  `SinkWriteError` without calling `router.emit`.

  ```python
  try:
      for event in batch.events:
          jsonl_sink(render_jsonl(event))
          if console_sink is not None:
              console_sink(render_console(event))
      if flush is not None:
          flush()
  except Exception as error:
      raise SinkWriteError("observability sink write failed") from error
  ```

- [ ] **Step 4: Run GREEN tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/observability/test_service.py tests/libs/observability/test_defensive_paths.py -q`

  Expected: shell ordering, failures, saturation, and packet-path spies pass.

### Task 5: Verify and record roadmap evidence

**Files:**
- Modify: `architecture/libs/observability/ROADMAP.md`
- Modify: `architecture/project/ROADMAP.md`
- Create: `docs/superpowers/specs/2026-07-24-observability-core-design.md`
- Create: `docs/superpowers/plans/2026-07-24-observability-core.md`

- [ ] **Step 1: Mark only Observability Stage 1, Step 1.1, and Substep 1.1.1 done**

  Add OBS-AC-001 through OBS-AC-003 evidence. Update the central equal-weight
  progress from 71% to 86% (`600 / 7 = 86%`) and retain Resource management as
  `[PLAN]`.

- [ ] **Step 2: Run full verification**

  Run: `PYTHONPATH=. UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked python tools/quality.py all`

  Expected: formatting, lint, Pyright, 100% coverage, tests, corpus/docs,
  whitespace, and wheel build pass.

- [ ] **Step 3: Commit the one roadmap STEP**

  Stage Observability source/tests, its approved spec/plan, and only its two
  roadmap updates. Commit:

  ```text
  feature(observability): add bounded event pipeline
  ```
