# [PLAN-1-a6385796] Fixture Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize every checked static fixture under root `fixtures/`, remove `phase` from tracked filenames, and enforce strict progress/task-identification rules without changing runtime behavior or fixture bytes.

**Architecture:** Copy and hash fixtures before changing consumers, centralize test path ownership, switch generators and documentation, then delete legacy paths under a repository-layout guard. Accepted study evidence and pytest fixture functions remain in their architecture-owned locations.

**Tech Stack:** Python 3.12, pathlib, hashlib, JSON, pytest, uv, Ruff, strict Pyright, Git.

**Spec:** `docs/superpowers/specs/2026-08-18-fixture-consolidation-design.md`

## Global Constraints

- Copy every fixture as an ordinary file before deleting its legacy source.
- Preserve exact bytes, modes needed for executable fixtures, and deterministic generator output.
- Root fixture paths live below `fixtures/{examples,tests}`; no compatibility duplicate remains after cutover.
- Do not move accepted evidence from `examples/validation_study/evidence/` or pytest fixture functions from test support.
- No tracked filename may contain `phase`, case-insensitively.
- Every task and step heading in newly generated task documents carries an immutable CRC32 label.
- Do not add dependencies, symlinks, hardlinks, or runtime fallback paths.

---

### [TASK-1-64505144] Governance and Repository-Layout Contract

**Files:**
- Modify: `AGENTS.md`
- Create: `scripts/check_fixture_layout.py`
- Create: `tests/unit/test_repository_layout.py`

**Interfaces:**
- Produces: `build_manifest(root: Path) -> tuple[ManifestEntry, ...]`
- Produces: `check_manifest(root: Path, manifest_path: Path) -> None`
- Produces: `tracked_phase_paths(repository: Path) -> tuple[Path, ...]`
- Produces: `legacy_fixture_paths(repository: Path) -> tuple[Path, ...]`

- [ ] **[STEP-1-9bdeb44b] Write governance and layout RED tests**

  Add tests that reject an altered fixture byte, an extra unmanifested file, a
  tracked `phase` basename, and each legacy fixture root. Assert deterministic
  UTF-8 manifest ordering and lowercase SHA-256 values.

- [ ] **[STEP-2-170cecbd] Run the focused RED tests**

  Run:

  ```bash
  uv run --locked pytest -q -n 0 tests/unit/test_repository_layout.py
  ```

  Expected: failures because the checker and root fixture manifest do not exist.

- [ ] **[STEP-3-33e38b73] Implement the minimal checker and strict AGENTS rules**

  Use frozen dataclasses for manifest entries, `Path.lstat()` to reject
  non-regular fixture entries, `hashlib.sha256()` for identities, and
  `git ls-files -z` with `shell=False` for tracked filename checks. Add the short
  output-percentage/ETA and generated-label rules from the design to `AGENTS.md`.

- [ ] **[STEP-4-b493dfe3] Run focused GREEN and static checks**

  ```bash
  uv run --locked pytest -q -n 0 tests/unit/test_repository_layout.py
  uv run --locked ruff format --check scripts/check_fixture_layout.py tests/unit/test_repository_layout.py
  uv run --locked ruff check scripts/check_fixture_layout.py tests/unit/test_repository_layout.py
  uv run --locked pyright scripts/check_fixture_layout.py tests/unit/test_repository_layout.py
  ```

- [ ] **[STEP-5-d82662cd] Commit the governance contract**

  ```bash
  git add AGENTS.md scripts/check_fixture_layout.py tests/unit/test_repository_layout.py
  git commit -m "chore: define fixture repository contract"
  ```

### [TASK-2-b78742a2] Copy and Verify the Root Fixture Taxonomy

**Files:**
- Create: `fixtures/README.md`
- Create: `fixtures/manifest.json`
- Copy: `tests/fixtures/` to `fixtures/tests/`
- Copy: `tests/docker/compose.endpoint.json` to `fixtures/tests/docker/compose.endpoint.json`
- Copy: `examples/data/` to `fixtures/examples/pipeline/`
- Modify: `tests/unit/test_repository_layout.py`

**Interfaces:**
- Consumes: `build_manifest()` and `check_manifest()` from Task 1.
- Produces: byte-identical root fixture trees and their authoritative manifest.

- [ ] **[STEP-1-1b32526a] Add copy-parity RED assertions**

  Assert every declared source maps to one destination regular file with equal
  mode, size, and SHA-256, and that the manifest contains each destination once.

- [ ] **[STEP-2-e45592f7] Copy ordinary files without deleting legacy sources**

  Use `cp -a` for the three declared trees/files, then rename destination
  subdirectories to the taxonomy in the design. Do not use links.

- [ ] **[STEP-3-a9d385a9] Generate and check the sorted fixture manifest**

  ```bash
  uv run --locked python scripts/check_fixture_layout.py --write-manifest
  uv run --locked python scripts/check_fixture_layout.py --check-manifest
  ```

- [ ] **[STEP-4-f4c45fc0] Prove byte and mode parity before deletion**

  Run the focused parity tests and existing fixture generator check modes against
  the copied destinations. Expected: all pass while legacy and new files coexist.

- [ ] **[STEP-5-1661bdfa] Commit the verified copies**

  ```bash
  git add fixtures tests/unit/test_repository_layout.py
  git commit -m "test: copy fixtures into root taxonomy"
  ```

### [TASK-3-7181c9fe] Switch Consumers and Rename Phase-Named Files

