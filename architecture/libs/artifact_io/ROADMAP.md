# Artifact I/O Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Validated Atomic Package

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria, including detached-status acceptance.

### [PLAN] STEP 1.1 — Implement publication transaction

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Build and verify artifact publication

- **Objective:** Satisfy ART-AC-001 and ART-AC-002.
- **Implementation:** Implement attempt/destination path plans, staging,
  validation callbacks, non-self-referential manifest hashes, streaming hashes,
  atomic artifact publication, and the version 1 detached status commit marker.
- **Affected files:** `src/trafficlab/libs/artifact_io/`; `tests/libs/artifact_io/`.
- **Dependencies:** lineage library and supported filesystem semantics.
- **Outputs:** Versioned publication interface and typed failures.
- **Tests:** Unit, property, status golden/mutation, self-hash, permission,
  orphan, failure-injection, crash-state, and integration tests.
- **Validation:** Run tests on same-filesystem temporary destinations; inspect
  manifest/member/launch/status hashes and every artifact-to-status crash point.
- **Completion criteria:** All SRS requirements and ART-AC-001 through ART-AC-003 pass.
