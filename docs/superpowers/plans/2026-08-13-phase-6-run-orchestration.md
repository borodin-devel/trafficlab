# Phase 6 Run Orchestration and Complete Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Add one retry-safe `trafficlab run` command that composes the existing stages in process, preserves
validated work across failures, and proves the complete offline and Docker-backed pipeline boundaries.

**Architecture:** Keep every stage as the sole owner of its scientific artifacts and failure diagnostics. Add one
small explicit coordinator that calls full preflight once, passes that prepared run into the capture stage, then
calls the existing fit, generate, and compare functions; do not add a workflow framework, manifest, database,
subprocess protocol, or generic rollback layer. Reuse is decided only by the owning strict artifact parser and
lineage checks, while later failure never removes an earlier completed stage.

**Tech Stack:** CPython 3.12.3 development runtime, Python standard library, existing Pydantic configuration,
Docker Compose boundary, pytest/pytest-xdist/pytest-cov, Ruff, strict Pyright, and the uv locked environment.

## Global Constraints

- Implement Phase 6 only. Do not add model families, metrics, security, workers, databases, generic workflow
  abstractions, Node.js application dependencies, or a second process protocol.
- `trafficlab run EXPERIMENT` executes full preflight, capture, fit, generate, and compare through the same
  in-process stage functions used by the individual commands.
- Full Docker preflight runs exactly once in one `run` attempt. The prepared result is passed into the capture
  core; the individual `capture` command remains a wrapper that performs its own full preflight first.
- Each stage validates before reuse. A valid complete capture pair may be reused without starting the workload;
  an absent or invalid/incomplete capture pair follows the existing race-safe recovery and recapture path.
- Missing derived fit, generation, and comparison outputs may be recreated. Malformed, incompatible, or
  byte-different checkpoint, best-model, generated, and similarity artifacts are preserved and rejected unless
  their owning Phase 5 policy already defines exact repair, replacement, or byte-identical reuse.
- `genetic.resume` remains the only resume switch. Do not add a run-level resume option or new configuration.
- Selection trials use only `genetic.trial_seeds` and `generation.trial`; final validation uses exactly
  `run.final_seed` with `generation.trial`; final artifact generation uses exactly `run.final_seed` with
  `generation.final` over the full stored observation window.
- A later failure never rolls back an earlier valid stage. Capture and preflight retain ownership of their own
  bounded Docker cleanup; the coordinator does not issue a second generic cleanup command after resources have
  already been closed.
- Successful runs contain exactly the documented ordinary files: `experiment.toml`, `reference.pcapng`,
  `capture.json`, `checkpoint.json`, `ga_history.csv`, `best_model.json`, `generated.pcapng`, `similarity.json`,
  and `run.log`. Do not add a manifest, status file, PID file, detached summary, or lineage graph.
- A failed capture may retain `diagnostic-capture.json` and `diagnostic-reference.pcapng`. Before a later capture
  success is returned, the capture stage identity-safely quarantines and removes its stale diagnostic pair; a
  concurrent replacement is preserved and rejected. Successful runs contain no diagnostic, temporary, or
  quarantine entry.
- `run.log` remains canonical newline-delimited JSON. A successful coordinator appends one deterministic
  `run_completed` record. After preflight has returned a prepared directory, a later coordinator failure appends
  one contextual `run_failed` record without replacing the original `TrafficlabError` or its exit code. A
  preflight failure is reported by preflight itself because no reusable run directory is guaranteed to exist.
- The CLI prints one readable deterministic success line containing the winning family, fitness, reference packet
  count, generated packet count, aggregate score, and run directory. It prints structured stage/action errors with
  no traceback.
- Ordinary and offline tests never need Docker or Internet. Docker tests use unique project labels, direct Compose
  commands without sudo, serial execution, and mandatory complete resource cleanup. Internet remains opt-in.
- Use TDD for every behavior: write the behavioral test, run a guarded RED that fails for the intended reason,
  implement the minimum, run guarded GREEN, then refactor.
- Use `apply_patch` for authored edits. Public interfaces are typed, lines are at most 120 characters, and no new
  dependency is expected.
- Every pytest command uses `scripts/run_bounded.sh` with all five named limit flags before
  `-- uv run --locked pytest`; never run raw pytest, `-n auto`, or overlapping test processes.
- Focused tests use `2G/3G/512M`, five-minute wall time, ten-second kill grace, and `-n 0`. Docker tests use the
  same memory limits and twenty minutes. Fast and coverage gates use the exact four-worker commands in
  `architecture/DEVELOPMENT.md`.
- After any timeout, interruption, OOM, or incomplete tool response, inspect the scope, pytest descendants,
  memory, and swap before another test command.
