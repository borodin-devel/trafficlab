# Markov Renewal Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Observation and State Preparation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement all state modes

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Derive, classify, count, and serialize

- **Objective:** Satisfy TMR-AC-001 and TMR-AC-003 preparation cases.
- **Implementation:** Add source validation, observations, quantile/exact/
  cluster/manual state builders after candidate application, canonical IDs,
  learned counts, generation-ready model validator, and immutable builder lineage.
- **Affected files:** `src/trafficlab/traffic_models/markov_renewal/`; tests.
- **Dependencies:** PCAPNG, lineage, maintained k-means library, model protocol.
- **Outputs:** Self-contained prepared model.
- **Tests:** Hand fixtures, builder immutability, candidate-before-state
  construction, ties, zero variance/IAT, gaps/overlaps, impossible counts, hashes.
- **Validation:** Compare exact state/emission/transition tables and canonical TOML.
- **Completion criteria:** TMR-AC-001, the preparation portion of TMR-AC-004,
  and relevant TMR-AC-003 cases pass.

## [PLAN] STAGE 2 — Seeded Generation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Sample learned renewal chain

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Emit observed pairs under stop policy

- **Objective:** Reproduce deterministic self-contained generation.
- **Implementation:** Add weighted state/emission/transition samplers,
  dead-end restart, count/duration stops, packet/byte/proposal limits, event
  validation, and lineage.
- **Affected files:** generator and tests.
- **Dependencies:** Stage 1.
- **Outputs:** Ordered timestamp/length events.
- **Tests:** Golden seed, dead end, zero IAT, each stop, packet/byte/proposal
  boundaries, infinite zero-IAT proposal stream, and corrupt learned data.
- **Validation:** Remove source file and reproduce exact expected events.
- **Completion criteria:** TMR-AC-002, TMR-AC-004, and remaining TMR-AC-003 pass.
