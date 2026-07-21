# Sequence-Kernel MMD Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Window Path Construction

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Build canonical anchored paths

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Verify all window and point boundaries

- **Objective:** Produce deterministic path samples and counts.
- **Implementation:** Resolve config/resource bounds; implement windowing,
  transformed points, ordering, anchor, empty paths, and exclusions.
- **Affected files:** `src/trafficlab/similarity_methods/sequence_kernel_mmd/`; tests.
- **Dependencies:** temporal extraction.
- **Outputs:** Canonical window-path collection.
- **Tests:** Hand windows, boundaries, empty, order, horizon, invalid settings.
- **Validation:** Compare every point/path/count with fixtures.
- **Completion criteria:** Window portions of MSM-AC-001/002 pass.

## [PLAN] STAGE 2 — Signature Kernel and MMD

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Select library and implement deterministic score

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Reproduce authoritative kernel/MMD examples

- **Objective:** Complete MSM-AC-001 and MSM-AC-002.
- **Implementation:** Select deterministic library/backend; implement RBF,
  signatures, normalization, biased MMD, tolerance, score, diagnostics/lineage.
- **Affected files:** method numerical core and tests.
- **Dependencies:** Stage 1 and numerical library.
- **Outputs:** Deterministic method result.
- **Tests:** Independent kernels/MMD, negative K, n=1, tolerance, fixed repeat.
- **Validation:** Compare every kernel matrix and aggregate independently.
- **Completion criteria:** MSM-AC-001 and MSM-AC-002 pass.

## [PLAN] STAGE 3 — Resource and Result Integration

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 3.1 — Bound expensive evaluation

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 3.1.1 — Admit and publish safely

- **Objective:** Satisfy MSM-AC-003.
- **Implementation:** Add complexity estimate/admission, typed numerical/
  library failures, result-contract adapter, atomic publication.
- **Affected files:** method adapter and integration tests.
- **Dependencies:** Stage 2, resources, similarity evaluation.
- **Outputs:** Contract-valid result or diagnostic failure.
- **Tests:** Resource, malformed input, invalid kernel/numerics, publication failure.
- **Validation:** Inject each failure and verify no successful result.
- **Completion criteria:** All MSM requirements pass.