- If a failed unit test identifies a defective function, direct behavioral tests cover 100% of that function's
  executable lines and branches.
- Each task writes its ignored SDD report, commits one coherent verified increment, receives independent task
  review, and fixes every Critical or Important finding before the next task.

## File Map

- `architecture/SYSTEM.md`: Lock exact run reuse, prepared-capture, run-log, summary, and preservation behavior.
- `architecture/TESTING.md`: Name the Phase 6 corruption, stage-failure, offline, and complete Docker evidence.
- `src/trafficlab/artifacts.py`: Expose race-safe capture-pair reuse/recovery needed before workload launch.
- `src/trafficlab/capture.py`: Split the prepared capture core from the individual full-preflight wrapper and add
  a strict reuse flag.
- `src/trafficlab/comparison.py`: Validate and byte-identically reuse `similarity.json` while preserving bad or
  different entries.
- `src/trafficlab/run.py`: Frozen run result, explicit dependencies, stage order, contextual logging, and
  preservation-only orchestration.
- `src/trafficlab/cli.py`: Lazy `run` route and one-line result/error summaries.
- `tests/unit/test_run.py`: Coordinator contract, invariants, ordering, logging, and failure matrix.
- `tests/unit/test_cli.py`: Injected `run` route, exact summary, errors, options, and lazy import boundary.
- `tests/unit/test_comparison.py`: Strict comparison-result reuse/corruption/race tests.
- `tests/unit/test_capture.py`: Prepared-core and pre-workload capture reuse tests.
- `tests/integration/test_run_pipeline.py`: Offline stage-by-stage pipeline and complete in-process run behavior.
- `tests/docker/test_run_docker.py`: Deterministic controlled complete-run and every-stage cleanup/preservation.
- `tests/docker/support.py`: Small-budget complete-run experiment helper and exact artifact/resource assertions.
- `architecture/ROADMAP.md`: Phase 6 boxes marked only after their exact evidence exists.

## Locked Interfaces

```python
@dataclass(frozen=True, slots=True)
class CaptureResult:
    run_directory: Path
    reference_path: Path
    packet_count: int
    target_status: int
    reused: bool = False


def capture_prepared_experiment(
    path: Path,
    prepared: PreparedExperiment,
    *,
    docker: CaptureDocker | None = None,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], bool] = lambda: False,
) -> CaptureResult: ...


def capture_experiment(
    path: Path,
    *,
    docker: CaptureDocker | None = None,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], bool] = lambda: False,
) -> CaptureResult:
    prepared = run_preflight(path, config_only=False, docker=docker, clock=clock)
    return capture_prepared_experiment(
        path,
        prepared,
        docker=docker,
        clock=clock,
        interruption=interruption,
    )


def load_or_recover_capture_pair(
    run_directory: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
) -> CaptureInspection | None: ...
```

`capture_prepared_experiment` validates that `prepared.source`, its effective configuration, and its authoritative
snapshot match `path`. Before creating temporary files or a Docker project, it calls the owning artifact helper.
The helper returns a strict `CaptureInspection` for a valid pair, returns `None` after safely quarantining an
invalid/incomplete pair, and raises on deadline, identity race, or recovery failure. For valid reuse it snapshots
the pair identity before validation and requires the same identity after validation; a replacement during
validation is preserved and rejected without launching a workload. A valid stable pair yields
`CaptureResult(..., target_status=0, reused=True)` and a `capture_reused` log record.

```python
@dataclass(frozen=True, slots=True)
class RunResult:
    experiment_path: Path
    run_directory: Path
    capture: CaptureResult
    fit: FitStageResult
    generation: GenerationStageResult
    comparison: ComparisonResult


@dataclass(frozen=True, slots=True)
class RunDependencies:
    preflight: Callable[[Path], PreparedExperiment]
    capture: Callable[[Path, PreparedExperiment], CaptureResult]
    fit: Callable[[Path], FitStageResult]
    generate: Callable[[Path], GenerationStageResult]
    compare: Callable[[Path], ComparisonResult]

    @classmethod
    def production(cls) -> Self: ...


def run_experiment(
    experiment_path: Path,
    *,
    dependencies: RunDependencies | None = None,
) -> RunResult: ...
```

Production dependencies use one `_full_preflight(path)` adapter around
`run_preflight(path, config_only=False)` and a `_capture_prepared(path, prepared)` adapter around
`capture_prepared_experiment`. The coordinator stores a literal current stage name, calls all five functions in
order, and validates each returned stage result immediately before calling the next stage. It requires exact result
types, the prepared run directory, documented artifact paths, target status zero, common observation windows, and
the configured final seed. Every mismatch becomes a contextual `TrafficlabError` for that stage, is logged as
`run_failed`, and prevents the next call. Only after all immediate checks pass does it append:

