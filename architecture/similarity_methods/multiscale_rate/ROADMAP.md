# Multi-Scale Rate Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Multi-Scale Binning Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Build and compare exact count vectors

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement boundaries, weights, and score

- **Objective:** Satisfy MMR-AC-001 and MMR-AC-002.
- **Implementation:** Resolve schema/resource bound; implement aligned
  bins, packet/byte vectors, exclusions, normalized L1, weighted score/details.
- **Affected files:** `src/trafficlab/similarity_methods/multiscale_rate/`; tests.
- **Dependencies:** temporal extraction and result contract.
- **Outputs:** Deterministic method core and result schema.
- **Tests:** Hand vectors, boundaries, zero bins, post-horizon, weights, invalids, bin limit.
- **Validation:** Independently recompute every vector/distance and validate result.
- **Completion criteria:** All MMR requirements pass.
