# Genetic Training Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Candidate Orchestration Shell

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Execute one candidate deterministically

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Chain model creation, generation, and evaluation

- **Objective:** Produce one validated candidate result with complete lineage.
- **Implementation:** Resolve source/member and resource configuration, keep
  the final destination absent, allocate deterministic candidate namespaces and
  distinct per-operation leaf attempts, copy immutable builders, apply and
  validate candidate values, invoke model-owned preparation and stages 50/60,
  validate detached outputs, retain failures, and classify eligibility.
- **Affected files:** `src/trafficlab/apps/genetic_training/`; tests.
- **Dependencies:** child applications, contracts, configuration, resource library.
- **Outputs:** Candidate result record and retained child diagnostics.
- **Tests:** Fake-child success/failure, builder immutability, directional
  member, candidate/unit topology, status-collision rejection, argument vector,
  three generation limits, path, detached hash, invalid result.
- **Validation:** Compare exact commands, immutable state transitions, artifacts,
  eligibility, and lineage.
- **Completion criteria:** One-candidate portions of GTR-AC-001, GTR-AC-002,
  GTR-AC-004, and GTR-AC-005 pass.

## [PLAN] STAGE 2 — Strategy Execution

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Implement registered strategy adapter

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Run populations and publish winner

- **Objective:** Execute a complete deterministic genetic run.
- **Implementation:** Implement strategy protocol, canonical scheduling,
  population barriers, stopping, winner/ranking publication, and retries.
- **Affected files:** training core, selected genetic strategy, tests.
- **Dependencies:** Stage 1 and one mature strategy roadmap.
- **Outputs:** Winning model and ranking report.
- **Tests:** Seed determinism, failure exclusion, ties, stopping, lineage, atomicity.
- **Validation:** Replay fixed fake result matrix and compare exact outputs.
- **Completion criteria:** GTR-AC-001 and GTR-AC-002 pass.

## [PLAN] STAGE 3 — Neural and Parallel Evaluation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 3.1 — Add multi-capture fitting and bounded workers

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 3.1.1 — Aggregate neural validation under resource control

- **Objective:** Evaluate neural candidates and parallel jobs safely.
- **Implementation:** Add deterministic source assignment, local fitting,
  per-capture child stages and equal mean; before enabling parallel work, add
  versioned worker/CPU/memory/storage settings, then reservations, queue, and
  canonical restore.
- **Affected files:** training scheduler, resources, neural adapters, tests.
- **Dependencies:** Stage 2, neural models, explicit resource settings.
- **Outputs:** Complete neural candidate aggregates and resource lineage.
- **Tests:** Multi-capture, derived-suffix anchor/guard, boundary leakage,
  one-failure, equal-mean, completion-order, and resource-limit property tests.
- **Validation:** Vary child completion order and assert identical result and bounds.
- **Completion criteria:** GTR-AC-001 through GTR-AC-005 pass.
