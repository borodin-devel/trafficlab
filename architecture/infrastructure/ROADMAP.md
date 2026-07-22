# Infrastructure Roadmap

Part of the [central Trafficlab roadmap](../project/ROADMAP.md).

## [DONE] STAGE 1 — Repository Toolchain

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.
- **Evidence:** Step 1.1 and Substep 1.1.1 are `[DONE]` with clean-environment,
  unit, integration, coverage, static-analysis, and package-build evidence.

### [DONE] STEP 1.1 — Create Python build and check configuration

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.
- **Evidence:** Substep 1.1.1 passes every configured local check and INF-AC-001.

#### [DONE] SUBSTEP 1.1.1 — Establish repeatable local checks

- **Objective:** Make one command set install, lint, type-check, test, and build.
- **Implementation:** Add `pyproject.toml`, uv lock, pytest, coverage,
  pyright, ruff, and setuptools configuration.
- **Affected files:** repository configuration and test bootstrap.
- **Dependencies:** Python 3.12 and uv.
- **Outputs:** Locked environment and executable check commands.
- **Tests:** Clean-environment install, sample unit test, type and lint fixtures.
- **Validation:** Execute every configured command from a clean environment.
- **Completion criteria:** INF-AC-001 passes with documented commands.
- **Evidence:** `pyproject.toml` and `uv.lock` define and lock the Python 3.12
  environment; `tools/quality.py` exposes the documented fail-fast interface.
  A source copy without an environment or generated metadata passed locked
  synchronization, Ruff formatting and linting, strict pyright, 9 unit tests,
  1 bundled-runtime integration test, 100% package coverage, reproducible
  wheel builds, isolated wheel installation, and package import. Pyright used
  the locked Node wheel with global Node disabled. Two identical builds
  produced SHA-256
  `a2c81a53b9e3cd47cd3159719d2436414a862ba78b0706e6d4230f6d17f0b956`.

## [DONE] STAGE 2 — Continuous Integration and Documentation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.
- **Evidence:** Step 2.1 and Substep 2.1.1 are `[DONE]`; the standard-library
  corpus validator and secure locked CI satisfy INF-AC-002 with the defect,
  clean-clone, and reproducibility evidence below.

### [DONE] STEP 2.1 — Automate all mandatory gates

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.
- **Evidence:** Substep 2.1.1 passes deliberately defective corpus and delivery
  fixtures, validates the committed corpus, and runs the same locked aggregate
  gate locally and in CI.

#### [DONE] SUBSTEP 2.1.1 — Add CI and corpus validation

- **Objective:** Enforce identical checks for every proposed change.
- **Implementation:** Configure CI, cache safely, validate docs and build.
- **Affected files:** CI workflow and documentation checker.
- **Dependencies:** Stage 1.
- **Outputs:** Mandatory reproducible pipeline results.
- **Tests:** Deliberately broken link, duplicate ID, failing test, and build failure fixtures.
- **Validation:** Confirm each injected defect fails its intended gate.
- **Completion criteria:** INF-AC-002 passes and all normal gates succeed.
- **Evidence:** `tools/validate_architecture.py` provides the standard-library
  validator, with stable fixtures for broken links and fragments, duplicate
  requirement identifiers, roadmap and registry rules, every regular-file
  name, AC-only SRS documents, bare central-roadmap links, untrusted-file
  replacement, and CommonMark/GFM block state across wrapped inline and
  reference links, lists, quotes, fences, indented code, and structural tables.
  It validates all 319 architecture Markdown files. The complete-tree
  whitespace gate compares both the prospective index and tracked working tree
  with Git's empty tree, so committed content is checked in a clean checkout.
  The GitHub Actions workflow pins checkout to
  `3d3c42e5aac5ba805825da76410c181273ba90b1` and setup-uv to
  `11f9893b081a58869d3b5fccaea48c9e9e46f990`, grants only read access to
  repository contents, disables persisted credentials, selects uv 0.11.25,
  performs locked synchronization, and runs the same
  `uv run --locked python tools/quality.py all` command used locally. The
  focused defect and policy suite passed 201 tests; the full aggregate gate
  passed 203 tests with 100% package coverage, clean Ruff and Pyright checks,
  and silent corpus, complete-tree whitespace, and diff-hygiene validation.
  Repeated local and clean-clone wheel builds produced identical SHA-256
  `76659dfe5d626a361d94ade34e1f6c232d48f51820760a71f16ed15c98e165f2`.
  A fresh local clone of committed implementation head `77a3d989`, with no
  pre-existing environment, completed locked synchronization and passed the
  full aggregate gate.