```json
{"aggregate_score":0.75,"event":"run_completed","family":"mmpp","fitness":0.8,
 "generated_packet_count":12,"reference_packet_count":10,"run_directory":"/abs/run","stage":"run"}
```

JSON rendering remains sorted and compact through `append_run_log`; the illustration is split only for prose.
After preflight succeeds, a later `TrafficlabError` makes the coordinator append `run_failed` with `failed_stage`,
`detail`, and `corrective_action`, then re-raise the same primary exit code. If failure logging also fails, the
raised error retains the primary detail/action/code and adds the logging failure as secondary text. A preflight
error propagates directly without coordinator logging because preparation may have failed before creating a run.

`_publish_comparison_result(destination, expected) -> bool` returns `True` when this call created the artifact and
`False` when a pre-existing or racing winner was strictly loaded, canonically rendered, and byte-identical to the
new expected result. Invalid or different bytes are preserved and raise `TrafficlabError`. `compare_experiment`
records `comparison_succeeded` with an exact boolean `reused` field.

---

### Task 1: Lock the run, reuse, summary, and failure contract

**Files:**

- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**

- Consumes: the approved Phase 6 Roadmap and current stage/artifact policies.
- Produces: the exact behavior in Global Constraints and Locked Interfaces for all later tasks.

- [ ] **Step 1: Add the exact run-stage rules to `SYSTEM.md`**

State that full preflight runs once, its `PreparedExperiment` enters the capture core, capture may validate/reuse
before Docker launch, later stages call their ordinary public functions, and the coordinator owns no Docker
cleanup of its own. Add the missing/corrupt/different matrix, immediate result validation between calls, and forbid
a new resume switch or manifest. Replace the stale phrase `explicit --resume` with the actual
`genetic.resume = true` configuration contract.

- [ ] **Step 2: Lock exact success, failure, and summary records**

Document `run_completed`, `run_failed`, the single CLI summary, strict comparison reuse, exact success directory,
and preservation of all earlier complete outputs after later failure. Failed runs may retain the two diagnostic
capture files; a later successful capture removes only the stable stale diagnostic identities, so successful runs
have exactly the nine documented names and no temporary or quarantine residue.

- [ ] **Step 3: Add named Phase 6 evidence to `TESTING.md`**

Require unit tests for the five-stage call order, result invariants, missing/corrupt output matrix, every-stage
failure preservation, and no double preflight. Require the existing checked capture offline
`fit -> generate -> compare` path without `run`, plus serial deterministic Docker full-run evidence.

- [ ] **Step 4: Review and commit the owning-document resolution**

```bash
git diff --check
git add architecture/SYSTEM.md architecture/TESTING.md
git commit -m "docs: define run orchestration policy"
```

Require an independent task review against Phase 6 before Task 2. Fix ambiguous reuse ownership or cleanup text
in these owning documents rather than encoding ambiguity in production.

### Task 2: Make comparison publication retry-safe and strict

**Files:**

- Modify: `src/trafficlab/comparison.py`
- Modify: `tests/unit/test_comparison.py`
- Modify: `tests/unit/test_similarity_artifact.py`
- Modify: `tests/integration/test_comparison_pipeline.py`

**Interfaces:**

- Consumes: strict `ComparisonResult`, `render_comparison_result`, input SHA-256 lineage, and exclusive sibling
  publication.
- Produces: `_publish_comparison_result(destination: Path, result: ComparisonResult) -> bool` and exact logged
  reuse evidence.

- [ ] **Step 1: Write publication reuse RED tests**

Cover absent destination creation, strict byte-identical existing reuse, malformed JSON preservation, valid but
different lineage/score preservation, canonical-result/noncanonical-byte rejection, link-race identical reuse,
link-race different preservation, one-read existing bytes, fsync/link failure cleanup, and post-link owned-temp
cleanup reporting without deleting the published result.

```python
created = comparison._publish_comparison_result(path, expected)  # pyright: ignore[reportPrivateUsage]
assert created is False
assert path.read_bytes() == render_comparison_result(expected)
```

- [ ] **Step 2: Run the guarded RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_comparison.py \
  tests/unit/test_similarity_artifact.py -k "reuse or race or preserve"
