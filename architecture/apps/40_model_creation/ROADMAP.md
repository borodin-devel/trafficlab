# Model Creation Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Registered Model Builders

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Create and publish normal files

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement deterministic builder dispatch

- **Objective:** Satisfy MCR-AC-001 and MCR-AC-002.
- **Implementation:** Add CLI, registry lookup, builder protocol, model
  validation, explicit builder state, canonical TOML, startup record, detached
  hashing, and atomic single-file publication.
- **Affected files:** `src/trafficlab/apps/model_creation/`; tests.
- **Dependencies:** configuration, artifact I/O, lineage, mature model owners.
- **Outputs:** Valid normal model files and typed failures.
- **Tests:** Every model fixture, unknown/stub model, builder immutability,
  generation rejection, orphan output, publication failure, and determinism.
- **Validation:** Reparse each output with its selected model validator and
  verify detached file/launch digests.
- **Completion criteria:** MCR-AC-001 through MCR-AC-003 pass.
