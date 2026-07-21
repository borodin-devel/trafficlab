# ON/OFF Pareto Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Mathematical Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define alternating heavy-tail process

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve period and packet behavior

- **Objective:** Satisfy TOP-AC-002.
- **Implementation:** Define random variables/support, initial state,
  period boundary, ON arrivals/sizes, fitting/censoring, schema, stops,
  deterministic sampling, complexity, lineage, and limitations.
- **Affected files:** model SAD/SRS/CONFIGS and ordered math docs.
- **Dependencies:** literature review and traffic-model protocol.
- **Outputs:** Complete reviewed specification and worked examples.
- **Tests:** Distribution quantiles/moments, tail diagnostics, boundaries,
  censoring, deterministic sampling, statistical goodness, registry gate.
- **Validation:** Independent formula/library comparison and expert review.
- **Completion criteria:** Every unknown is testable and TOP-AC-002 passes.
