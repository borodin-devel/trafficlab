# [PLAN-1-98a9884e] Review Findings Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct every Important and Minor finding from the 2026-08-18 whole-project review, verify the complete
TrafficLab gate, and move the local `MVP_2` tag to the resulting verified commit.

**Architecture:** Preserve the existing one-process prototype and artifact schemas. Extend the existing content-identity
boundary for immutable directory inputs, use the existing durability primitive for all exclusive artifact links, make
the run coordinator own its final completion-log error, and keep Docker fixture ownership session-local.

**Tech Stack:** CPython 3.12.3, Pydantic 2, pytest, Ruff, Pyright, uv, Docker Engine, Docker Compose, standard-library
hashing/JSON/filesystem APIs, and Git.

**Spec:** [System architecture](../../../architecture/SYSTEM.md),
[development policy](../../../architecture/DEVELOPMENT.md),
[testing strategy](../../../architecture/TESTING.md), and the accepted review findings in the conversation that
requested this remediation.

## Global Constraints

- Preserve the current scientific artifact schema and the strict nine-file run tree.
- Use no new runtime dependency, service, process boundary, security subsystem, or generalized framework.
- Follow RED, minimal GREEN, and focused refactor for every behavioral change.
- Run every pytest command through `scripts/run_bounded.sh` and every Python command through `uv run --locked`.
- Keep Docker image ownership unique per test session and remove every image built by that session with bounded calls.
- Do not rewrite accepted r21 evidence; it remains bound to its recorded source and evidence revisions.
- Commit each independently verified task and finish with a clean current branch before moving `MVP_2`.

---

### [TASK-1-553ca6aa] Bind immutable directory mounts and separate writable outputs

**Files:**

- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/DEVELOPMENT.md`
- Modify: `src/trafficlab/compatibility.py`
- Modify: `src/trafficlab/capture.py`
- Modify: `src/trafficlab/preflight.py`
- Test: `tests/unit/test_compatibility.py`
- Test: `tests/unit/test_capture.py`

**Interfaces:**

- Produce `identify_directory(path: Path) -> ContentIdentity`, a deterministic identity over relative directory and
  regular-file entries that rejects links, nonregular entries, and concurrent inventory changes.
- `_identify_mounted_inputs` hashes read-only regular files and directories and omits writable output mounts.

- [x] **[STEP-1-85ff5b7b] Write failing directory and mount-role tests**

  Add direct tests proving byte/path/inventory changes alter a read-only directory identity, symlinks and nonregular
  entries fail, read-only directory changes fail mounted-input comparison, and writable file/directory mounts are not
  treated as immutable inputs.

- [x] **[STEP-2-0c171569] Run the focused RED tests**

  Run `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 2m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_compatibility.py tests/unit/test_capture.py` and require failures caused by the absent directory identity and current mount-role behavior.

- [x] **[STEP-3-5be8e346] Implement deterministic directory identity and mount classification**

  Add the standard-library directory inventory codec in `compatibility.py`; use it only for read-only directory mounts,
  retain `identify_file` for read-only file mounts, and omit writable mounts from the immutable input tuple.

- [x] **[STEP-4-d600a728] Run focused GREEN and static checks**

  Rerun the focused pytest command, then `uv run --locked ruff check src/trafficlab/compatibility.py src/trafficlab/capture.py tests/unit/test_compatibility.py tests/unit/test_capture.py` and `uv run --locked pyright`.

- [x] **[STEP-5-d89a64cf] Commit mounted-input fidelity**

  Commit the four files with message `fix: bind immutable directory mounts`.

### [TASK-2-ae3deb5d] Classify final run-log publication correctly

**Files:**

- Modify: `src/trafficlab/run.py`
- Test: `tests/unit/test_run.py`
- Test fixture: `tests/fixtures/data/diagnostics/failure-outcomes.jsonl` only if the canonical public matrix requires it

**Interfaces:**

- The failed `run_completed` append produces `publication_failed`, stage `publication`, affected evidence `run.log`,
  evidence state `preserved`, with completed stages through `compare`.

- [x] **[STEP-6-9bbf96b2] Write the failing final-completion logging test**

  Inject a failure only for the `run_completed` append, allow `run_failed` to persist, and assert the exact canonical
  outcome plus the preserved comparison artifact and completed stage list.

- [x] **[STEP-7-326eb34c] Run the focused RED test**

  Run `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 2m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_run.py` and require the existing `metric_infeasible/compare/similarity.json` result to fail the new assertion.

- [x] **[STEP-8-ae553034] Implement the run-owned publication outcome**

  Wrap only the final completion append, attach the canonical publication outcome, and set the coordinator stage to
  `run` before that boundary without changing earlier stage arbitration.

- [x] **[STEP-9-a3a6d396] Run focused GREEN and static checks**

  Rerun `tests/unit/test_run.py`, then `uv run --locked ruff check src/trafficlab/run.py tests/unit/test_run.py` and
  `uv run --locked pyright`.

- [x] **[STEP-10-8d7b545b] Commit final-log failure semantics**

  Commit the implementation and regression test with message `fix: classify final run log failure`.

### [TASK-3-84cfda09] Complete exclusive-publication directory durability

**Files:**

- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/CAPTURE.md`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `src/trafficlab/comparison.py`
- Test: `tests/unit/test_artifacts.py`
- Test: `tests/unit/test_comparison.py`
- Test: `tests/integration/test_generate_cli.py`

**Interfaces:**

