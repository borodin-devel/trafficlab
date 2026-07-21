# Rollback Script Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Manifest-Scoped Reverse Plan

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Build idempotent rollback decisions

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Validate and reverse recorded actions

- **Objective:** Derive exact safe reverse order from one manifest.
- **Implementation:** Parse/validate identity and ownership, validate protected
  contained no-follow relative path/type/owner/mode/digest, map actions to
  reversals, handle absent resources, and reject unlisted/global targets.
- **Affected files:** rollback planner; `tests/scripts/rollback_capture_workspace/`.
- **Dependencies:** setup manifest schema and restoration records.
- **Outputs:** Deterministic rollback plan.
- **Tests:** Ordering, partial, absent, malformed, protected traversal/symlink/
  substitution/mode/owner/digest/sentinel, global-target, and idempotence tests.
- **Validation:** Compare plan with reverse manifest order and allowlist.
- **Completion criteria:** Planning portions of SRB-AC-001 and SRB-AC-002 pass.

## [PLAN] STAGE 2 — Privileged Reverse Execution

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Apply and retain outcomes

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Execute manifest actions safely

- **Objective:** Restore state or leave a diagnosable retry point.
- **Implementation:** Read protected restoration only with equivalent authority,
  execute argument vectors, record redacted outcomes, continue/stop only by
  documented policy, and render final remaining state.
- **Affected files:** `scripts/rollback_capture_workspace.sh`; fake integration and manual tests.
- **Dependencies:** Stage 1 and operator authority.
- **Outputs:** Complete action log and restored/remaining state.
- **Tests:** Failure injection, repeated run, public/protected restoration,
  sentinel non-disclosure, and fake privileged commands.
- **Validation:** Verify only planned actions occurred and diagnostics permit retry.
- **Completion criteria:** SRB-AC-001 through SRB-AC-003 pass.
