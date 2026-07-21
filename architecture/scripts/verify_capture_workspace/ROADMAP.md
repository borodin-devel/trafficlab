# Verify Script Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Read-Only Workspace Verification

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement observation and decision pipeline

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Inspect and classify workspace state

- **Objective:** Satisfy SVR-AC-001 and SVR-AC-002.
- **Implementation:** Validate inputs, add read-only adapters, compare manifest/observations, order findings, render status.
- **Affected files:** `scripts/verify_capture_workspace.sh`; `tests/scripts/verify_capture_workspace/`.
- **Dependencies:** workspace manifest schema.
- **Outputs:** Deterministic verification report.
- **Tests:** State table, drift, missing observation, fake reader failure, no-side-effect tests.
- **Validation:** Compare exact reports and side-effect logs.
- **Completion criteria:** Both acceptance criteria pass.
