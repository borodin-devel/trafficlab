# Trafficlab Orchestrator Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Run Directory and Child Executor

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Launch one application

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve paths and retain status

- **Objective:** Implement `trafficlab run` with safe deterministic path handling.
- **Implementation:** Add CLI/config, run naming, collision handling, explicit
  attempt paths, application-specific output adapters, argument-vector process
  launch, detached status/startup lineage, and failure retention.
- **Affected files:** `src/trafficlab/apps/trafficlab/`; tests.
- **Dependencies:** configuration, artifact, lineage, observability, resource library.
- **Outputs:** Single-application run directory and top-level status.
- **Tests:** Clock/collision, path, cwd, attempt type/owner/mode/emptiness,
  adapter, absent-destination, orphan/status, injection, child outcome, and
  test-run-root fixtures.
- **Validation:** Compare exact command vector, attempt/artifact layout, and
  detached status validation sequence.
- **Completion criteria:** Single-run portions of ORC-AC-001, ORC-AC-003, and ORC-AC-004 pass.

## [PLAN] STAGE 2 — Experiment Pipeline

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Chain validated contracts

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Execute supported stage suffixes

- **Objective:** Implement fail-fast explicit-path experiments.
- **Implementation:** Define stage graph, require request/readiness inputs,
  assign attempt/artifact paths, validate detached status and contracts, pass
  explicit directional status/member selection, reserve resources, stop
  failures, and record lineage.
- **Affected files:** orchestrator pipeline and integration tests.
- **Dependencies:** Stage 1 and mature child applications/contracts.
- **Outputs:** Complete or diagnosable failed experiment directory.
- **Tests:** Every start stage, capture request/readiness mismatch/freshness,
  directional member missing/invalid/repeated, producer/consumer path, adapter,
  contract/status/child failure, completion order, and resource cases.
- **Validation:** Inspect exact launched stages and vectors; verify no implicit
  preflight and no later launch after failure.
- **Completion criteria:** ORC-AC-001 through ORC-AC-006 pass.
