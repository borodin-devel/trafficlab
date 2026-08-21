# Codebase Compartmentalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize Trafficlab production and test code by shared ownership and
the five documented pipeline subsystems without changing behavior.

**Architecture:** Move cross-stage foundations into `trafficlab.common`; give
preflight, capture, fitting, generation, and comparison their own packages; and
nest genetic search, traffic models, and similarity methods under their owning
stage. Preserve test kind as the first test-tree axis and mirror production
subsystems as the second axis.

**Tech Stack:** CPython 3.12.3, uv, pytest, Ruff, strict Pyright, Git, Docker
Compose integration tests.

**Spec:** `docs/superpowers/specs/2026-08-21-codebase-compartmentalization-design.md`

## Global Constraints

- Preserve all scientific behavior, artifact schemas, CLI commands,
  configuration, deterministic seeds, stage order, failure authority, and
  cleanup semantics.
- Keep one Python process and the existing two production capture containers;
  add no runtime dependency, service, compatibility shim, or parallel import
  path.
- Use `apply_patch` for hand-authored changes and `git mv` plus a deterministic
  bulk rewrite for mechanical moves/import changes.
- Retain all 3,830 baseline tests, including the 19 tests normally deselected
  by default options, and add three direct layout-contract tests.
- Run strict Pyright, Ruff, branch-aware coverage at or above 90%, deterministic
  artifact checkers, available external tests, and independent review.
- Commit each independently verified task and keep the working tree clean at
  completion.

---

### [TASK-1-cfe456b3] Establish production subsystem packages

**Files:**

- Create: `tests/unit/pipeline/test_source_layout.py`
- Create: `src/trafficlab/common/__init__.py`
- Create: `src/trafficlab/preflight/__init__.py`
- Create: `src/trafficlab/capture/__init__.py`
- Create: `src/trafficlab/fitting/__init__.py`
- Create: `src/trafficlab/generation/__init__.py`
- Create: `src/trafficlab/comparison/__init__.py`
- Move: the 21 flat/shared and stage paths listed in Step 3
- Move: `src/trafficlab/genetic` to `src/trafficlab/fitting/genetic`
- Move: `src/trafficlab/models` to `src/trafficlab/generation/models`
- Move: `src/trafficlab/similarity` to `src/trafficlab/comparison/similarity`
- Modify: every Python import in `src/trafficlab`, `tests`, and `scripts`

**Interfaces:**

- Consumes: all current function, class, protocol, enum, and constant
  signatures unchanged.
- Produces: the exact new import paths in the design; package initializers
  expose no compatibility aliases.

- [x] **[STEP-1-7f5b16a2] Write the failing production-layout contract**

  Add a data-driven test whose expected owned files are:

  ```python
  EXPECTED = {
      "common": {
          "compatibility.py",
          "config.py",
          "config_io.py",
          "errors.py",
          "scapy_io.py",
          "scientific_schema.py",
          "statistics.py",
          "trace.py",
      },
      "preflight": {"stage.py"},
      "capture": {
          "cleanup.py",
          "compose.py",
          "docker_cli.py",
          "policy.py",
          "stage.py",
          "validation.py",
      },
      "fitting": {"stage.py"},
      "generation": {"stage.py"},
      "comparison": {"stage.py"},
  }
  FORBIDDEN_ROOT_MODULES = {
      "capture.py",
      "capture_policy.py",
      "capture_validation.py",
      "cleanup.py",
      "comparison.py",
      "compatibility.py",
      "compose.py",
      "config.py",
      "config_io.py",
      "docker_cli.py",
      "errors.py",
      "fitting.py",
      "generation.py",
      "preflight.py",
      "scapy_io.py",
      "scientific_schema.py",
      "statistics.py",
      "trace.py",
  }
  FORBIDDEN_ROOT_PACKAGES = {"genetic", "models", "similarity"}
  ```

  Assert that each expected file exists, every forbidden path is absent, and
  each of `fitting/genetic`, `generation/models`, and
  `comparison/similarity` contains its current Python inventory.

- [x] **[STEP-2-4d9639e4] Run the contract and verify RED**

  Run:

  ```bash
  uv run --locked pytest -q -n 0 tests/unit/pipeline/test_source_layout.py
  ```

  Expected: failure listing the absent subsystem paths and still-present flat
  modules.