```

Expected: failure because publication rejects every existing destination and returns no ownership state.

- [ ] **Step 3: Implement strict expected-result reuse**

Read an existing entry once, parse those bytes, canonical-render it, require canonical bytes and exact equality to
the expected canonical bytes, then return `False`. On a hard-link race, validate the winner identically. Catch and
translate only expected JSON/filesystem/publication errors, always clean only the caller-owned temporary file,
and let unexpected exceptions propagate after that cleanup.

- [ ] **Step 4: Thread reuse into comparison logging**

Set `created_by_call = _publish_comparison_result(...)` and append `comparison_succeeded` with
`"reused": not created_by_call`. Preserve the existing aggregate, W, path, stage, input hash, and CLI behavior.

- [ ] **Step 5: Run GREEN, coverage, and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_comparison.py \
  tests/unit/test_similarity_artifact.py tests/integration/test_comparison_pipeline.py
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 --cov=trafficlab.comparison --cov-branch \
  --cov-report=term-missing --cov-fail-under=0 tests/unit/test_comparison.py \
  tests/unit/test_similarity_artifact.py
uv run --locked ruff format src/trafficlab/comparison.py tests/unit/test_comparison.py \
  tests/unit/test_similarity_artifact.py tests/integration/test_comparison_pipeline.py
uv run --locked ruff check src/trafficlab/comparison.py tests/unit/test_comparison.py \
  tests/unit/test_similarity_artifact.py tests/integration/test_comparison_pipeline.py
uv run --locked pyright src/trafficlab/comparison.py tests/unit/test_comparison.py \
  tests/unit/test_similarity_artifact.py tests/integration/test_comparison_pipeline.py
```

All exposed publication/reuse defect branches must be directly covered.

- [ ] **Step 6: Review and commit**

```bash
git diff --check
git add src/trafficlab/comparison.py tests/unit/test_comparison.py \
  tests/unit/test_similarity_artifact.py tests/integration/test_comparison_pipeline.py
git commit -m "fix: reuse validated comparison results"
```

Obtain independent task review before Task 3.

### Task 3: Reuse capture before workload launch and add the explicit coordinator

**Files:**

- Modify: `src/trafficlab/artifacts.py`
- Modify: `src/trafficlab/capture.py`
- Create: `src/trafficlab/run.py`
- Modify: `tests/unit/test_artifacts.py`
- Modify: `tests/unit/test_capture.py`
- Create: `tests/unit/test_run.py`
- Modify: `tests/integration/test_capture_pipeline.py`

**Interfaces:**

- Consumes: `PreparedExperiment`, `CaptureInspection`, the existing capture lifecycle, every existing stage result,
  `append_run_log`, and Locked Interfaces.
- Produces: prepared capture execution, pre-Docker capture reuse, `RunResult`, `RunDependencies.production()`, and
  `run_experiment`.

- [ ] **Step 1: Write strict capture/result RED tests**

Add tests that `CaptureResult` rejects a non-boolean reuse flag, invalid paths, nonpositive packet count, and
non-integer status. Add artifact tests for valid pair reuse, absent pair, invalid pair recovery, identity change
during recovery, replacement of a valid pair during validation, deadline propagation, and raw stat/read errors.
The replacement case requires before/after identity equality, preserves both replacement bytes, raises, and makes
no workload call. Prove stable valid reuse performs no Docker call, temporary-directory creation, clock/deadline
calculation, or workload launch.

```python
result = capture_prepared_experiment(path, prepared, docker=cast(CaptureDocker, NoDocker()))
assert result.reused is True
assert result.target_status == 0
assert result.packet_count == inspection.packet_count
```

- [ ] **Step 2: Run capture RED through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_artifacts.py tests/unit/test_capture.py \
  -k "prepared or reuse_before or capture_result"
```

Expected: collection or assertion failure because the prepared capture core and reuse field do not exist.

- [ ] **Step 3: Extract the prepared capture core without changing lifecycle behavior**

Move the current post-preflight body into `capture_prepared_experiment`. Validate the prepared source, effective
configuration, run directory, snapshot bytes, and initial log before reuse. The wrapper performs full preflight
once and delegates. Before temporary-directory/project creation, call a public race-safe artifact helper that
wraps the existing `_existing_capture` policy. Log `capture_reused` or keep the existing `capture_published`
success event with exact `reused` evidence; do not weaken any interruption, deadline, rollback, or cleanup path.

- [ ] **Step 4: Write coordinator RED tests**

Use five recording callables to require exact order and identity of the one experiment path and prepared value.
Cover each stage failing with exit codes `11..15`, no later call, no deletion or mutation of earlier sentinel
artifacts, primary plus run-log failure, strict result type/path/W/seed/status invariants, and one exact successful
`run_completed` record. Preflight failure has no coordinator log; capture through compare failures have exact
`run_failed.failed_stage` values because the prepared directory then exists. Parameterize an invalid return from
capture, fit, generate, and compare, require translation to `TrafficlabError`, and prove validation occurs before
the next dependency call.

```python
result = run_experiment(Path("experiment.toml"), dependencies=dependencies)
assert calls == ["preflight", "capture", "fit", "generate", "compare"]
assert result.run_directory == prepared.run_directory
```

- [ ] **Step 5: Run coordinator RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_run.py
```