- Produce `fsync_published_artifact(path: Path, *, stage: str, affected_evidence: str) -> None`.
- Capture-pair, generated-PCAPNG, and similarity publishers fsync their containing directory after successful linking;
  a post-link failure preserves and reports the destination.

- [x] **[STEP-11-d89f2940] Write failing publication ordering and failure-state tests**

  Add tests for generated, capture-pair, and similarity publication that require directory fsync after linking and
  require `publication_failed` with `preserved` evidence when only the directory fsync fails.

- [x] **[STEP-12-1ad3b73f] Run publication RED tests**

  Run `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 3m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_artifacts.py tests/unit/test_comparison.py tests/integration/test_generate_cli.py` and require the missing post-link durability calls to fail.

- [x] **[STEP-13-8f58963d] Implement the shared post-publication durability boundary**

  Reuse the existing containing-directory fsync implementation, preserve established collision behavior, and retain
  already-linked destinations on durability failure.

- [x] **[STEP-14-45fe5620] Run publication GREEN and static checks**

  Rerun the three focused files, then `uv run --locked ruff check src/trafficlab/artifacts.py src/trafficlab/comparison.py tests/unit/test_artifacts.py tests/unit/test_comparison.py tests/integration/test_generate_cli.py` and `uv run --locked pyright`.

- [x] **[STEP-15-056ea904] Commit publication durability**

  Commit the implementation and tests with message `fix: fsync exclusive publications`.

### [TASK-4-029f0607] Own every Docker fixture image per session

**Files:**

- Modify: `tests/conftest.py`
- Test: `tests/unit/test_docker_fixture_support.py`

**Interfaces:**

- Every locally built capture, client, endpoint, and no-shell image receives one shared session UUID suffix.
- Teardown makes a bounded removal attempt for every successfully built image while never removing a borrowed capture
  image supplied through `--capture-image`.

- [ ] **[STEP-16-ea7161ef] Write failing unique-tag and complete-cleanup tests**

  Require all locally built image references to be unique, require reverse-order removal of all owned images, require
  partial-build cleanup, and require borrowed capture preservation alongside cleanup of locally built helper images.

- [ ] **[STEP-17-be8e3883] Run Docker fixture RED tests**

  Run `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 2m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_docker_fixture_support.py` and require fixed helper tags and incomplete teardown to fail.

- [ ] **[STEP-18-a4dfdf08] Implement session-local image ownership and teardown**

  Derive all local tags from one UUID, record each successful build, remove all recorded images in reverse order with
  the existing 20-second command bound, and preserve the first cleanup failure while noting later failures.

- [ ] **[STEP-19-9af8e037] Run Docker fixture GREEN and static checks**

  Rerun the focused test, then `uv run --locked ruff check tests/conftest.py tests/unit/test_docker_fixture_support.py`
  and `uv run --locked pyright`.

- [ ] **[STEP-20-86a1abb3] Commit Docker fixture ownership**

  Commit both files with message `test: isolate Docker fixture images`.

### [TASK-5-ae28aa9e] Correct the user-facing MMPP initialization description

**Files:**

- Modify: `README.md`
- Test: `tests/unit/test_readme.py`

**Interfaces:** The root README names rate-weighted arrival-epoch initialization and no longer claims arbitrary-time
stationary initialization.

- [ ] **[STEP-21-60512926] Confirm the authoritative MMPP wording**

  Compare the root overview with `architecture/traffic_models/mmpp.md` and `src/trafficlab/models/mmpp.py` before editing.

- [ ] **[STEP-22-0358047a] Update only the incorrect README sentence**

  Replace “initializes from its stationary distribution” with concise arrival-epoch wording and make no unrelated
  documentation changes.

- [ ] **[STEP-23-92c120dd] Run documentation checks**

  Run `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 1m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_readme.py`, `uv run --locked ruff format --check README.md`, and `git diff --check`.

- [ ] **[STEP-24-501d1885] Review the corrected scientific description**

  Re-read the README paragraph beside the MMPP architecture equation and require that it distinguishes arbitrary-time
  stationarity from arrival-epoch conditioning.

- [ ] **[STEP-25-1fe62d13] Commit the documentation correction**

  Commit `README.md` with message `docs: correct MMPP initialization`.

### [TASK-6-7221d7fc] Verify, integrate, and retag MVP_2

**Files:** Verification first; modify only files implicated by fresh failures.

**Interfaces:** `main` contains the reviewed commits, `MVP_2` points at the verified final commit, no remote is changed,
and the working tree plus Docker project resources are clean.

- [ ] **[STEP-26-f0f9438a] Run deterministic and static gates**

  Run locked sync/lock, fixture layout and all four deterministic fixture generators, Ruff format/lint, strict Pyright,
  and `git diff --check`.

- [ ] **[STEP-27-76293924] Run bounded non-external tests and coverage**

  Run the documented four-worker non-external suite and the serial branch-aware coverage suite with a 90% floor.

- [ ] **[STEP-28-819b0c67] Run bounded Docker and Internet verification**

  On the available host, run the serial 20-test `docker or internet` matrix with the documented credential-free HTTPS
  endpoint; inspect that no TrafficLab-labelled container, network, volume, or owned image remains.

- [ ] **[STEP-29-ddd7d782] Integrate the verified branch and move the local tag**

  Fast-forward `main` to the verified remediation branch, verify the old `MVP_2` target before replacing the lightweight
  local tag, and do not push the branch or tag.

- [ ] **[STEP-30-7b04d064] Require final clean state**

  Confirm `git status --short --branch`, `git show-ref --tags MVP_2`, the final commit log, and absence of project-scoped
  Docker residue before reporting completion.
