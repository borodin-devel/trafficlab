# Lineage Library Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Hash and Provenance Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement canonical lineage

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Hash, serialize, and validate provenance

- **Objective:** Satisfy LIN-AC-001 through LIN-AC-003.
- **Implementation:** Add streaming hash, canonical builders, detached hash
  domains, local/external path snapshot validation, graph validation, and typed errors.
- **Affected files:** `src/trafficlab/libs/lineage/`; `tests/libs/lineage/`.
- **Dependencies:** artifact I/O contract needs.
- **Outputs:** Versioned lineage representation and validators.
- **Tests:** Known-vector, ordering, path, mutation, self-hash, detached-status,
  graph, and property tests.
- **Validation:** Compare exact fixtures and verify self-reference, mutation,
  changed external source, and broken-parent rejection.
- **Completion criteria:** All lineage requirements and LIN-AC-001 through
  LIN-AC-003 pass.