Expected: collection failure because `trafficlab.run` does not exist.

- [ ] **Step 6: Implement the minimal explicit coordinator**

Implement only `RunResult`, `RunDependencies`, `_full_preflight`, `_capture_prepared`, result validation, contextual
logging, and `run_experiment`. Do not introduce a stage base class, registry, loop of arbitrary callables, rollback
stack, retry framework, or new artifact. Keep five direct calls readable in source.

- [ ] **Step 7: Prove the real production dependency wiring**

Monkeypatch the exact functions imported by `trafficlab.run`, then call `run_experiment` with no injected
dependencies. Require one `run_preflight(path, config_only=False)`, zero `capture_experiment` calls, and one
`capture_prepared_experiment(path, same_prepared)` call before real-shape fit/generate/compare results. This test
must fail if `RunDependencies.production()` accidentally wires the wrapper and doubles full preflight.

- [ ] **Step 8: Prove successful retry directory cleanup**

Start with stable stale diagnostic capture files, execute a successful prepared capture, and require their
identity-safe removal. Replace either diagnostic at the quarantine boundary and require the replacement to survive
with the success rejected. On ordinary capture-only success assert directory name set equality with
`{"experiment.toml", "run.log", "capture.json", "reference.pcapng"}` and assert no entry contains `.tmp`,
`quarantine`, or `.trafficlab-capture-`. The nine-file complete-pipeline equality belongs to Task 5.

- [ ] **Step 9: Run focused GREEN, branch coverage, and regression checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_artifacts.py tests/unit/test_capture.py \
  tests/unit/test_run.py tests/integration/test_capture_pipeline.py
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 --cov=trafficlab.run --cov=trafficlab.capture --cov-branch \
  --cov-report=term-missing --cov-fail-under=0 tests/unit/test_capture.py tests/unit/test_run.py
uv run --locked ruff format src/trafficlab/artifacts.py src/trafficlab/capture.py src/trafficlab/run.py \
  tests/unit/test_artifacts.py tests/unit/test_capture.py tests/unit/test_run.py
uv run --locked ruff check src/trafficlab/artifacts.py src/trafficlab/capture.py src/trafficlab/run.py \
  tests/unit/test_artifacts.py tests/unit/test_capture.py tests/unit/test_run.py
uv run --locked pyright src/trafficlab/artifacts.py src/trafficlab/capture.py src/trafficlab/run.py \
  tests/unit/test_artifacts.py tests/unit/test_capture.py tests/unit/test_run.py
```

The new coordinator and every capture function changed after a unit RED require 100% line/branch coverage.

- [ ] **Step 10: Review and commit**

```bash
git diff --check
git add src/trafficlab/artifacts.py src/trafficlab/capture.py src/trafficlab/run.py \
  tests/unit/test_artifacts.py tests/unit/test_capture.py tests/unit/test_run.py \
  tests/integration/test_capture_pipeline.py
git commit -m "feat: orchestrate complete experiments"
```

Independent review must inspect no-double-preflight behavior, capture race safety, primary error retention, and
absence of generic rollback before Task 4.

### Task 4: Expose `trafficlab run` and prove retry/failure behavior in process

**Files:**

- Modify: `src/trafficlab/cli.py`
- Modify: `tests/unit/test_cli.py`
- Create: `tests/integration/test_run_pipeline.py`
- Modify: `tests/integration/test_preflight_cli.py`
- Modify: `tests/integration/test_compare_cli.py`
- Modify only after a diagnosed matrix RED: `src/trafficlab/artifacts.py`, `src/trafficlab/fitting.py`,
  `src/trafficlab/generation.py`, or `src/trafficlab/comparison.py`, plus that function's direct unit tests.
- Modify only after a diagnosed cold-default CLI RED: `src/trafficlab/preflight.py`. The permitted correction is
  limited to making local/config-only imports structurally Docker-adapter-free while retaining the exact full
  preflight protocol and concrete Docker behavior. Modify its direct owner test
  `tests/unit/test_docker_preflight.py` only when the lazy concrete cleanup boundary requires the test to patch the
  source module rather than the removed eager preflight binding.

Class 3 resolution (2026-08-13): a fresh interpreter running the default config-only CLI completed local preflight
but loaded `trafficlab.docker_cli` through `preflight.py`'s top-level cleanup/type imports. Task 4 therefore owns the
narrow import-boundary correction above; it does not move local science/config logic, add a module, or alter Docker
behavior. Focused full-preflight verification diagnosed the direct test patch-target adjustment described above.

**Interfaces:**

- Consumes: `run_experiment`, `RunResult`, existing stage functions, checked fit/capture fixtures, and strict
  artifact loaders.
- Produces: lazy CLI routing, exact summary, installed entrypoint behavior, and the complete non-Docker reuse and
  corruption matrix.

- [ ] **Step 1: Write CLI RED tests**

Register `run EXPERIMENT`, reject `--config-only` and unknown flags, dispatch one injected callback, preserve exact
`TrafficlabError.exit_code`, format the Locked Interfaces summary, and prove preflight/compare remain free of eager
`trafficlab.run`, capture, and Docker-adapter imports.

```python
assert main(["run", "experiment.toml"], run=run_once) == 0
assert captured.out == (
    "run: family=mmpp fitness=0.800000 reference_packets=10 generated_packets=12 "
    "aggregate_score=0.750000 output=/abs/run\n"
)
```

- [ ] **Step 2: Run CLI RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_cli.py -k "run"
```

