# Similarity Result Contract Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Extensible Result Schema

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define common and method-specific validation

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement shared envelope and registered detail schemas

- **Objective:** Preserve one strict common result with method-owned details.
- **Implementation:** Define schema/version, canonical TOML, score metadata,
  input/method/dependency lineage, detail registry, detached non-self hash
  rules, and compatibility.
- **Affected files:** contract schemas, shared validators, contract tests.
- **Dependencies:** lineage library, similarity methods, evaluation and training.
- **Outputs:** Golden result corpus and shared validator.
- **Tests:** Per-method golden/mutation, self-digest rejection, orphan/status,
  non-finite, unknown-field, and lineage tests.
- **Validation:** Run all fixtures through producer and consumer validators.
- **Completion criteria:** SMR-AC-001 and SMR-AC-002 pass.
