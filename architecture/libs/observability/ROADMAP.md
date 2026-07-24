# Observability Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [DONE] STAGE 1 — Bounded Structured Logging

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.
- **Evidence:** Step 1.1 is `[DONE]` with bounded caller-driven diagnostics and
  full repository quality evidence.

### [DONE] STEP 1.1 — Implement event and sink pipeline

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.
- **Evidence:** Substep 1.1.1 covers OBS-AC-001 through OBS-AC-003.

#### [DONE] SUBSTEP 1.1.1 — Validate, queue, serialize, and stress-test

- **Objective:** Satisfy OBS-AC-001 and OBS-AC-002.
- **Implementation:** Define event schema, severity filter, bounded normal and
  reserved queues, bounded severe coalescing/global overflow counters, writer,
  canonical JSONL, console renderer, drop/coalescing summaries, and
  non-packet-only fallback.
- **Affected files:** `src/trafficlab/libs/observability/`; `tests/libs/observability/`.
- **Dependencies:** configuration and artifact I/O.
- **Outputs:** Versioned event interface and sinks.
- **Tests:** Schema, severity, normal/severe saturation, concurrency,
  no-packet-thread-I/O, sink-failure, and security tests.
- **Validation:** Parse all fixture output, verify exact saturation accounting,
  and assert packet producer spies observe no blocking/sink operation.
- **Completion criteria:** OBS-AC-001 through OBS-AC-003 pass.
- **Evidence:** Unit fixtures cover bounded immutable events, filtering,
  normal/reserved saturation, severe coalescing/global overflow, deterministic
  summaries, canonical JSONL, caller-driven sink failure, and boundary
  validation. Full quality verification passes tests, 100% coverage, Ruff,
  Pyright, corpus validation, whitespace, and wheel build.
