# Hurst-Parameter Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Statistical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Select and validate Hurst estimator

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve assumptions and score

- **Objective:** Satisfy SHP-AC-002.
- **Implementation:** Define traffic series, estimator equations, scale
  choice, assumptions, minimum input, bias/uncertainty diagnostics, mapping,
  schema, numerical/resource controls, and examples.
- **Affected files:** method SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** statistical literature and temporal extraction.
- **Outputs:** Complete reviewed statistical specification.
- **Tests:** Known synthetic processes, finite-sample contrasts, invalid scaling,
  deterministic fit, independent estimator comparison, registry gate.
- **Validation:** Simulation study and expert statistical review.
- **Completion criteria:** Every unknown is testable and SHP-AC-002 passes.
