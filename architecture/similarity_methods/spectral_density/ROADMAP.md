# Spectral-Density Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Signal-Processing Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define spectral comparison pipeline

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve series through score

- **Objective:** Satisfy SPS-AC-002.
- **Implementation:** Define regular series, sampling/horizon, preprocessing,
  spectral estimator/grid, zero-power policy, distance/normalization, score,
  schema, complexity, diagnostics, and examples.
- **Affected files:** method SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** signal-processing literature and temporal extraction.
- **Outputs:** Complete reviewed specification.
- **Tests:** Sinusoids/noise, leakage/aliasing, zero/short series, FFT/library
  comparison, deterministic output, registry gate.
- **Validation:** Independent spectral calculations and expert review.
- **Completion criteria:** Every unknown is testable and SPS-AC-002 passes.
