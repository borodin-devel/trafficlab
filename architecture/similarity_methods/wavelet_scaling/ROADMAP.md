# Wavelet-Scaling Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Signal-Processing Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define wavelet scaling comparison

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve transform and score

- **Objective:** Satisfy SWV-AC-002.
- **Implementation:** Define traffic series, transform/wavelet, boundary,
  levels, coefficient statistics, scale alignment/weights, distance, score,
  schema, numerical/resource limits, diagnostics, and examples.
- **Affected files:** method SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** wavelet literature and temporal extraction.
- **Outputs:** Complete reviewed specification.
- **Tests:** Known signals, boundary/non-dyadic cases, zero scale, independent
  library comparison, deterministic output, registry gate.
- **Validation:** Independent transform calculations and expert review.
- **Completion criteria:** Every unknown is testable and SWV-AC-002 passes.
