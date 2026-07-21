# MAP/BMAP Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Mathematical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define valid MAP/BMAP semantics

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve matrices, fitting, batches, and generation

- **Objective:** Satisfy TMB-AC-002.
- **Implementation:** Define variables/generator constraints, stationary
  law, batch timestamps/order, marks, fitting, canonicalization, schema, stops,
  numerical algorithms, resource bounds, lineage, and examples.
- **Affected files:** MAP/BMAP SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** literature review and traffic-model protocol.
- **Outputs:** Reviewed complete testable specification.
- **Tests:** Matrix validity, stationary law, likelihood, batch fixtures,
  numerical stability, deterministic canonicalization, registry gate.
- **Validation:** Independent library/calculation comparison and expert review.
- **Completion criteria:** No unresolved behavior remains and TMB-AC-002 passes.
