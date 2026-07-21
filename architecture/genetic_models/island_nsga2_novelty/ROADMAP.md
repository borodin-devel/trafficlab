# Island NSGA-II Novelty Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Resolve Mathematical and Configuration Gaps

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Specify descriptor, archive, migration, and winner policies

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Complete the selectable strategy contract

- **Objective:** Resolve every item in CONFIGS.md without inventing behavior in code.
- **Implementation:** Define formulas, scales, distance, archive rule,
  migration failure, stopping, score-direction mapping, and winner extraction.
- **Affected files:** ordered algorithm docs, CONFIGS.md, SRS.md, training config.
- **Dependencies:** similarity score metadata and traffic-model protocol.
- **Outputs:** Complete validated strategy schema and mathematical examples.
- **Tests:** Hand-calculated descriptor, dominance, crowding, novelty, archive, migration examples.
- **Validation:** Independent calculation review and configuration boundary matrix.
- **Completion criteria:** No unresolved setting remains and GIN-AC-003 gate opens.

## [PLAN] STAGE 2 — Pure Multi-Island Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Implement ranking, novelty, archive, and migration

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Reproduce golden island traces

- **Objective:** Deterministically evolve bounded mixed-model islands.
- **Implementation:** Add identities, fronts, crowding, descriptors,
  novelty, archives, compatible operators, ring migration, and lineage.
- **Affected files:** `src/trafficlab/genetic_models/island_nsga2_novelty/`; tests.
- **Dependencies:** Stage 1.
- **Outputs:** Pure strategy transition API.
- **Tests:** Golden matrices, property invariants, fixed seeds, failures, mixed models.
- **Validation:** Compare complete generation/archive/migration traces.
- **Completion criteria:** GIN-AC-001 and GIN-AC-002 pass.

## [PLAN] STAGE 3 — Training Integration

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 3.1 — Add probes and resource-bounded evaluation

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 3.1.1 — Execute complete strategy run

- **Objective:** Publish a validated winner and full strategy lineage.
- **Implementation:** Integrate deterministic probes, target evaluation,
  scheduler, failure policy, stopping/winner, reports, and atomic publication.
- **Affected files:** genetic training adapter and integration tests.
- **Dependencies:** Stage 2, generation, similarity, resource library.
- **Outputs:** Winning model, Pareto/ranking report, archive/migration lineage.
- **Tests:** Fake-child, completion-order, resource, failure, end-to-end tests.
- **Validation:** Repeat fixed run and compare all output hashes and decisions.
- **Completion criteria:** GIN-AC-001 through GIN-AC-003 pass.
