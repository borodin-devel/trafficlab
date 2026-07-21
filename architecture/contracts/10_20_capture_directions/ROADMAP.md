# Capture Directions Contract Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Versioned Package Validator

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement producer-consumer conformance

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Validate package, PCAPNG, report, and hashes

- **Objective:** Ensure both applications agree on one exact interface.
- **Implementation:** Define schema version, JSON schemas, path rules,
  non-self-referential member validator, PCAPNG preservation checks, detached
  status/hash verification, and compatibility policy.
- **Affected files:** contract schemas, shared validators, contract tests.
- **Dependencies:** artifact, lineage, PCAPNG libraries; capture and convert.
- **Outputs:** Golden package and shared versioned validator.
- **Tests:** Golden, manifest self-entry rejection, detached-status mutation,
  orphan, missing/extra, traversal, explicit directional selection, malformed
  report, and packet fixture tests.
- **Validation:** Run identical fixture corpus through producer and consumer entry points.
- **Completion criteria:** CDR-AC-001 through CDR-AC-003 pass.
