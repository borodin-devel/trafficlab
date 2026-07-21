# Rate Cross-Correlation Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Mathematical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define shift-tolerant rate comparison

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve before implementation

- **Objective:** Satisfy SXC-AC-002.
- **Implementation:** Define rate vectors, lag domain, correlation,
  normalization/ties/aggregation, score, edges, constant series, schema,
  numerical algorithm, complexity, diagnostics, and examples.
- **Affected files:** method SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** temporal extraction and literature review.
- **Outputs:** Complete reviewed method specification.
- **Tests:** Hand shifted vectors, zero/constant, boundaries, FFT/direct agreement,
  deterministic ties, registry gate.
- **Validation:** Independent calculations and expert review.
- **Completion criteria:** Every unknown is testable and SXC-AC-002 passes.