- [x] **[STEP-3-39681ab6] Move production files with history**

  Create the six package directories, then use `git mv` for this exact map:

  ```text
  compatibility.py      -> common/compatibility.py
  config.py             -> common/config.py
  config_io.py          -> common/config_io.py
  errors.py             -> common/errors.py
  scapy_io.py           -> common/scapy_io.py
  scientific_schema.py  -> common/scientific_schema.py
  statistics.py         -> common/statistics.py
  trace.py              -> common/trace.py
  preflight.py          -> preflight/stage.py
  capture.py            -> capture/stage.py
  capture_policy.py     -> capture/policy.py
  capture_validation.py -> capture/validation.py
  cleanup.py            -> capture/cleanup.py
  compose.py            -> capture/compose.py
  docker_cli.py         -> capture/docker_cli.py
  fitting.py            -> fitting/stage.py
  genetic/              -> fitting/genetic/
  generation.py         -> generation/stage.py
  models/               -> generation/models/
  comparison.py         -> comparison/stage.py
  similarity/           -> comparison/similarity/
  ```

  Each new top-level package initializer contains only its ownership docstring,
  for example:

  ```python
  """Traffic comparison stage and similarity methods."""
  ```

- [x] **[STEP-4-1cdb7395] Rewrite every live Python import atomically**

  Apply this exact module map to Python code under `src`, `tests`, and `scripts`:

  ```text
  trafficlab.compatibility      -> trafficlab.common.compatibility
  trafficlab.config             -> trafficlab.common.config
  trafficlab.config_io          -> trafficlab.common.config_io
  trafficlab.errors             -> trafficlab.common.errors
  trafficlab.scapy_io           -> trafficlab.common.scapy_io
  trafficlab.scientific_schema  -> trafficlab.common.scientific_schema
  trafficlab.statistics         -> trafficlab.common.statistics
  trafficlab.trace              -> trafficlab.common.trace
  trafficlab.preflight          -> trafficlab.preflight.stage
  trafficlab.capture            -> trafficlab.capture.stage
  trafficlab.capture_policy     -> trafficlab.capture.policy
  trafficlab.capture_validation -> trafficlab.capture.validation
  trafficlab.cleanup            -> trafficlab.capture.cleanup
  trafficlab.compose            -> trafficlab.capture.compose
  trafficlab.docker_cli         -> trafficlab.capture.docker_cli
  trafficlab.fitting            -> trafficlab.fitting.stage
  trafficlab.genetic            -> trafficlab.fitting.genetic
  trafficlab.generation         -> trafficlab.generation.stage
  trafficlab.models             -> trafficlab.generation.models
  trafficlab.comparison         -> trafficlab.comparison.stage
  trafficlab.similarity         -> trafficlab.comparison.similarity
  ```

  Rewrite imports, monkeypatch target strings, and executable documentation
  strings. Match complete module tokens so `capture` does not corrupt
  `capture_policy` or already-rewritten `capture.validation`.

- [x] **[STEP-5-7523e210] Prove collection and source layout GREEN**

  Run:

  ```bash
  uv run --locked pytest -q -n 0 tests/unit/pipeline/test_source_layout.py
  uv run --locked pytest --collect-only -q
  ```

  Expected: the two layout tests pass and collection reports
  `3813/3832 tests collected (19 deselected)` with no import error.

- [x] **[STEP-6-9ee8e9d6] Run focused production-package checks**

  Run:

  ```bash
  uv run --locked ruff check src/trafficlab tests/unit/pipeline/test_source_layout.py
  uv run --locked pyright src/trafficlab tests/unit/pipeline/test_source_layout.py
  ```

  Expected: both commands pass.

- [x] **[STEP-7-e97ed8c1] Review and commit the production move**

  Request an independent diff review, fix every Critical or Important finding,
  rerun Steps 5–6, then commit:

  ```bash
  git add src tests/unit/pipeline/test_source_layout.py tests scripts
  git commit -m "refactor: compartmentalize source packages"
  ```

### [TASK-2-bb5bcfc4] Mirror subsystem ownership beneath every test scope

