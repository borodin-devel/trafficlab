# Poisson Uniform Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Validated Deterministic Model

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement schema, sampler, and builder

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Generate baseline event sequences

- **Objective:** Satisfy TPU-AC-001 and TPU-AC-002.
- **Implementation:** Add versioned validator, normal builder, canonical
  TOML, immutable deterministic finalization, generation-ready validation,
  seeded exponential/integer sampling, and stop enforcement.
- **Affected files:** `src/trafficlab/traffic_models/poisson_uniform/`; tests.
- **Dependencies:** traffic-model protocol and Poisson family.
- **Outputs:** Model builder/validator and event generator.
- **Tests:** Golden seed, builder immutability/rejection, finalization lineage,
  distribution invariants, endpoints, stops, and invalid schemas.
- **Validation:** Compare exact golden events and statistical sampling sanity.
- **Completion criteria:** All TPU requirements and acceptance criteria pass.
