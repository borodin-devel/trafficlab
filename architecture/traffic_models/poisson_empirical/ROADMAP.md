# Poisson Empirical Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Reference Preparation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Extract canonical size frequencies

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Validate and count original lengths

- **Objective:** Satisfy TPE-AC-001.
- **Implementation:** Stream reference, validate Ethernet/original lengths,
  count/sort sizes after candidate application, hash source, validate and
  serialize a distinct generation-ready model without editing the builder.
- **Affected files:** `src/trafficlab/traffic_models/poisson_empirical/`; tests.
- **Dependencies:** PCAPNG, lineage, model protocol, Poisson family.
- **Outputs:** Prepared self-contained frequency model.
- **Tests:** Builder immutability, candidate-before-extraction, exact count,
  truncation, unsupported link/length, ordering, and hash tests.
- **Validation:** Compare table and source hash with fixture.
- **Completion criteria:** TPE-AC-001 and the preparation portions of
  TPE-AC-003 pass.

## [PLAN] STAGE 2 — Seeded Generation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Sample categorical sizes and Poisson timing

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Implement complete model generator

- **Objective:** Satisfy TPE-AC-002.
- **Implementation:** Add schema/builder, weighted categorical sampler,
  generation-ready enforcement, exponential IATs, stop enforcement, and
  deterministic event order.
- **Affected files:** model generator and tests.
- **Dependencies:** Stage 1.
- **Outputs:** Event generator from self-contained model.
- **Tests:** Golden seed, weighted proportions, stops, invalid model files.
- **Validation:** Compare exact golden sequence and statistical sanity bounds.
- **Completion criteria:** All TPE requirements and TPE-AC-001 through
  TPE-AC-003 pass.