Expected: parser rejection because the route is absent.

- [ ] **Step 3: Add the lazy CLI route**

Add `RunExperiment = Callable[[Path], RunResult]`, an optional `run` injected argument, and import
`trafficlab.run.run_experiment` only in the selected branch. Catch `KeyboardInterrupt` as status 130 after the
capture lifecycle owns cleanup; format other package errors exactly like existing commands.

- [ ] **Step 4: Write installed-entrypoint RED and parity tests**

Invoke `.venv/bin/trafficlab run INVALID_EXPERIMENT` in a guarded subprocess using a configuration that fails
local preflight before Docker. Compare its exact exit status, empty stdout, stage-prefixed stderr, corrective
action, and no traceback with direct `run_experiment(INVALID_EXPERIMENT)`. This proves the installed parser and
production dispatch route; injected `main()` tests alone are insufficient.

- [ ] **Step 5: Write the in-process stage and corruption matrix RED tests**

Build a prepared temporary run from checked `capture.json`/`reference.pcapng`. Exercise the ordinary real
`fit_experiment -> generate_experiment -> compare_experiment` functions without `run` and assert identical W,
input hashes, final seed, and final limits. Separately invoke `run_experiment` with an injected capture boundary and
real offline later stages. For every stage output, cover:

```text
experiment.toml/run.log: corrupt -> reject prepared run without mutation
capture.json/reference.pcapng: absent or invalid/incomplete -> recapture path
checkpoint.json: absent with resume=true -> fresh fit; malformed/incompatible -> preserve and reject
ga_history.csv: absent/corrupt -> derive and atomically repair from checkpoint
best_model.json: absent -> rebuild from terminal checkpoint; malformed/different -> preserve and reject
generated.pcapng: absent -> generate; malformed/different -> preserve and reject; identical -> reuse
similarity.json: absent -> compare; malformed/different -> preserve and reject; identical -> reuse
```

For preflight, capture, fit, generate, and compare failures, assert the exact last call, prior artifacts byte-equal,
and no downstream artifact. Preflight retains its own direct diagnostics; every post-preflight failure has one
contextual `run_failed` record.

- [ ] **Step 6: Run integration RED and implement only diagnosed corrections**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/integration/test_run_pipeline.py
```

Expected: failures at the newly specified installed route, run summary, or owning artifact policy—not test setup.
Fix only verified gaps in the owning stage; do not add coordinator-side filename deletion or result fabrication.
If the RED identifies a defect in an owning file listed in this task's conditional file map, add its direct unit
regression, reach 100% failed-function line/branch coverage, and commit that correction separately before the CLI
and integration commit. A defect outside the conditional map requires updating this plan before editing it.

- [ ] **Step 7: Add direct trial/final observability assertions**

Rerun and retain the recording-family evidence from
`test_small_nondefault_three_family_population_keeps_each_family_and_common_evaluation_inputs` to prove every
selection and final-validation generation uses `generation.trial`, with only trial seeds for selection and only
`run.final_seed` for final validation. Rerun
`test_stage_uses_only_stored_family_window_and_configured_final_seed_and_limits` to prove final artifact generation
passes exactly `generation.final`. Do not infer limits from result fields that do not store them.

- [ ] **Step 8: Run GREEN, static checks, and installed command proof**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_cli.py tests/integration/test_run_pipeline.py \
  tests/integration/test_genetic_fitting.py tests/integration/test_generate_cli.py \
  tests/integration/test_compare_cli.py
uv run --locked ruff format src/trafficlab/cli.py tests/unit/test_cli.py \
  tests/integration/test_run_pipeline.py tests/integration/test_preflight_cli.py \
  tests/integration/test_compare_cli.py
uv run --locked ruff check src/trafficlab/cli.py tests/unit/test_cli.py \
  tests/integration/test_run_pipeline.py tests/integration/test_preflight_cli.py \
  tests/integration/test_compare_cli.py
uv run --locked pyright src/trafficlab/cli.py tests/unit/test_cli.py \
  tests/integration/test_run_pipeline.py tests/integration/test_preflight_cli.py \
  tests/integration/test_compare_cli.py
```

