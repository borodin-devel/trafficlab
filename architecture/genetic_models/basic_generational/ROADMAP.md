# Basic Generational Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Candidate and Population Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement deterministic initialization and evaluation state

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Create valid populations and eligibility

- **Objective:** Establish canonical population zero and evaluation barrier.
- **Implementation:** Implement candidate values/IDs, search sampling,
  validation, whole-attempt retry, result ingestion, and eligibility.
- **Affected files:** `src/trafficlab/genetic_models/basic_generational/`; tests.
- **Dependencies:** model protocol, method score metadata, training adapter.
- **Outputs:** Complete evaluated population values.
- **Tests:** Sampling, duplicates, failures, domains, determinism, attempt limit.
- **Validation:** Replay golden seed and check every candidate/result.
- **Completion criteria:** GBG-FR-001 through GBG-FR-003 pass.

## [PLAN] STAGE 2 — Selection and Operators

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Form complete next populations

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Select, cross, mutate, replace, and retry

- **Objective:** Preserve validity and population size through reproduction.
- **Implementation:** Add tournaments, elitism, same-model crossover,
  mutation category, proposals, replacement, fallbacks, and lineage.
- **Affected files:** strategy core and property tests.
- **Dependencies:** Stage 1.
- **Outputs:** Valid next population and operator decisions.
- **Tests:** Every branch, tie, invalid mix, no pool, replacement, exhaustion.
- **Validation:** Property-check invariants and compare golden trace.
- **Completion criteria:** GBG-FR-004 through GBG-FR-008 and GBG-AC-003 pass.

## [PLAN] STAGE 3 — Stopping and Integration

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 3.1 — Select winner and integrate training

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 3.1.1 — Stop, report, and publish

- **Objective:** Complete deterministic runs with diagnosable failures.
- **Implementation:** Add stopping state, best-so-far/winner, report schema,
  fake-child adapter, resource-order restore, and publication.
- **Affected files:** strategy, genetic training adapter, integration tests.
- **Dependencies:** Stages 1–2 and result contract.
- **Outputs:** Winner, ranking, strategy lineage, failure state.
- **Tests:** All stopping/tie/failure cases, completion-order independence, atomicity.
- **Validation:** Repeat fixed result matrix and compare exact artifacts.
- **Completion criteria:** GBG-AC-001 through GBG-AC-003 pass.
