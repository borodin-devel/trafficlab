# Packet-Train Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Behavioral Research Specification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define train process and boundaries

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve before implementation

- **Objective:** Satisfy TPT-AC-002.
- **Implementation:** Define train start/count/timing/size variables,
  overlap and stop boundaries, fitting, schema, seed/determinism, resource
  limits, lineage, generation, and examples.
- **Affected files:** model SAD/SRS/CONFIGS and ordered design docs.
- **Dependencies:** traffic-model protocol and research evidence.
- **Outputs:** Complete reviewed behavioral specification.
- **Tests:** Hand trains, zero/equal timestamps, boundary/partial train,
  statistical distributions, fixed seed, registry gate.
- **Validation:** Independent simulation review and explicit scenario matrix.
- **Completion criteria:** Every unknown is resolved/testable and TPT-AC-002 passes.