- [ ] **Step 9: Review and commit**

```bash
git diff --check
git add src/trafficlab/cli.py tests/unit/test_cli.py tests/integration/test_run_pipeline.py \
  tests/integration/test_preflight_cli.py tests/integration/test_compare_cli.py
git commit -m "feat: expose complete run command"
```

The independent review must verify installed CLI parity, exact corruption outcomes, no Docker on offline paths,
and no weakened import guards.

### Task 5: Prove the controlled Docker complete run and cleanup matrix

**Files:**

- Modify: `tests/docker/support.py`
- Create: `tests/docker/test_run_docker.py`
- Modify only if a real failure proves a defect: the owning `src/trafficlab` module and its direct unit tests.

**Interfaces:**

- Consumes: `RunDependencies`, `run_experiment`, `EndpointDockerCompose`, checked endpoint/client images, unique
  project tracker, strict artifact loaders, and the small Phase 5 genetic settings.
- Produces: real controlled capture plus fit/generate/compare evidence and all-stage resource cleanup evidence.

- [ ] **Step 1: Write the Docker full-run test without executing it yet**

Create a small experiment with all three families, population six, generation count zero, one trial seed,
distinct final seed, bounded trial/final generation limits, and the controlled TCP/UDP endpoint. Inject one shared
`EndpointDockerCompose` into full preflight and prepared capture dependencies, while using real fit/generate/compare
functions. Invoke the public CLI branch with the resulting real `run_experiment` callback.

Assert the directory's ordinary filename set equals the documented nine names, with no diagnostic, temporary, or
quarantine entry, and load every artifact strictly; capture is bidirectional and nonempty; checkpoint
contains all families; winner final trials use only the final seed; generated result uses final limits; every W is
equal; comparison hashes exact files; summary and `run_completed` are exact; all labelled containers, networks,
volumes, and orphans are absent.

- [ ] **Step 2: Add every-stage Docker cleanup/preservation cases**

First use the real endpoint overlay with probe URL `http://endpoint:1/` to make the full-preflight network probe
fail deterministically; require its unique probe project to be removed and its direct network failure to remain
primary. Separately parameterize capture, fit, generate, and compare failures. The capture case uses the existing
readiness-timeout fixture with a `0.5` second readiness timeout and proves target never starts. Later cases use real
Docker preflight/capture up to the injected failure boundary. Assert cleanup is complete in every case, original
stage error remains primary, prior artifacts remain byte-identical, and no later stage runs.

- [ ] **Step 3: Collect the exact Docker nodes through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest --collect-only -q -n 0 tests/docker/test_run_docker.py
```

Expected: the complete-run node, real preflight-probe failure, and four later failure-boundary cases collect with
both `docker` and `integration` markers. Collection is a non-resource focused command, so its five-minute bound is
intentional; it is not Docker execution evidence.

- [ ] **Step 4: Run guarded Docker RED/GREEN only on a capable host**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 tests/docker/test_run_docker.py
```

If Docker is absent, record the actionable failing setup message and Class 4 environment limitation; do not mark
Docker Roadmap evidence and do not pretend collection or injection replaced it. If Docker is available, every case
must run and pass with teardown diagnostics empty.

