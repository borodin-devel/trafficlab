# Infrastructure Roadmap

Part of the [central Trafficlab roadmap](../project/ROADMAP.md).

## [PLAN] STAGE 1 — Repository Toolchain

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Create Python build and check configuration

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Establish repeatable local checks

- **Objective:** Make one command set install, lint, type-check, test, and build.
- **Implementation:** Add `pyproject.toml`, uv lock, pytest, coverage,
  pyright, ruff, and setuptools configuration.
- **Affected files:** repository configuration and test bootstrap.
- **Dependencies:** Python 3.12 and uv.
- **Outputs:** Locked environment and executable check commands.
- **Tests:** Clean-environment install, sample unit test, type and lint fixtures.
- **Validation:** Execute every configured command from a clean environment.
- **Completion criteria:** INF-AC-001 passes with documented commands.

## [PLAN] STAGE 2 — Continuous Integration and Documentation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Automate all mandatory gates

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Add CI and corpus validation

- **Objective:** Enforce identical checks for every proposed change.
- **Implementation:** Configure CI, cache safely, validate docs and build.
- **Affected files:** CI workflow and documentation checker.
- **Dependencies:** Stage 1.
- **Outputs:** Mandatory reproducible pipeline results.
- **Tests:** Deliberately broken link, duplicate ID, failing test, and build failure fixtures.
- **Validation:** Confirm each injected defect fails its intended gate.
- **Completion criteria:** INF-AC-002 passes and all normal gates succeed.
