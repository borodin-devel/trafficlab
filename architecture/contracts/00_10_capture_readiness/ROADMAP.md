# Capture Readiness Contract Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Implement Contract Schema

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement request binding and readiness serialization

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Build producer-consumer interface

- **Objective:** Implement the specified version 1 package and satisfy RDN-AC-001.
- **Implementation:** Implement package membership, canonical TOML,
  request/workspace identity/hash, findings/blockers/capabilities,
  time/freshness, validation, lineage, bounds, and shared fixtures.
- **Affected files:** readiness contract library; preflight/capture adapters and tests.
- **Dependencies:** capture-request contract, artifact I/O, lineage, and workspace identity owner.
- **Outputs:** Shared schema/validators and golden/invalid fixtures.
- **Tests:** Schema, mismatch, blocker, stale, mutation, lineage fixture designs.
- **Validation:** Run identical serialized fixtures through producer and consumer validators.
- **Completion criteria:** RDN-AC-001 and schema portions of RDN-AC-002 pass.

## [PLAN] STAGE 2 — Shared Validation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Implement both boundary adapters

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Publish and consume matching decisions

- **Objective:** Satisfy RDN-AC-002.
- **Implementation:** Implement canonical serializer, shared validator,
  detached hashes/lineage, atomic package/status publication, exact request
  binding/freshness, and producer/consumer adapters.
- **Affected files:** contract library, preflight/capture, contract tests.
- **Dependencies:** Stage 1, artifact and lineage libraries.
- **Outputs:** Versioned readiness artifact and validators.
- **Tests:** Golden, blocker, mismatch, freshness/future-clock, mutation,
  ordering, permission, protected-sentinel, detached-hash, and atomicity tests.
- **Validation:** Run identical fixtures through producer and consumer.
- **Completion criteria:** RDN-AC-002 passes.