**Files:**

- Create: `tests/unit/pipeline/test_test_layout.py`
- Move: all `test_*.py` files using the exact maps in Steps 10–11
- Move: `tests/scientific/oracles.py` with the generation scientific tests
- Modify: affected relative imports and explicit test paths in repository tools

**Interfaces:**

- Consumes: the production import paths established by Task 1.
- Produces: unchanged pytest node behavior under subsystem-owned paths and an
  enforced repository layout contract.

- [x] **[STEP-8-8fe38389] Write the failing test-layout contract**

  Use the exact allowed mapping:

  ```python
  ALLOWED = {
      "unit": {
          "common",
          "preflight",
          "capture",
          "fitting",
          "generation",
          "comparison",
          "pipeline",
          "validation",
          "tooling",
      },
      "integration": {
          "preflight",
          "capture",
          "fitting",
          "generation",
          "comparison",
          "pipeline",
          "validation",
      },
      "docker": {"capture", "pipeline"},
      "internet": {"capture"},
      "property": {"common", "fitting", "generation", "comparison"},
      "scientific": {"fitting", "generation"},
  }
  ```

  For each scope, recursively collect `test_*.py`; assert its first relative
  path component is allowed and that no test file remains directly at the
  scope root.

- [x] **[STEP-9-f11ba95d] Run the contract and verify RED**

  Run:

  ```bash
  uv run --locked pytest -q -n 0 tests/unit/pipeline/test_test_layout.py
  ```

  Expected: failure listing current flat unit, integration, property,
  scientific, Docker, and Internet test files.

- [x] **[STEP-10-385fe29c] Move package-aligned and external test groups**

  Use `git mv` for these exact directory/file groups:

  ```text
  tests/unit/genetic/          -> tests/unit/fitting/genetic/
  tests/unit/models/           -> tests/unit/generation/models/
  tests/unit/similarity/       -> tests/unit/comparison/similarity/
  tests/unit/validation_study/ -> tests/unit/validation/study/
  tests/docker/test_capture_*  -> tests/docker/capture/
  tests/docker/test_run_docker.py -> tests/docker/pipeline/
  tests/internet/test_capture_internet.py -> tests/internet/capture/
  tests/property/test_trace* and test_parser_and_schema_properties.py
      -> tests/property/common/
  tests/property/test_genetic_properties.py -> tests/property/fitting/
  tests/property/test_model_vectorization_properties.py
      -> tests/property/generation/
  tests/property/test_similarity_vectorization_properties.py
      -> tests/property/comparison/
  tests/scientific/probes/ -> tests/scientific/fitting/probes/
  tests/scientific/test_model_validation.py and tests/scientific/oracles.py
      -> tests/scientific/generation/
  ```

- [x] **[STEP-11-08be505c] Move flat unit and integration tests by ownership**

  Move each flat unit file using this complete mapping:

  ```text
  unit/common/
    test_capture_metadata.py test_compatibility.py test_config_io.py
    test_config_schema.py test_config_validation.py
    test_failure_outcome_contract.py test_failure_outcomes.py
    test_scapy_io.py test_statistics.py test_trace.py

  unit/preflight/
    test_docker_preflight.py test_preflight.py
    test_preflight_failure_authority.py

  unit/capture/
    test_capture.py test_capture_failure_context.py
    test_capture_image_identity.py test_capture_policy.py
    test_capture_validation.py test_cleanup.py test_compose.py
    test_docker_cli.py

  unit/fitting/
    test_fit_fixture_generator.py test_fitting.py

  unit/generation/
    test_scientific_rng.py

  unit/comparison/
    test_comparison.py test_similarity_artifact.py

  unit/pipeline/
    test_artifact_schemas.py test_artifacts.py test_cli.py
    test_failure_outcome_public_matrix.py test_package.py test_run.py

  unit/validation/
    test_study_evidence.py test_validation_study_natural_variation.py
    test_validation_study_standalone.py

  unit/tooling/
    test_artifact_schema_generator.py test_docker_fixture_support.py
    test_external_test_control.py test_internet_client.py
    test_repository_layout.py test_scapy_production_benchmark.py
    test_scientific_stack_benchmark.py test_scientific_stack_example.py
    test_scientific_stack_example_run.py
    test_scientific_stack_probe_runner.py
    test_scientific_stack_reduction.py test_test_config_support.py
  ```

  Move integration files using their names and documented stages:

  ```text
  capture_cli, capture_pipeline, cleanup_boundary, process_guard -> capture/
  compare_cli, comparison_pipeline -> comparison/
  full_preflight, preflight_cli -> preflight/
  genetic_fitting -> fitting/
  generate_cli, model_pipeline -> generation/
  pipeline_equivalence, run_pipeline -> pipeline/
  validation_study_collection, validation_study_pipeline -> validation/
  ```

  Preserve each filename and add `__init__.py` only where needed to prevent
  duplicate-module collection; do not alter markers or test bodies.

