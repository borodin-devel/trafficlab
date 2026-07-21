# Backup Script Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Scoped Read-Only Backup

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement backup plan and adapters

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Classify, read, display, and protect restoration

- **Objective:** Produce a safe rollback input without mutation.
- **Implementation:** Validate identity/value classification/allowlist, execute
  argument-vector readers, display only public diffs, atomically serialize
  protected restoration data to verified mode-`0600` storage, and record its
  relative reference/digest in the ordinary manifest.
- **Affected files:** `scripts/backup_system_configuration.sh`; `tests/scripts/backup_system_configuration/`.
- **Dependencies:** setup manifest format and approved readers.
- **Outputs:** Validated backup record or explicit failure.
- **Tests:** Scope, classification, public diff, protected sentinel,
  traversal/symlink path, type/owner/mode/digest, fake reader, failure, quoting,
  and no-mutation tests.
- **Validation:** Compare exact public output/private record/manifest, scan every
  non-private sink for sentinels, and inspect the side-effect spy log.
- **Completion criteria:** SBK-AC-001 through SBK-AC-003 pass.