- [ ] **Step 5: Run the opt-in Internet selection only when an explicit URL exists**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet --internet-url "$TRAFFICLAB_INTERNET_URL"
```

Do not invent a URL, credentials, or successful evidence. Without an explicit credential-free HTTPS URL and
Docker-capable host, record the external limitation and leave the Internet boxes unchecked.

- [ ] **Step 6: Review and commit the Docker test contract**

```bash
git diff --check
git add tests/docker/support.py tests/docker/test_run_docker.py
git commit -m "test: cover complete Docker experiments"
```

If TDD required a production fix, include its direct failed-function tests and owning files in a separate narrow
commit before this test commit. Obtain independent task review of resource ownership and truthful evidence.

### Task 6: Run Phase 6 gates, independent review, and truthful Roadmap closure

**Files:**

- Modify: `architecture/ROADMAP.md`
- Modify only for verified review fixes: owning Phase 6 source/test/document files.

**Interfaces:**

- Consumes: Tasks 1–5, strict fixture generators/loaders, the process guard, and every Phase 6 Roadmap item.
- Produces: exact verification evidence, Critical/Important-free final review, and only accurately checked boxes.

- [ ] **Step 1: Build the evidence ledger before changing Roadmap**

Record exact source, test nodes, artifact bytes/loaders, commits, and outputs for: five-stage in-process order; one
full preflight; valid reuse; corruption matrix; trial/final separation; nine-file directory; CLI/log summaries;
offline stage pipeline; Docker complete run; every-stage cleanup; and documented test commands.

- [ ] **Step 2: Run locked sync, lock, fixture, and process containment checks**

```bash
uv sync --locked --all-groups
uv lock --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_fit_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_model_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

- [ ] **Step 3: Run focused Phase 6 and static gates**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_run.py tests/unit/test_cli.py \
  tests/unit/test_capture.py tests/unit/test_comparison.py tests/integration/test_run_pipeline.py \
  "tests/integration/test_genetic_fitting.py::"\
"test_small_nondefault_three_family_population_keeps_each_family_and_common_evaluation_inputs" \
  tests/integration/test_generate_cli.py::test_stage_uses_only_stored_family_window_and_configured_final_seed_and_limits
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
git diff --check
```

- [ ] **Step 4: Run the exact fast gate once**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

- [ ] **Step 5: Run the exact branch-coverage gate once**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing -m "not docker and not internet"
```

Require exit zero, package branch coverage at least 90%, and direct behavioral coverage of every failed-function
line and branch. Do not overlap the two broad commands or silently reuse pre-fix results.

- [ ] **Step 6: Run the external commands only as truthful evidence**

Use the exact Docker and Internet commands from Task 5. If the environment is incapable, retain the actionable
failure output in the report, classify it as Class 4, keep external boxes unchecked, and continue every remaining
safe local task. Do not label unavailable external execution as a product test failure.

- [ ] **Step 7: Obtain and address independent whole-Phase review**

Give a fresh reviewer the full Phase 6 commit range, owning architecture, evidence ledger, reports, exact artifact
tree, reuse/corruption matrix, and external limitations. Fix every Critical or Important finding through guarded
TDD and a scoped re-review. A review report without both architecture compliance and code-quality verdicts is
incomplete.

- [ ] **Step 8: Update Roadmap only to the extent proved**

Check local implementation, offline integration, reuse/corruption, seed/limit, directory, summary, and test-command
boxes only when their evidence is green. Check complete Docker-run/cleanup boxes and Phase 6 `Done when` only after
real Docker execution. Never change the older Phase 3 or future Validation Study boxes without their own evidence.

- [ ] **Step 9: Commit evidence and require a clean worktree**

```bash
git diff --check
git status --short
git add architecture/ROADMAP.md
git commit -m "docs: record phase 6 progress"
git status --short
```

If all Phase 6 boxes are genuinely complete, use `docs: record phase 6 completion`; otherwise keep Phase 6 current
and continue to safe Validation Study work without claiming external completion.

## Plan Self-Review

- Tasks 1–4 assign every local Phase 6 deliverable to one owning document, artifact boundary, capture core,
  coordinator, CLI, or deterministic in-process test.
- Strict reuse does not trust filename existence: capture validates the complete pair, fit uses its checkpoint and
  model codecs, generation validates exact bytes/events/lineage, and comparison gains canonical byte-identical
  validation and race handling.
- The coordinator has five explicit calls, no generic workflow abstraction, no duplicate Docker cleanup, no new
  resume option, and no extra successful-run artifact.
- Missing/corrupt/different output behavior and every-stage preservation are enumerated rather than delegated to
  vague error handling.
- Offline analytical evidence deliberately invokes individual stages and never claims to be `run`; complete `run`
  evidence remains the real controlled Docker case.
- Trial selection, final validation, and final artifact generation use the already approved distinct seed and
  limit contracts, with direct integration assertions.
- Task 6 contains the exact guarded serial, fast, coverage, process-control, Docker, and Internet commands and
  forbids false external evidence.
- Every pytest command is guarded with all five named limits, focused/resource-owning tests use `-n 0`, broad tests
  use exact `-n 4 --dist worksteal`, and no command uses `-n auto` or raw pytest.
- Later-task interfaces match Locked Interfaces; no task names an undefined manifest, status file, stage registry,
  cleanup stack, or package dependency.
- Placeholder and ambiguity scan found no deferred implementation language; external capability is explicitly an
  evidence condition, not a hidden implementation gap.
