# MMPP Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Mathematical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Resolve hidden-state Poisson model

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Define before implementation

- **Objective:** Satisfy TMM-AC-002.
- **Implementation:** Define state process/equations, valid parameter
  domain, initial law, observation likelihood, fitting, canonical labels, mark
  law, generation, schema, stops, complexity, and lineage.
- **Affected files:** MMPP SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** literature review and traffic-model protocol.
- **Outputs:** Complete reviewed mathematical specification.
- **Tests:** Hand likelihood, matrix/rate boundaries, identifiability,
  deterministic sample, numerical extremes, registry gate.
- **Validation:** Compare against independent implementation and expert review.
- **Completion criteria:** Every unknown is resolved/testable and TMM-AC-002 passes.
