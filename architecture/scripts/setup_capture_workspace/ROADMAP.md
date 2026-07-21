# Setup Script Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Manifest-First Transaction Plan

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement pure setup planning

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Validate identity and enumerate actions

- **Objective:** Produce deterministic displayed and manifest-recorded actions.
- **Implementation:** Define manifest, ownership, public/protected
  classification and display, private backup reference/digest, durable-change
  backup requirement, and action order.
- **Affected files:** setup script planner and `tests/scripts/setup_capture_workspace/`.
- **Dependencies:** backup record and workspace design.
- **Outputs:** Valid setup plan and pre-mutation manifest.
- **Tests:** Identity, allowlist, ordering, protected sentinel/redaction,
  private backup reference/digest, durable backup, and global-resource rejection.
- **Validation:** Compare displayed plan and manifest actions exactly.
- **Completion criteria:** Planning portions of SST-AC-001 and SST-AC-003 pass.

## [PLAN] STAGE 2 — Privileged Execution

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Apply plan and start ordinary-user invoker

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Execute with failure retention

- **Objective:** Create one ready workspace without broad cleanup.
- **Implementation:** Execute argument vectors, record outcomes, stop on failure, start invoker, set state.
- **Affected files:** `scripts/setup_capture_workspace.sh`; fake integration tests; manual verification.
- **Dependencies:** Stage 1 and explicit operator authority.
- **Outputs:** Ready or diagnosable failed workspace.
- **Tests:** Fake success/failure at every action; manual supported-host run.
- **Validation:** Verify manifest, owner, invoker, resource scope, and final state.
- **Completion criteria:** SST-AC-001 through SST-AC-003 pass.
