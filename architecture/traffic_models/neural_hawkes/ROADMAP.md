# Neural Hawkes Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Probability and Causal Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement representation and normalized laws

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Verify causal likelihood components

- **Objective:** Satisfy TNH-AC-001.
- **Implementation:** Add event normalization, embeddings, strict masks,
  empty history, time/mark mixture parameterization, likelihood, and sampling.
- **Affected files:** `src/trafficlab/traffic_models/neural_hawkes/`; tests.
- **Dependencies:** deterministic neural runtime and common neural preparation.
- **Outputs:** Valid causal next-event distribution interface.
- **Tests:** Independent formulas, normalization, masks/leakage, zero IAT, rounding, extremes.
- **Validation:** Compare with hand calculations and numerical integration.
- **Completion criteria:** TNH-AC-001 and probability requirements pass.

## [PLAN] STAGE 2 — Deterministic Candidate Fitting

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Train, validate, select, and serialize

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Reproduce tiny fixture weights

- **Objective:** Produce one deterministic trained artifact.
- **Implementation:** Add window adapter, seeded initialization/batching,
  immutable candidate-before-preparation transition, optimizer, finite checks,
  checkpoint selection, stopping, canonical weights/lineage.
- **Affected files:** model fitting and tests.
- **Dependencies:** Stage 1, configuration, artifact/lineage libraries.
- **Outputs:** Valid trained self-describing model.
- **Tests:** Tiny fixture, builder immutability, split-before-windows,
  checkpoint tie, patience, non-finite failures, path/hash/schema.
- **Validation:** Repeat training and compare exact epoch, metrics, and weight hash.
- **Completion criteria:** Fitting parts of TNH-AC-002/003 pass.

## [PLAN] STAGE 3 — Causal Generation Integration

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 3.1 — Emit reproducible PCAPNG events

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 3.1.1 — Generate under count and duration stops

- **Objective:** Complete TNH-AC-002 and TNH-AC-003.
- **Implementation:** Add trained-file resolution, seeded autoregressive
  loop, bounded history, conversion validation, stop and packet/byte/proposal
  limit handling, application adapter.
- **Affected files:** generator adapter and integration tests.
- **Dependencies:** Stage 2 and traffic generation application.
- **Outputs:** Deterministic timestamp/length events and PCAPNG.
- **Tests:** Golden event sequence, stops, zero-IAT proposal exhaustion,
  untrained/invalid weights, and runtime failures.
- **Validation:** Compare repeated events/artifact hashes and causal trace.
- **Completion criteria:** All TNH acceptance criteria pass.
