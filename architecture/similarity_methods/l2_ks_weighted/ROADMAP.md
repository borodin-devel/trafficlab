# Weighted L2 KS Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Strict Component Composition

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Combine validated KS results

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Validate weights and preserve diagnostics

- **Objective:** Satisfy MLK-AC-001 and MLK-AC-002.
- **Implementation:** Add exact-decimal config, invoke both component
  cores, validate results, compute weighted score, serialize all diagnostics.
- **Affected files:** `src/trafficlab/similarity_methods/l2_ks_weighted/`; tests.
- **Dependencies:** IAT/frame KS and result contract.
- **Outputs:** Deterministic combined method result.
- **Tests:** Exact examples, boundaries, invalid/nonfinite sums, zero-weight failure.
- **Validation:** Recompute score independently and validate complete result.
- **Completion criteria:** All MLK requirements pass.
