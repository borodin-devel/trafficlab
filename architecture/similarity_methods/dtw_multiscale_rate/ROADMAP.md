# Multi-Scale Rate DTW Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Mathematical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define bounded multi-scale DTW

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve series, warping, and score

- **Objective:** Satisfy SDW-AC-002.
- **Implementation:** Define bins/features, local distance, admissible path,
  window, boundary/unequal lengths, normalization, scales, score, schema,
  numerical tolerance, complexity/admission, diagnostics, examples.
- **Affected files:** method SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** temporal extraction and maintained DTW research.
- **Outputs:** Complete reviewed specification.
- **Tests:** Hand paths, constrained/unconstrained contrasts, boundaries,
  complexity rejection, independent library comparison, registry gate.
- **Validation:** Independent path/cost review and resource analysis.
- **Completion criteria:** Every unknown is testable and SDW-AC-002 passes.
