# [PLAN-1-6624568f] Testing Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile Trafficlab's testing architecture, give test helpers focused
ownership, modularize the validation-study suite, and adopt only measured,
quality-equivalent speed improvements.

**Architecture:** `architecture/TESTING.md` owns behavioral evidence while
`architecture/DEVELOPMENT.md` owns executable bounded commands. Thin pytest
wiring delegates to typed modules under `tests/support/`; the validation-study
suite is divided by domain, and serial-versus-parallel coverage is decided from
coverage JSON equivalence rather than elapsed time alone.

**Tech Stack:** Python 3.12, pytest, pytest-xdist, pytest-cov, coverage.py,
Pyright strict mode, Ruff, Bash bounded-process guard, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-19-testing-infrastructure-design.md`

## Global Constraints

- Do not change production behavior, scientific tolerances, seeds, repetition
  counts, mutation matrices, fixture bytes, or external cleanup requirements.
- Keep `integration`, `docker`, and `internet` as the complete marker taxonomy.
- Use `scripts/run_bounded.sh` for every acceptance pytest process tree.
- Maintain at least 90% branch-aware coverage of `trafficlab`.
- Add no production dependency or Node.js application dependency.
- Use RED-GREEN-refactor for support behavior and preserve exact collection
  until the explicitly mapped validation-study file split.
- Use `apply_patch` for hand-authored edits and `uv run --locked` for Python
  tools.

---

### [TASK-1-61c55ee9] Baseline the accepted test system

**Files:**
- Inspect: `architecture/TESTING.md`
- Inspect: `architecture/DEVELOPMENT.md`
- Inspect: `tests/conftest.py`
- Inspect: `tests/unit/test_validation_study.py`

**Interfaces:**
- Consumes: current `pytest --collect-only`, duration, coverage JSON, and Git
  state.
- Produces: before-state counts and timings against which later steps are
  compared.

- [x] **[STEP-1-53539b04] Capture focused helper and collection baselines.** Run
  `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 5m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_external_test_control.py tests/unit/test_docker_fixture_support.py`, then collect non-external tests and record total cases and marker counts.
- [x] **[STEP-2-b556ba81] Record the slow-test baseline.** Run the bounded
  four-worker ordinary command with `--durations=50`, preserving its elapsed
  time and slowest-case table in the implementation notes.
- [x] **[STEP-3-ed688755] Commit the approved design and plan.** Add only the two
  new documents, inspect their diff and immutable labels, then commit with
  `docs: design testing infrastructure refactor`.

### [TASK-2-c62c95eb] Establish one canonical gate model

**Files:**
- Modify: `architecture/TESTING.md`
- Modify: `architecture/DEVELOPMENT.md`
- Modify: `README.md`
- Modify: `architecture/ROADMAP.md` only if a checked gate currently asserts a
  contradictory command.

**Interfaces:**
- Consumes: gate taxonomy from the approved spec.
- Produces: one `DEVELOPMENT.md` command table referenced by testing architecture
  and README.

- [x] **[STEP-4-3553028b] Demonstrate the documentation contradiction.** Use
  `rg` to show parallel coverage in `TESTING.md`, serial normative coverage in
  `DEVELOPMENT.md`, and repeated command blocks in README; retain the commands
  as review evidence rather than adding a documentation-template test.
- [x] **[STEP-5-8f402632] Replace duplicated normative recipes.** Put Focused,
  Fast, Ordinary, Coverage, External, and Release commands in one table in
  `DEVELOPMENT.md`; make `TESTING.md` define obligations and link to the table;
  reduce README to a link plus one focused example.
- [x] **[STEP-6-72aaa9ed] Verify command and marker consistency.** Search all
  current architecture and README text for conflicting `pytest` selections,
  confirm the external command uses `-m "docker or internet"`, and run Ruff
  format/check on the repository.
- [x] **[STEP-7-16b9898a] Commit the gate model.** Inspect the documentation diff
  and commit with `docs: unify testing verification gates`.

### [TASK-3-67843b64] Extract external and Docker support

**Files:**
- Create: `tests/support/__init__.py`
- Create: `tests/support/external.py`
- Create: `tests/support/docker.py`
- Modify: `tests/conftest.py`
- Modify: `tests/unit/test_external_test_control.py`
- Modify: `tests/unit/test_docker_fixture_support.py`
- Modify: `tests/docker/support.py`

**Interfaces:**
- Produces: `external_tests_requested(config: pytest.Config, marker: str) -> bool`,
  `validate_internet_url(value: str | None) -> str`,
  `run_external_command(command: Sequence[str], *, purpose: str, timeout: float) -> subprocess.CompletedProcess[str]`, and
  `require_serial_external_tests(config: pytest.Config) -> None`.
- Produces: typed Docker environment, image, tracker, inspection, overlay, and
  adapter helpers imported by fixture wiring and Docker scenarios.

- [x] **[STEP-8-11eea047] Write RED imports for external support.** Change the
  focused control tests to import the four public functions from
  `tests.support.external`; run them and confirm collection fails because that
  module does not exist.
- [x] **[STEP-9-cb886ce8] Move the smallest external implementation.** Create the
  typed support module, delegate the collection hook and Internet fixture to it,
  remove the corresponding implementation from `conftest.py`, then run the
  focused control tests GREEN.
- [x] **[STEP-10-6c9708ea] Write RED imports for Docker support.** Redirect the
  Docker fixture-support tests to the intended `tests.support.docker`
  interfaces and confirm the missing imports fail before moving code.
- [x] **[STEP-11-996621d8] Move generic Docker machinery.** Extract image
  lifecycle, resource inspection/tracking, endpoint overlay, and adapters;
  retain only thin pytest fixtures in `conftest.py`, and keep scenario config
  writers and log readers in `tests/docker/support.py`.
- [x] **[STEP-12-7f047a84] Run focused GREEN and branch coverage.** Run both
  helper owner modules serially with branch coverage of their complete helper
  functions, followed by Ruff and strict Pyright.
- [x] **[STEP-13-a1d4abab] Prove collection preservation and commit.** Compare
  total collection, marker counts, and exact node IDs with the baseline, then
  commit with `test: separate external support ownership`.

### [TASK-4-c56fe6ef] Extract the canonical configuration fixture

**Files:**
- Create: `tests/support/config.py`
- Modify: `tests/conftest.py`
- Create: `tests/unit/test_test_config_support.py`

**Interfaces:**
- Produces: `valid_config_data(tmp_path: Path) -> dict[str, object]`, returning a
  fresh valid mapping whose run directory is scoped below `tmp_path`.

- [x] **[STEP-14-3509cb61] Write a RED direct builder contract.** Assert two
  calls return equal but independent nested mappings, use distinct run paths,
  and both validate as `ExperimentConfig`; confirm the builder import fails.
- [x] **[STEP-15-1256de4a] Move the fixture data builder.** Create
  `tests.support.config.valid_config_data`, make the pytest fixture delegate to
  it, and remove the large literal from `conftest.py`.
- [x] **[STEP-16-14a56531] Run GREEN and fixture consumers.** Run the new helper
  test plus configuration schema, validation, I/O, and comparison-pipeline
  owners; then run Ruff and strict Pyright.
- [x] **[STEP-17-59e159e0] Prove collection preservation and commit.** Compare
  exact node IDs and marker counts, inspect the diff, and commit with
  `test: isolate configuration fixture builder`.

### [TASK-5-b7fd83f4] Modularize validation-study tests

**Files:**
- Create: `tests/support/validation_study.py`
- Create: `tests/unit/validation_study/__init__.py`
- Create: `tests/unit/validation_study/conftest.py`
- Create: `tests/unit/validation_study/test_protocol.py`
- Create: `tests/unit/validation_study/test_orchestration.py`
- Create: `tests/unit/validation_study/test_audit.py`
- Create: `tests/unit/validation_study/test_prerequisites.py`
- Create: `tests/unit/validation_study/test_audit_boundaries.py`
- Remove: `tests/unit/test_validation_study.py`

**Interfaces:**
- Produces: typed shared builders for canonical prerequisite/result documents,
  offline primary materialization, candidate copying, and session repository
  fixture mechanics.
- Produces: the same test-function and parametrized-case inventory, mapped from
  one old owner path to five explicit behavioral owners.

- [x] **[STEP-18-47e658dc] Generate the pre-split semantic manifest.** Record test
  function names, parametrized node suffixes, markers, total cases, and the five
  intended path mappings from the current module.
- [x] **[STEP-19-bec68188] Write RED shared-support imports.** Redirect the first
  protocol builder tests to `tests.support.validation_study` and confirm the
  missing interface fails.
- [x] **[STEP-20-7c8fd85f] Extract only cross-owner builders and fixtures.** Move
  canonical document builders, repository-copy mechanics, and shared fixtures;
  keep assertion-specific helpers beside their owner tests and run the original
  monolithic module GREEN.
- [x] **[STEP-21-b73b9e6a] Split protocol and orchestration owners.** Move complete
  top-level test/decorator blocks without changing bodies, run both new modules,
  and compare their semantic manifest slice.
- [x] **[STEP-22-2f4ff7d6] Split audit, prerequisite, and audit-boundary owners.**
  Move complete top-level blocks and local helpers, install shared autouse
  fixtures in the directory `conftest.py`, and run each owner serially.
- [x] **[STEP-23-890d71c5] Remove the monolith and verify equivalence.** Require
  equal test names, parametrized case counts, markers, and total collection;
  run the whole validation-study directory with `-n 4 --dist worksteal`, Ruff,
  and strict Pyright.
- [x] **[STEP-24-2a5d982f] Commit modularization.** Inspect Git rename/move
  detection and commit with `test: modularize validation study suite`.

### [TASK-6-a71faf9b] Measure safe speed improvements

**Files:**
- Modify: `architecture/DEVELOPMENT.md` if and only if coverage equivalence is
  demonstrated.
- Modify: `architecture/TESTING.md` with the measured decision and invariant.

**Interfaces:**
- Consumes: serial and four-worker coverage JSON generated from the same commit.
- Produces: a documented serial-or-parallel coverage decision and duration
  evidence without lowering coverage scope.

- [x] **[STEP-25-b559c072] Run the post-refactor ordinary benchmark.** Execute all
  non-external tests with four workers, work stealing, and `--durations=50` in
  the bounded scope; compare wall time and slowest cases with the baseline.
- [x] **[STEP-26-6b12c80f] Run serial branch coverage.** Export coverage JSON and
  record collected cases, executed/missing lines, executed/missing branches,
  total branch coverage, and elapsed time.
- [x] **[STEP-27-6576c0e6] Run repeated four-worker branch coverage.** Execute the
  same selection at least twice with pytest-cov, export separate JSON files,
  and compare each file/line/branch set to serial evidence.
- [x] **[STEP-28-6ed2093c] Apply the measured decision.** Make parallel coverage
  normative only if every equivalence criterion passes; otherwise retain serial
  coverage and document job-level concurrency as the speed path.
- [x] **[STEP-29-6ef4642d] Run deterministic generator checks and commit.** Run
  every checked-in generator in check mode, inspect the architecture diff, and
  commit with `test: document measured coverage strategy`.

### [TASK-7-dfa7a261] Complete verification and review

**Files:**
- Modify: any touched file required to fix a Critical or Important independent
  review finding.
- Update: `docs/superpowers/plans/2026-08-19-testing-infrastructure.md`

**Interfaces:**
- Consumes: all refactored test infrastructure and the canonical gate table.
- Produces: clean local commits and final evidence satisfying the approved spec.

- [ ] **[STEP-30-e470728a] Run locked and static gates.** Run `uv sync --locked`,
  Ruff format check, Ruff lint, and strict Pyright; retain exact exit status.
- [ ] **[STEP-31-b79f7aad] Run the complete offline gates.** Execute the canonical
  bounded ordinary and coverage commands, require all tests to pass and branch
  coverage to remain at least 90%.
- [ ] **[STEP-32-b639b7dd] Run external and audit evidence.** Run the combined
  serial `docker or internet` gate when available, deterministic generator
  checks, and the regular-copy/no-hardlink validation audit.
- [ ] **[STEP-33-a049ab78] Obtain independent review.** Request a read-only review
  of the full diff and test evidence; fix every Critical and Important finding
  with focused RED-GREEN verification.
- [ ] **[STEP-34-713c0040] Close the local delivery.** Mark completed plan boxes,
  commit the verification record, confirm `git status --short` is empty, and
  report commits, timings, coverage, and any unavailable external capability.
