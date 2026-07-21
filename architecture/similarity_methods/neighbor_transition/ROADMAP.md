# Neighbour-Transition Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Partition and Transition Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Validate complete domain and compare transitions

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement canonical states and JSD

- **Objective:** Satisfy MNT-AC-001 and MNT-AC-002.
- **Implementation:** Define config schema/state bound; implement complete-
  domain partition validator, assignments, transitions, probability vectors,
  base-2 JSD, score, and diagnostics.
- **Affected files:** `src/trafficlab/similarity_methods/neighbor_transition/`; tests.
- **Dependencies:** temporal extraction and result contract.
- **Outputs:** Deterministic transition method.
- **Tests:** Partition property/boundaries, hand sequences/JSD, zeros, invalids, resource bound.
- **Validation:** Exhaust small partitions and compare independent JSD.
- **Completion criteria:** All MNT requirements pass.
