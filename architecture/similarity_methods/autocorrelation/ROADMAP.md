# Autocorrelation Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Lag Correlation Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Compute and compare configured autocorrelations

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement exact formula and validation

- **Objective:** Satisfy MAC-AC-001 and MAC-AC-002.
- **Implementation:** Resolve schema/resource bounds; implement sequences,
  means/denominators, per-lag correlations, normalized differences, weighted score/details.
- **Affected files:** `src/trafficlab/similarity_methods/autocorrelation/`; tests.
- **Dependencies:** temporal extraction and result contract.
- **Outputs:** Deterministic autocorrelation method.
- **Tests:** Independent sequences, lag boundaries, constant/short data, weights, invalids.
- **Validation:** Compare every intermediate against independent calculation.
- **Completion criteria:** All MAC requirements pass.