- [x] **[STEP-12-04f62b41] Update repository-owned test path references**

  Rewrite exact paths in scripts, test support, architecture testing commands,
  and current documentation. Do not rewrite historical plans/specifications.
  Confirm no live Python code names a removed test path:

  ```bash
  rg -n 'tests/(unit|integration|docker|internet|property|scientific)/test_' \
    src tests scripts architecture README.md TASK.md
  ```

  Every result must point to its new subsystem directory or be an intentional
  glob documented by the test runner.

- [x] **[STEP-13-cea7bcb8] Prove inventory, review, and commit the test move**

  Run the layout test and full collection, require all 3,830 baseline tests plus
  the three new layout tests, request independent review, fix all
  Critical/Important findings, and commit:

  ```bash
  uv run --locked pytest -q -n 0 tests/unit/pipeline/test_test_layout.py
  uv run --locked pytest --collect-only -q
  git add tests scripts architecture README.md TASK.md
  git commit -m "test: group suites by subsystem"
  ```

### [TASK-3-68cee564] Repair path-sensitive documentation and deterministic evidence

**Files:**

- Modify: `architecture/SYSTEM.md` and any other current authoritative document
  naming moved modules
- Modify: `TASK.md` current source-path references
- Modify: `scripts/benchmark_scientific_stack.py`
- Modify: `scripts/measure_scientific_stack_reduction.py`
- Modify: moved tooling tests that bind source paths
- Regenerate if required by checkers: `examples/scientific_stack/benchmark.json`
  and `examples/scientific_stack/code_reduction.json`

**Interfaces:**

- Consumes: the new source and test paths from Tasks 1–2.
- Produces: current documentation and checked deterministic records that name
  only existing files while retaining their scientific thresholds and results.

- [x] **[STEP-14-e01d6058] Find every stale live path**

  Run a repository search for each removed module and filesystem path, excluding
  only `docs/superpowers/plans/**` and superseded design specifications. Record
  all current architecture, TASK, script, test, example, and schema hits.

- [x] **[STEP-15-786003df] Run path-sensitive checkers and verify RED**

  Run:

  ```bash
  uv run --locked python scripts/measure_scientific_stack_reduction.py --check
  uv run --locked python scripts/benchmark_scientific_stack.py --check
  uv run --locked python scripts/benchmark_scapy_production.py --check
  ```

  Expected: any checker whose retained source inventory uses old paths fails
  specifically on missing/mismatched source identity; scientific value or
  threshold failures are not expected from a path-only refactor.

- [x] **[STEP-16-8baff31a] Update authoritative path inventories**

  Apply the Task 1 path map to current architecture, `TASK.md`, benchmark source
  inventories, reduction-measurement AST paths, and their unit expectations.
  Keep recorded algorithms, thresholds, timing samples, and before-revision
  evidence unchanged.

- [x] **[STEP-17-8456ba6e] Regenerate only checker-owned current records**

  When a deterministic generator owns a checked record that includes current
  source paths or hashes, run its non-`--check` command once, then rerun with
  `--check`. Do not edit performance samples manually and do not alter accepted
  validation-study evidence bound to an earlier source commit.

- [x] **[STEP-18-0e290fe6] Verify no live old import or path remains**

  Search Python and current docs for old imports and paths. Expected: no result
  outside historical `docs/superpowers/plans/**` and prior design records.
  Confirm `python -m trafficlab --help` and all six command help surfaces still
  expose the existing CLI names.

