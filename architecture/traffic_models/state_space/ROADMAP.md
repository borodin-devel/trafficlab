# State-Space Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Mathematical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Select state-space family and inference

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve complete model contract

- **Objective:** Satisfy TSS-AC-002.
- **Implementation:** Define equations/variables/domains, latent dimension,
  transition/emission, initialization, inference, fitting, stability, schema,
  seed/stops, generation, complexity, lineage, and limitations.
- **Affected files:** model SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** literature review and traffic-model protocol.
- **Outputs:** Reviewed mathematical and interface specification.
- **Tests:** Hand filter/smoother examples, stability, likelihood, numerical
  extremes, deterministic fit/sample, registry gate.
- **Validation:** Independent implementation comparison and expert review.
- **Completion criteria:** Every unknown is testable and TSS-AC-002 passes.
