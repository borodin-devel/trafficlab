# FARIMA Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Mathematical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Resolve model and validation contract

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Define before implementation

- **Objective:** Satisfy TFA-AC-002 without guessing from the name.
- **Implementation:** Select event domain, FARIMA convention/equations,
  estimation, stability domains, finite-history approximation, size coupling,
  generation, schema, dependencies, complexity, and lineage.
- **Affected files:** FARIMA SAD/SRS/CONFIGS and ordered math documents.
- **Dependencies:** literature review and traffic-model protocol.
- **Outputs:** Reviewed complete mathematical specification and examples.
- **Tests:** Independent calculation, stationarity/invertibility, numerical edge,
  deterministic fixture, and registry-gate test designs.
- **Validation:** Expert review against cited authoritative formulation.
- **Completion criteria:** Every unresolved item is testable and TFA-AC-002 passes.
