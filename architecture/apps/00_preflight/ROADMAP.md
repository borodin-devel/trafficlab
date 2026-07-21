# Preflight Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Deterministic Readiness Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define request and decision contracts

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement read-only assessment

- **Objective:** Produce a useful readiness decision without side effects.
- **Implementation:** Consume the shared typed capture request, define
  observations/findings/blocker aggregation, bind its exact digest, validate
  the version 1 readiness decision, and implement package/status publication.
- **Affected files:** `src/trafficlab/apps/preflight/`; `tests/apps/preflight/`.
- **Dependencies:** configuration, artifact I/O, lineage, capture request and readiness contracts.
- **Outputs:** Validated readiness artifact and retained diagnostics.
- **Tests:** Table, property, request mutation/path/mode, missing-resource,
  readiness golden, detached-publication, and side-effect-spy tests.
- **Validation:** Run fixtures for every assessment scope and inspect exact blockers.
- **Completion criteria:** PRE-AC-001 through PRE-AC-003 pass.

## [PLAN] STAGE 2 — Supported-Environment Integration

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Add Linux observation adapters

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Verify prepared workspace observations

- **Objective:** Assess a real supported workspace without mutation.
- **Implementation:** Add read-only Linux adapters for manifest, invoker,
  permissions, tools, network, bridges, storage, and resources.
- **Affected files:** preflight shell and integration tests.
- **Dependencies:** Stage 1 and workspace script contracts.
- **Outputs:** Supported WSL2 readiness report.
- **Tests:** Fake-command integration plus manual prepared-workspace verification.
- **Validation:** Compare report with workspace verification script results.
- **Completion criteria:** All mandatory checks agree and no mutation occurs.
