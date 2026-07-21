# Inspection Dataset Contract Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Resolve Versioned Row Schema

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define equivalent Parquet and JSONL forms

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Complete data and privacy contract

- **Objective:** Satisfy IXD-AC-001.
- **Implementation:** Define columns/types/units/nulls, JSON mapping,
  timestamp precision, compression, row groups/batches, file/row limits,
  manifest/schema versions, exclusions, privacy allowlist, examples.
- **Affected files:** contract SAD/SRS/schema docs and app CONFIGS.
- **Dependencies:** PCAPNG observation model and PyArrow support.
- **Outputs:** Machine-readable schemas and golden logical rows.
- **Tests:** Schema, cross-encoding, unit/null, privacy, size boundary designs.
- **Validation:** ML/LLM consumer review and exact field traceability.
- **Completion criteria:** Every IXD-IF-002 item is explicit/testable.

## [PLAN] STAGE 2 — Shared Package Validation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Implement schemas and mutation fixtures

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Validate logical equivalence and publication

- **Objective:** Satisfy IXD-AC-002 and IXD-AC-003.
- **Implementation:** Implement schema, Parquet/JSONL readers, row comparator,
  privacy validator, non-self-referential manifest checks, detached-status
  verification, and package validator.
- **Affected files:** contract library, inspection app, contract tests.
- **Dependencies:** Stage 1, PyArrow, artifact/lineage libraries.
- **Outputs:** Golden package and shared validator.
- **Tests:** Golden, manifest self-entry rejection, status mutation/orphan,
  privacy, malformed, large-stream, and atomicity tests.
- **Validation:** Run all fixtures through producer and consumer tooling.
- **Completion criteria:** IXD-AC-002 and IXD-AC-003 pass.
