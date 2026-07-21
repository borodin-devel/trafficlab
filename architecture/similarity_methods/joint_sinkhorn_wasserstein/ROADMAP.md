# Joint Sinkhorn/Wasserstein Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Solver Selection and Mathematical Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Reproduce authoritative transport examples

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement deterministic debiased Sinkhorn

- **Objective:** Satisfy MJS-AC-001.
- **Implementation:** Select solver/backend, define config schema, ground
  costs/weights, three transport terms, convergence checks, debiasing, score.
- **Affected files:** `src/trafficlab/similarity_methods/joint_sinkhorn_wasserstein/`; tests.
- **Dependencies:** temporal extraction, numerical library, resource admission.
- **Outputs:** Deterministic pure method core and library lineage.
- **Tests:** Independent examples, unequal clouds, zero IAT, scales, deterministic repeat.
- **Validation:** Compare all transport terms and residuals independently.
- **Completion criteria:** MJS-AC-001 passes.

## [PLAN] STAGE 2 — Failure and Application Integration

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Publish only converged valid results

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Bound resources and validate diagnostics

- **Objective:** Satisfy MJS-AC-002.
- **Implementation:** Add resource estimator/admission, typed convergence/
  numerical failures, method details, result-contract adapter.
- **Affected files:** method adapter and integration tests.
- **Dependencies:** Stage 1 and similarity evaluation.
- **Outputs:** Contract-valid results or complete failure diagnostics.
- **Tests:** Non-convergence, invalid numerics, resource rejection, malformed input, atomicity.
- **Validation:** Inject each failure and verify no successful result.
- **Completion criteria:** All MJS requirements pass.