**Files:**
- Create: `tests/support/fixture_paths.py`
- Rename: `scripts/generate_phase2_fixtures.py` to `scripts/generate_similarity_fixtures.py`
- Rename: six `docs/superpowers/plans/*phase-*.md` files to outcome-specific basenames
- Modify: `scripts/generate_fit_fixtures.py`
- Modify: `scripts/generate_model_fixtures.py`
- Modify: `scripts/generate_similarity_fixtures.py`
- Modify: `scripts/generate_validation_study_fixture.py`
- Modify: fixture-consuming files under `tests/`, `examples/`, `docs/`, and `README.md`

**Interfaces:**
- Produces: `FIXTURES_ROOT`, `TEST_FIXTURES`, `EXAMPLE_FIXTURES`, and focused child constants in `tests/support/fixture_paths.py`.
- Consumes: the verified root fixture taxonomy from Task 2.

- [ ] **[STEP-1-a7cdc5be] Write path-cutover and renamed-command RED tests**

  Assert generator defaults target root fixtures, fixture-consuming tests resolve
  below `FIXTURES_ROOT`, renamed scripts support `--check`, and every tracked
  documentation link resolves.

- [ ] **[STEP-2-05787819] Run the cutover RED matrix**

  ```bash
  uv run --locked pytest -q -n 0 tests/unit/test_repository_layout.py tests/unit/models/test_fixture_generator.py tests/unit/test_readme.py
  ```

- [ ] **[STEP-3-4d8b8379] Switch code and tests to centralized fixture paths**

  Update imports and path literals without fallback aliases. Preserve accepted
  evidence paths. Rename the generator module/import and the six historical plan
  files; update all Markdown and command references.

- [ ] **[STEP-4-82956260] Run generator, documentation, and affected test GREEN**

  Run every generator `--check`, repository-layout tests, README tests, validation
  study unit/integration tests, Docker fixture-support tests, and example CLI tests.

- [ ] **[STEP-5-640cd1d0] Commit the consumer cutover**

  ```bash
  git add scripts tests examples docs README.md fixtures
  git commit -m "refactor: use root fixture taxonomy"
  ```

### [TASK-4-f4f9359a] Delete Legacy Fixture Paths and Enforce Compartmentalization

**Files:**
- Delete: `tests/fixtures/`
- Delete: `tests/docker/compose.endpoint.json`
- Delete: `examples/data/`
- Modify: `fixtures/manifest.json`
- Modify: `fixtures/README.md`
- Modify: `tests/unit/test_repository_layout.py`

**Interfaces:**
- Consumes: root-only consumers from Task 3.
- Produces: one authoritative fixture root with no duplicate compatibility copy.

- [ ] **[STEP-1-7ba9af4a] Enable real-repository legacy-path RED assertions**

  Assert the actual checkout has no legacy roots, no unmanifested fixture file,
  no non-regular fixture entry, and no tracked basename containing `phase`.

- [ ] **[STEP-2-95c54ee6] Delete only byte-verified legacy fixture paths**

  Remove each source only after its destination identity is present in the
  manifest and the parity test has passed. Retain no symlink or fallback copy.

- [ ] **[STEP-3-2f4571ea] Regenerate the root-only manifest and run layout GREEN**

  ```bash
  uv run --locked python scripts/check_fixture_layout.py --write-manifest
  uv run --locked python scripts/check_fixture_layout.py --check
  uv run --locked pytest -q -n 0 tests/unit/test_repository_layout.py
  ```

- [ ] **[STEP-4-8453fb19] Run every affected generator and fixture owner**

  Require all deterministic generator checks, validation-study clean-checkout
  reproduction, example pipelines, Docker fixture support, and process-guard
  behavior to pass from root fixture paths.

- [ ] **[STEP-5-cb0caf51] Commit legacy removal**

  ```bash
  git add -A fixtures tests examples scripts docs README.md
  git commit -m "refactor: remove legacy fixture locations"
  ```

### [TASK-5-f160f250] Full Verification and Review

**Files:**
- Verification first; fixes may touch only files implicated by fresh failures.

**Interfaces:**
- Consumes: the root-only fixture layout and repository checker.
- Produces: final gate evidence and a clean reviewed branch.

- [ ] **[STEP-1-46df0934] Run locked and static gates**

  ```bash
  uv sync --locked --all-groups
  uv lock --check
  uv run --locked ruff format --check .
  uv run --locked ruff check .
  uv run --locked pyright
  uv run --locked python scripts/check_fixture_layout.py --check
  ```

- [ ] **[STEP-2-b048f1a8] Run bounded parallel and serial coverage gates**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 10m --kill-after 10s -- uv run --locked pytest -q -n 4 -m "not docker and not internet"
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 10m --kill-after 10s -- uv run --locked pytest -q -n 0 -m "not docker and not internet" --cov=trafficlab --cov-branch --cov-report=term-missing --cov-fail-under=90
  ```

- [ ] **[STEP-3-28896c47] Run available bounded Docker fixture gates**

  Use one unique project scope and exact cleanup. Do not run public Internet
  collection; retain existing accepted evidence untouched.

- [ ] **[STEP-4-6cbdbddc] Perform independent review and fix findings**

  Review the complete branch for missed fixtures, stale paths, lossy copies,
  noncompliant generated task headings, and architecture regressions. Fix all
  Critical and Important findings and rerun affected gates.

- [ ] **[STEP-5-418a6149] Commit final fixes and require a clean tree**

  ```bash
  git status --short --branch
  ```

  Expected: all fixture migration commits retained locally, root-only fixtures,
  zero `phase` basenames, all gates green, and no uncommitted project work.