- [x] **[STEP-19-2143f8d6] Run focused tooling and architecture tests**

  Run all tests under `tests/unit/tooling` plus the two layout tests, then Ruff
  and strict Pyright for `src`, `tests`, and `scripts`. Expected: all pass.

- [x] **[STEP-20-d889eb2f] Review and commit path evidence**

  Request independent review, resolve all Critical/Important findings, repeat
  Steps 18–19, and commit:

  ```bash
  git add architecture TASK.md scripts tests examples/scientific_stack
  git commit -m "docs: align evidence with package layout"
  ```

### [TASK-4-9f53a9fc] Run complete behavior-preservation gates

**Files:**

- Modify only when a gate proves a path-dependent defect: the owning source,
  test, current document, or deterministic fixture from Tasks 1–3
- Record: command output in the implementation handoff and retained local Git
  commits; do not add a completion ledger to `architecture/`

**Interfaces:**

- Consumes: the complete compartmentalized repository.
- Produces: release-level evidence that the move preserved behavior.

- [ ] **[STEP-21-e13f9ec4] Verify locked environment and static gates**

  Run `uv sync --locked --all-groups`, Ruff format check, Ruff lint, and strict
  Pyright using the commands in `architecture/DEVELOPMENT.md`. Expected: pass.

- [ ] **[STEP-22-89cafb79] Run the fast and ordinary bounded suites**

  Use the canonical bounded Fast and Ordinary commands from
  `architecture/DEVELOPMENT.md`. Expected: all non-external tests pass with no
  collection loss and no escaped process scope.

- [ ] **[STEP-23-efe068c5] Run branch-aware coverage**

  Use the canonical four-worker bounded Coverage command. Expected: all tests
  pass and total branch-aware coverage is at least 90%.

- [ ] **[STEP-24-b9ffb354] Run every deterministic checker**

  Run all fixture, schema, scientific-stack reduction, benchmark, example, and
  probe `--check` commands listed in the Release gate. Expected: all pass without
  modifying the tree.

- [ ] **[STEP-25-195fd91c] Run offline real-program validation**

  Run the scientific example command and the bounded offline validation-study
  audit from its source-bound detached clone as prescribed by
  `architecture/DEVELOPMENT.md`. Expected: both pass; retain the exact commit and
  command output in the handoff.

- [ ] **[STEP-26-2d2c27f8] Run available Docker and Internet validation**

  Run the canonical External gate with the repository's credential-free HTTPS
  endpoint. Use uniquely project-scoped Docker resources and bounded cleanup.
  Expected: Docker capture/run and Internet tests pass, or document a proven
  [PROBLEM-C5] external blocker only after all local diagnostics are exhausted.

- [ ] **[STEP-27-22d5c8fe] Commit any gate-proven corrections**

  If Steps 21–26 required changes, rerun the narrow failing gate and its parent
  gate, request independent review, and commit one coherent correction. If no
  file changed, record that no verification-fix commit was necessary.

### [TASK-5-3c7ee99e] Complete independent final review and repository handoff

**Files:**

- Modify only to resolve final Critical or Important findings
- Modify: this plan's checkboxes as each verified item completes

**Interfaces:**

- Consumes: all implementation and verification commits.
- Produces: a clean reviewed branch and concise reproducible evidence handoff.

- [ ] **[STEP-28-25eeff9c] Obtain independent final review**

  Ask a reviewer to inspect the complete diff from `d0405f2` through HEAD for
  ownership mistakes, stale paths, accidental compatibility shims, missing
  tests, behavior changes, and verification gaps. Resolve every Critical or
  Important finding and rerun affected gates.

- [ ] **[STEP-29-ae7a6c2b] Mark the plan accurately and commit completion state**

  Change a checkbox to `[x]` only when its command/evidence has passed. Commit
  the completed plan and any reviewed correction with a terse conventional
  commit message.

- [ ] **[STEP-30-57dac5a4] Verify the completion gate and hand off**

  Confirm `git status --short --branch` is clean, list retained commits, and
  summarize static, test, coverage, deterministic, real-program, external, and
  review evidence. Do not claim completion if any checkbox or required gate is
  unresolved.
