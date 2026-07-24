# Configuration Library Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [DONE] STAGE 1 — Resolution and Startup Record

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.
- **Evidence:** Step 1.1 is `[DONE]` with deterministic resolver, secure attempt
  shell, canonical launch records, and full repository quality evidence.

### [DONE] STEP 1.1 — Implement shared configuration core

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.
- **Evidence:** Substep 1.1.1 covers CFG-AC-001 through CFG-AC-003.

#### [DONE] SUBSTEP 1.1.1 — Resolve and verify every configuration path

- **Objective:** Satisfy CFG-AC-001 through CFG-AC-003.
- **Implementation:** Define managed-attempt validation, collision-safe direct
  attempt creation, schema adapter, selectors, TOML parse, precedence, domain
  validation hook, canonical startup serialization, and shell.
- **Affected files:** `src/trafficlab/libs/configuration/`; `tests/libs/configuration/`.
- **Dependencies:** artifact I/O and Python TOML support.
- **Outputs:** Versioned resolver and startup-record writer.
- **Tests:** Table, property, malformed-file, unknown-key, attempt
  path/type/owner/mode/emptiness/containment, collision, and redaction-boundary tests.
- **Validation:** Run full precedence matrix and inspect canonical records.
- **Completion criteria:** All SRS requirements and acceptance criteria pass.
- **Evidence:** Configuration unit tests cover precedence, selected source,
  validation, managed/direct attempt paths, collision handling, canonical
  resolved/failure `launch.toml` records, and record identities. The repository
  quality gate verifies all tests, 100% coverage, Ruff, Pyright, corpus rules,
  whitespace, and wheel build.
