# MVP Validation Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Execute and publish a strict, reproducible ten-run validation study that measures natural variation and
the three existing model families on three real curl traffic shapes without adding a production command or
scientific algorithm.

**Architecture:** Add one typed support script outside the production package. It renders and validates the locked
study protocol, calls the existing in-process `run_experiment()` boundary for nine serial primary runs, invokes the
installed CLI once for a fresh reproduction, and extracts evidence only through existing production codecs and
evaluation functions. Keep raw Internet captures, response headers, JUnit files, and command output ignored; check
in only portable configurations, canonical finite JSON summaries, concise instructions, the evidence-backed
report, and truthful Roadmap state.

**Tech Stack:** CPython 3.12.3, Python standard library, the existing Trafficlab/Pydantic/TOML boundaries,
Docker Engine and Compose v2, pytest/pytest-xdist/pytest-cov, Ruff, strict Pyright, and the uv locked environment.

## Global Constraints

- Implement only the approved Validation Study design at
  `docs/superpowers/specs/2026-08-13-validation-study-design.md`: the original protocol reviewed at commit
  `76b44b7` plus the independently reviewed Task 7 MMPP-bound amendment recorded by this plan and Git history.
- Do not change `src/trafficlab`, add a package or dependency, add a production CLI command, or add a model,
  metric, plot framework, protocol parser, database, manifest, workflow engine, security subsystem, traffic replay,
  parallel evaluator, Node.js application dependency, or generic subprocess framework.
- If real execution exposes a production defect, stop the study attempt, give the defect its owning architecture
  update and focused TDD fix, re-review it, then restart all nine primary runs under a new study ID.
- Use exactly
  `curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b`
  as the target reference. Record its inspected image ID, repository digests, and configured user.
- Build `docker/capture/` with `docker build --pull=false --iidfile FILE`; use and record the resulting exact
  digest-form SHA-256 image ID without inventing a tag. Record SHA-256 hashes of `docker/capture/Dockerfile` and
  `docker/capture/capture.sh`.
- The operator URL must be absolute credential-free HTTPS with a DNS hostname and no query or fragment. Every
  redirect must satisfy the same rules. Capability evidence must prove byte ranges and an object size from 4 MiB
  through 16 MiB inclusive.
- Use study IDs matching `[a-z0-9][a-z0-9-]{0,31}`. A failed primary or reproduction invalidates the whole
  balanced protocol; preserve the failed evidence and restart with a new ID rather than replacing selected runs.
- Run primary experiments in the exact order `01-short-r1`, `02-streaming-r1`, `03-bursty-r1`,
  `04-streaming-r2`, `05-bursty-r2`, `06-short-r2`, `07-bursty-r3`, `08-short-r3`, `09-streaming-r3`.
- Run all Docker, Internet, primary, and reproduction work serially. Do not overlap public transfers, pytest
  commands, or broad gates.
- All ten experiments use master seed `73`, selection seeds `[17, 29]`, fresh final seed `97`, all three existing
  families, fixed operator/bound values, fixed similarity weights, one target digest, one capture image ID, one URL,
  and one shared read-write scratch mount.
- Selection evidence and fresh held-out evidence are distinct. Family champions use selection trials; primary
  held-out evidence comes only from `FitOutcome.final_trials[0]`; CLI reproduction held-out evidence comes only
  from a fresh direct `evaluate_final(candidate, validated_context, 97)` call.
- Raw trial-limit and final-limit generation with seed `97` must be exactly equal. Quantization and PCAPNG reparse
  are the only permitted reason for held-out versus published score differences.
- Natural variation compares each reference pair in both directions and averages the two aggregate and component
  scores. Never compare cross-workload aggregate distributions as if their multiscale settings were identical.
- Use arithmetic mean, minimum, maximum, range, sample variance with denominator `n - 1`, and sample standard
  deviation. These are three-observation pilot descriptions, not inferential statistics.
- Checked JSON uses UTF-8, sorted keys, compact separators, finite values, and one trailing newline. Reject duplicate
  and unknown keys, booleans where integers/numbers are required, nulls, nonfinite numbers, wrong array order,
  invalid hashes, invalid timestamps, invalid paths, and statistics that do not recompute from source records.
- Checked JSON paths are normalized repository-relative POSIX strings and must resolve beneath the discovered
  repository root. Realized `ExperimentConfig` paths and saved run snapshots are absolute after `load_experiment()`.
- A successful production run directory contains exactly the ordinary nine artifacts. Transfer headers and command
  output live in the sibling ignored evidence tree, never inside a run directory.
- Do not check in Internet PCAPNG, checkpoint, history, best-model, generated PCAPNG, full run log, response header,
  JUnit XML, command stdout/stderr, failed attempt, CID scratch, or complete raw run directory.
- Use TDD for every deterministic behavior: guarded RED for the named test, minimal implementation, guarded GREEN,
  then refactor. Use `apply_patch` for every authored source, test, configuration, documentation, report, and ledger
  edit.
- Every pytest invocation uses `scripts/run_bounded.sh` with all five named limit flags. Focused and
  resource-owning tests use `-n 0`; broad suites use exactly `-n 4 --dist worksteal`; no raw pytest or `-n auto`.
- Focused tests use `2G/3G/512M`, a five-minute wall limit, and ten-second kill grace. Docker uses twenty minutes;
  Internet smoke uses ten minutes. Do not launch a new command until an interrupted or timed-out guard is proven
  inactive with no descendant.
- Keep lines at most 120 characters. Run strict Pyright over the script and both Validation Study test files explicitly,
  because `scripts/` is outside the ordinary include.
- Maintain the ignored SDD workspace `.superpowers/sdd/2026-08-13-validation-study/`. Update `progress.md` with
  the first incomplete step, commit range, exact RED/GREEN commands, gate output, external state, and concerns.
  After each task, write `task-N-report.md`, save its diff, and commit a coherent verified increment. Consolidate
  independent architecture/code-quality review at Tasks 3 (Tasks 1–3), 5 (Tasks 4–5), 6, 7, and 8; fix every
  Critical or Important finding before crossing each review checkpoint.
- Validation Study external evidence may begin only from a clean reviewed commit. Do not commit between `prerequisites` and
  `study`, because prerequisite evidence binds the clean commit and the study permits only its expected generated
  Validation Study paths to differ.
- If `TRAFFICLAB_INTERNET_URL` is absent after every safe local Task 1–6 action, classify Task 7 as the documented
  Class 5 blocker. Do not invent an endpoint, generate real-study configs, fabricate results/report values, or check
  dependent Roadmap boxes.

---

## File Map

- Create `scripts/run_validation_study.py`: the only study-support implementation; strict values/codecs, fixed protocol,
  scratch/header evidence, prerequisite subprocesses, artifact extraction, statistics, primary orchestration, and
  fresh CLI reproduction plus one read-only local publication-audit function.
- Create `tests/unit/test_validation_study.py`: Docker- and Internet-free tests for every value, schema, command, config,
  profile, header, extraction, statistical, orchestration, cleanup, and failure contract.
- Create `tests/integration/test_validation_study_pipeline.py`: one in-process non-Docker extraction test through real
  Trafficlab
  configuration, fit, checkpoint, generation, comparison, and artifact codecs.
- Modify `.gitignore`: ignore exactly `examples/validation_study/.study-work/`; retain all checked Validation Study files.
- Create `examples/validation_study/README.md`: endpoint contract, exact preparation/study/validation/reproduction commands,
  raw-evidence locations, failure restart rule, and audit-retention policy.
- Create during successful external Task 7 `examples/validation_study/configs/short.toml`, `streaming.toml`, and
  `bursty.toml`: complete portable source configs rendered only after capability, Docker, and Internet prerequisites
  pass; each strictly loads to the exhaustive effective oracle.
- Create during successful external Task 7 `examples/validation_study/prerequisites.json`: canonical prerequisite identity,
  capability, image, tool, config-hash, Docker-matrix, and Internet-smoke evidence.
- Create during successful external Task 7 `examples/validation_study/results.json`: canonical nine-primary plus one fresh
  reproduction evidence and recomputed descriptions.
- Create during successful external Task 7 `examples/validation_study/REPORT.md`: observed protocol, natural variation,
  champions, held-out/published scores, runtime, trace disagreements, reproduction, limitations, and one supported
  next-work decision.
- Modify after Task 6 Docker evidence `architecture/ROADMAP.md`: mark only the seven Phase 3 Docker-backed test boxes
  mapped in Task 6 and update their evidence note; leave Internet, Done-when, and `Current` unchanged.
- Modify after successful external Task 7 evidence `architecture/ROADMAP.md`: mark the Internet and Validation Study items
  exactly to the extent proved, then close Phase 3 Done-when only if the real workload and cleanup evidence supports
  it.
- Create/update ignored `.superpowers/sdd/2026-08-13-validation-study/{progress.md,task-*-report.md,review-*}`:
  durable execution ledger and independent-review material; never stage these ignored files.

No production Python file, `pyproject.toml`, lockfile, Dockerfile, test image, or architecture algorithm document is
planned to change.

## Locked Constants and Source Interfaces

The support script uses these exact imports rather than copying production codecs or scientific behavior:

```python
import argparse
import hashlib
import json
import math
import os
import platform
import re
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from statistics import fmean, variance
from types import MappingProxyType
from typing import Literal, Protocol, cast
from urllib.parse import urljoin, urlsplit

from trafficlab import __version__
from trafficlab.artifacts import atomic_replace, quantize_generated_events
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import (
    ComparisonResult,
    compare_traces,
    parse_comparison_result,
    render_comparison_result,
    sha256_bytes,
    similarity_settings_sha256,
)
from trafficlab.config import ExperimentConfig, FamilyName, SimilarityConfig
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.checkpoint import CheckpointState, parse_checkpoint, render_history_csv
from trafficlab.genetic.evaluation import evaluate_final, validate_evaluation_context
from trafficlab.genetic.strategy import make_strategy_context
from trafficlab.genetic.types import METHOD_ORDER, Candidate, CandidateId, TrialResult
from trafficlab.models.registry import BestModel, get_family, load_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.run import RunResult, run_experiment
from trafficlab.trace import Direction, TraceEvent, align_generated, normalize_reference, parse_capture_metadata
```

The exact constants are:

```python
TARGET_REFERENCE = "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
FAMILY_ORDER: tuple[FamilyName, ...] = ("markov_renewal", "mmpp", "poisson_empirical")
PUBLISHED_METHOD_ORDER = METHOD_ORDER
ARTIFACT_NAMES = (
    "experiment.toml",
    "reference.pcapng",
    "capture.json",
    "checkpoint.json",
    "ga_history.csv",
    "best_model.json",
    "generated.pcapng",
    "similarity.json",
    "run.log",
)
PRIMARY_ORDER = (
    (1, "01-short-r1", "short", 1),
    (2, "02-streaming-r1", "streaming", 1),
    (3, "03-bursty-r1", "bursty", 1),
    (4, "04-streaming-r2", "streaming", 2),
    (5, "05-bursty-r2", "bursty", 2),
    (6, "06-short-r2", "short", 2),
    (7, "07-bursty-r3", "bursty", 3),
    (8, "08-short-r3", "short", 3),
    (9, "09-streaming-r3", "streaming", 3),
)
RUNTIME_BOUNDARY = "run_experiment_cached_images_full_lifecycle"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_HEADINGS = (
    "## Question, scope, environment, and protocol",
    "## Natural variation",
    "## Family champions",
    "## Held-out, published, and runtime",
    "## Trace diagnostics",
    "## Saved-run reproduction",
    "## Limitations and next work",
)
```

Use a callable protocol around `subprocess.run` solely for deterministic injection. Each owning routine builds and
checks its own literal argv; do not add command registration, retries, dependency graphs, shell strings, or generic
pipelines.

```python
type JsonScalar = str | int | float | bool
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]
type FrozenJsonObject = Mapping[str, FrozenJsonValue]
type WorkloadName = Literal["short", "streaming", "bursty"]
type TransferRange = tuple[int, int, str]
type PrerequisiteCommandKind = Literal["docker_matrix", "internet_smoke"]


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    name: WorkloadName
    argv: tuple[str, ...]
    transfers: tuple[TransferRange, ...]
    workload_timeout_seconds: float
    total_timeout_seconds: float
    multiscale_widths_seconds: tuple[float, float]


@dataclass(frozen=True, slots=True)
class StudyRunSpec:
    execution_order: int
    run_id: str
    workload: WorkloadName
    repeat: int
    config_path: Path
    run_directory: Path
    transfer_evidence_directory: Path


@dataclass(frozen=True, slots=True)
class StudyRunRecord:
    execution_order: int
    run_id: str
    key: FrozenJsonObject
    config_path: str
    run_directory: str
    transfer_evidence_directory: str
    elapsed_seconds: float
    reuse: FrozenJsonObject
    cleanup_verified: bool
    transfer_responses: tuple[FrozenJsonObject, ...]
    artifact_sha256: FrozenJsonObject
    reference: FrozenJsonObject
    generated: FrozenJsonObject
    family_champions: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    winner: FrozenJsonObject
    held_out: FrozenJsonObject
    published: FrozenJsonObject
    raw_sequence: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class ReproductionRecord:
    document: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class StudyResults:
    schema_version: int
    environment: FrozenJsonObject
    protocol: FrozenJsonObject
    runs: tuple[StudyRunRecord, ...]
    natural_variation: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    workload_summaries: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    reproduction: ReproductionRecord


@dataclass(frozen=True, slots=True)
class PrerequisiteResults:
    schema_version: int
    created_utc: str
    study_id: str
    git_commit: str
    git_tree_clean: bool
    url: str
    tools: FrozenJsonObject
    images: FrozenJsonObject
    capability: FrozenJsonObject
    config_sha256: FrozenJsonObject
    commands: tuple[FrozenJsonObject, FrozenJsonObject]
```

Every aggregate builder calls `_freeze_json()` before construction and retains tuples plus `MappingProxyType`
mappings; each dataclass `__post_init__` rejects a mutable or wrong-shaped field. `_thaw_json()` creates fresh
serialization dictionaries/lists. `ReproductionRecord.document` therefore stays a deeply immutable aggregate
boundary while still receiving the exact reproduction validator.

The core function signatures are fixed here so tasks do not drift. This is interface notation, not executable
Python; each parenthesized declaration below maps to one typed function in the support script:

```text
validate_study_id(value: str) -> str
validate_endpoint_url(value: str) -> str
workload_specs(url: str) -> tuple[WorkloadSpec, WorkloadSpec, WorkloadSpec]
build_base_config(
    workload: WorkloadSpec,
    *,
    repository_root: Path,
    study_id: str,
    url: str,
    capture_image_id: str,
) -> ExperimentConfig
render_checked_base_config(config: ExperimentConfig, destination: Path, repository_root: Path) -> bytes
validate_base_configs(
    repository_root: Path,
    prerequisites: PrerequisiteResults,
) -> dict[WorkloadName, ExperimentConfig]
_config_with_run_directory(config: ExperimentConfig, run_directory: Path) -> ExperimentConfig
_render_realized_config(config: ExperimentConfig, destination: Path) -> bytes

render_prerequisite_results(value: PrerequisiteResults) -> bytes
parse_prerequisite_results(content: bytes, *, repository_root: Path) -> PrerequisiteResults
_prerequisite_document(value: PrerequisiteResults) -> JsonObject
_validate_prerequisite_document(
    document: JsonObject,
    *,
    repository_root: Path,
) -> PrerequisiteResults
_publish_prerequisites(
    path: Path,
    value: PrerequisiteResults,
    *,
    repository_root: Path,
) -> None
render_study_results(value: StudyResults) -> bytes
parse_study_results(content: bytes, *, repository_root: Path) -> StudyResults
descriptive_statistics(values: Sequence[int | float]) -> JsonObject
audit_published_study(
    *,
    repository_root: Path,
    prerequisite_path: Path,
    result_path: Path,
    report_path: Path,
) -> None
_utc_now() -> datetime
_load_reference_trace(run_directory: Path) -> tuple[TraceEvent, ...]
_trace_summary(
    events: Sequence[TraceEvent],
    result: ComparisonResult,
    *,
    role: Literal["reference", "generated"],
) -> JsonObject
_study_run_document(value: StudyRunRecord) -> JsonObject
_reproduction_document(value: ReproductionRecord) -> JsonObject
_study_document(value: StudyResults) -> JsonObject
_publish_results(path: Path, value: StudyResults, *, repository_root: Path) -> None

prepare_transfer_scratch(
    repository_root: Path,
    study_id: str,
    run_id: str,
    workload: WorkloadSpec,
) -> dict[str, tuple[Path, int]]
archive_transfer_evidence(
    repository_root: Path,
    study_id: str,
    run_id: str,
    workload: WorkloadSpec,
    prepared: Mapping[str, tuple[Path, int]],
    *,
    object_size_bytes: int,
) -> tuple[JsonObject, ...]

extract_primary_record(
    repository_root: Path,
    spec: StudyRunSpec,
    workload: WorkloadSpec,
    result: RunResult,
    elapsed_seconds: float,
    transfer_responses: tuple[JsonObject, ...],
) -> StudyRunRecord
natural_variation(
    records: Sequence[StudyRunRecord],
    traces: Mapping[tuple[WorkloadName, int], tuple[TraceEvent, ...]],
    settings: Mapping[WorkloadName, SimilarityConfig],
) -> tuple[JsonObject, JsonObject, JsonObject]
workload_summaries(records: Sequence[StudyRunRecord]) -> tuple[JsonObject, JsonObject, JsonObject]
reconstruct_reproduction(
    repository_root: Path,
    spec: StudyRunSpec,
    source: StudyRunRecord,
    *,
    command: tuple[str, ...],
    guard_command: tuple[str, ...],
    completed: subprocess.CompletedProcess[bytes],
    elapsed_seconds: float,
    transfer_responses: tuple[JsonObject, ...],
) -> ReproductionRecord

_docker_matrix_argv(study_id: str) -> tuple[str, ...]
_internet_smoke_argv(study_id: str, url: str) -> tuple[str, ...]
_project_command_argv(
    kind: PrerequisiteCommandKind,
    argv: Sequence[str],
    *,
    repository_root: Path,
) -> tuple[str, ...]
_live_argv(
    kind: PrerequisiteCommandKind,
    argv: Sequence[str],
    *,
    repository_root: Path,
) -> tuple[str, ...]
_parse_junit_counts(content: bytes) -> JsonObject

run_prerequisites(
    url: str,
    study_id: str,
    *,
    repository_root: Path,
    runner: CommandRunner,
    utc_now: Callable[[], datetime],
) -> PrerequisiteResults
run_study(
    url: str,
    study_id: str,
    prerequisite_path: Path,
    *,
    repository_root: Path,
    run: Callable[[Path], RunResult],
    runner: CommandRunner,
    perf_counter: Callable[[], float],
    utc_now: Callable[[], datetime],
) -> StudyResults
build_parser() -> argparse.ArgumentParser
main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    run: Callable[[Path], RunResult] = run_experiment,
    runner: CommandRunner = cast(CommandRunner, subprocess.run),
    perf_counter: Callable[[], float] = time.perf_counter,
    utc_now: Callable[[], datetime] = _utc_now,
) -> int
```

`main()` follows the production CLI boundary: no arguments prints usage and returns `2`; parser `SystemExit` becomes
its integer status. Command entry validation converts its direct validator `ValueError` to `TrafficlabError`, while
filesystem and subprocess owners raise `TrafficlabError` themselves. `main()` prints one actionable line prefixed
by `validation-study:` with two clauses separated by `;` and returns the error status. It does not catch `KeyboardInterrupt`
as success, start background work, or delete retained evidence.

Every subprocess call has a literal owner and timeout:

```python
SUBPROCESS_TIMEOUTS = {
    "git_or_version": 20.0,
    "image_pull_or_build": 300.0,
    "capability": 45.0,
    "container_inspect_or_remove": 20.0,
    "docker_matrix_guard": 1230.0,
    "internet_smoke_guard": 630.0,
    "reproduction_guard": 1230.0,
}
```

Git/Docker identity uses exact argv `git rev-parse HEAD`, `git status --porcelain=v1 --untracked-files=all`,
`docker version --format {{.Server.Version}}`, and `docker compose version --short`; Python and Trafficlab identities
come directly from `platform.python_version()` and `trafficlab.__version__`. Image preparation uses `docker image
pull TARGET_REFERENCE`, `docker image inspect TARGET_REFERENCE`, and the exact build argv in Task 4. Container
ownership uses `docker container inspect ID_OR_NAME`; proven owned failure cleanup uses `docker container rm --force
ID`, then repeats inspect for both exact ID and name and requires absent/nonzero results. No subprocess retries.

## Locked Workloads and Experiment Oracle

The common curl transfer options, in this exact order, are:

```python
CURL_COMMON = (
    "--fail",
    "--silent",
    "--show-error",
    "--location",
    "--max-redirs",
    "3",
    "--proto",
    "=https",
    "--proto-redir",
    "=https",
    "--http1.1",
    "--connect-timeout",
    "15",
)
```

Build these exact profiles; tests compare complete tuples, including every `--next`:

```python
short = (
    *CURL_COMMON,
    "--max-time",
    "30",
    "--limit-rate",
    "4M",
    "--range",
    "0-262143",
    "--max-filesize",
    "262144",
    "--dump-header",
    "/trafficlab-study/short.headers",
    "--output",
    "/dev/null",
    "--url",
    url,
)
streaming = (
    *CURL_COMMON,
    "--max-time",
    "40",
    "--limit-rate",
    "256K",
    "--range",
    "0-4194303",
    "--max-filesize",
    "4194304",
    "--dump-header",
    "/trafficlab-study/streaming.headers",
    "--output",
    "/dev/null",
    "--url",
    url,
)
bursty_starts = (0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016)
bursty = ("--parallel", "--parallel-max", "4", "--fail-early", *fully_expanded_groups)
```

Each bursty group uses `CURL_COMMON`, `--max-time 30`, its exact 32 KiB range, `--max-filesize 32768`,
`/trafficlab-study/bursty-INDEX.headers`, `/dev/null`, and the URL. Insert `--next` between groups, never after the
eighth. Short expects `(0, 262143, "short.headers")`; streaming expects
`(0, 4194303, "streaming.headers")`; bursty expects the eight start/end/header triples in index order.

Every effective config has the following values. No omitted default is permitted:

| Section/field | Exact effective value |
|---|---|
| `run.directory` | the absent absolute named primary or reproduction directory |
| `run.minimum_free_bytes` | `1048576` |
| `run.master_seed` / `run.final_seed` | `73` / `97` |
| `target.image` | `TARGET_REFERENCE` |
| `target.argv` | the complete selected profile tuple |
| `target.environment` / `target.working_directory` | `{}` / `/` |
| `target.mounts` | one mount from the resolved study mount to `/trafficlab-study`, `read_only=false` |
| `capture.image` | the one exact digest-form SHA-256 image ID from prerequisites |
| `capture.network_probe_url` | the operator URL |
| `capture.readiness_timeout_seconds` / `flush_timeout_seconds` | `10.0` / `5.0` |
| `capture.workload_timeout_seconds` | `35.0` short/bursty; `50.0` streaming |
| `capture.total_timeout_seconds` | `90.0` short/bursty; `120.0` streaming |
| trial generation | `25000` packets, `40000000` bytes, `5.0` seconds |
| final generation | `50000` packets, `80000000` bytes, `10.0` seconds |
| genetic | population `6`, generations `2`, tournament `2`, elite `1` |
| genetic seeds/retry/stopping | `[17,29]`, retries `3`, generations `0`, tolerance `0.0`, resume `true` |
| models | `poisson_empirical`, `markov_renewal`, `mmpp` all enabled |
| similarity common | quantile `0.95`, lags `[1]`, lag weights `[1.0]`, ACF weights `0.5/0.5` |
| multiscale widths | `[0.001,0.01]` short/bursty; `[0.25,1.0]` streaming |
| multiscale weights/cap | scale `0.5/0.5`, packet/byte `0.5/0.5`, cap `100000` |
| method weights | exactly `0.25` for all four published methods |

Family values are exact: Poisson operators `0.9/1.0/0.1`, `c_lambda [0.25,4.0]`; Markov operators
`0.9/0.2/0.1`, `q1 [0.1,0.4]`, `q2 [0.6,0.9]`, `alpha [0.0,2.0]`, integer `r [1,8]`, `c_t [0.25,4.0]`; MMPP
operators `0.9/0.25/0.1`, `q01 [0.01,10.0]`, `q10 [0.01,10.0]`, `lambda0 [10.0,100.0]`, and
`lambda1 [0.1,1000.0]`.

The three checked base configs use the first primary directory for that workload:

- short: `runs/validation_study/STUDY_ID/01-short-r1`;
- streaming: `runs/validation_study/STUDY_ID/02-streaming-r1`;
- bursty: `runs/validation_study/STUDY_ID/03-bursty-r1`.

This gives every checked config a genuine absent primary destination rather than a sentinel. The checked TOML uses
portable relative source operands from `examples/validation_study/configs/`; immediately reload it with `load_experiment()`
and require equality to the exhaustive absolute oracle. Ignored realized configs are rendered with absolute paths
under `runs/validation_study/STUDY_ID/realized-configs/` and reload to their exact effective objects.

The portable TOML literals are exact: `run.directory` is
`../../../runs/validation_study/STUDY_ID/FIRST-RUN-ID` and `target.mounts[0].source` is
`../.study-work/mount/STUDY_ID`. `load_experiment()` resolves them respectively beneath repository `runs/validation_study`
and `examples/validation_study/.study-work/mount`; no other relative path is permitted.

## Locked JSON Schema

All objects require exactly their listed keys. The implementation stores leaf objects as freshly validated
`JsonObject` values rather than adding a class per leaf.

| Record | Exact keys |
|---|---|
| prerequisite root | exact tuple `PREREQUISITE_ROOT_KEYS` below |
| tools | `python_version,trafficlab_version,docker_engine_version,docker_compose_version,platform` |
| images | exact tuple `IMAGE_KEYS` below |
| command | `kind,argv,started_utc,completed_utc,exit_status,tests,stdout_sha256,stderr_sha256,junit_sha256` |
| test counts | `total,passed,failed,errors,skipped` |
| capability | exact tuple `CAPABILITY_KEYS` below |
| result root | `schema_version,environment,protocol,runs,natural_variation,workload_summaries,reproduction` |
| environment | exact tuple `ENVIRONMENT_KEYS` below |
| protocol | exact tuple `PROTOCOL_KEYS` below |
| seeds | `master,final,selection` |
| workload definition | `name,argv,workload_timeout_seconds,total_timeout_seconds,multiscale_widths_seconds` |
| run key | `workload,repeat` |
| candidate ID | `birth_generation,birth_index` |
| method scores | `autocorrelation,frame_size_ks,iat_ks,multiscale_rate` |
| score | `aggregate,methods` |
| descriptive | `count,mean,minimum,maximum,range,sample_variance,sample_standard_deviation` |
| score summary | `aggregate,methods` |
| direction values | `outbound,inbound` |
| sample | `count,minimum,median,quantile_probability,quantile,maximum,zero_count` |
| scale total | `width_seconds,bins_per_direction,packet_totals,byte_totals` |
| trace summary | `packet_count,observation_window_seconds,packet_totals,byte_totals,frame_lengths,iats,scales` |
| transfer response | exact tuple `TRANSFER_RESPONSE_KEYS` below |
| family champion | `family,candidate_id,genes,selection_fitness,selection_seeds,selection_score` |
| winner | `family,candidate_id,genes,selection_fitness` |
| held out | `seed,score,source` |
| published | `seed,score` |
| raw sequence | exact tuple `RAW_SEQUENCE_KEYS` below |
| reuse | `capture,best_model,generated,similarity` |
| artifact hashes | the exact nine `ARTIFACT_NAMES` keys |
| study run | every `StudyRunRecord` field in its declaration above |
| pair comparison | `left_repeat,right_repeat,forward,reverse,symmetric` |
| natural variation | `workload,pairs,reference_descriptors` |
| family summary | `selection_fitness,selection_components` |
| workload summary | exact tuple `WORKLOAD_SUMMARY_KEYS` below |
| score delta | `aggregate,methods` |
| reproduction comparison | exact tuple `REPRODUCTION_COMPARISON_KEYS` below |

The long key tuples referenced in the table are:

```python
PREREQUISITE_ROOT_KEYS = (
    "schema_version",
    "created_utc",
    "study_id",
    "git_commit",
    "git_tree_clean",
    "url",
    "tools",
    "images",
    "capability",
    "config_sha256",
    "commands",
)
IMAGE_KEYS = (
    "target_reference",
    "target_image_id",
    "target_repo_digests",
    "target_config_user",
    "capture_image_id",
    "capture_dockerfile_sha256",
    "capture_script_sha256",
)
CAPABILITY_KEYS = (
    "argv",
    "started_utc",
    "completed_utc",
    "exit_status",
    "status",
    "content_length",
    "object_size_bytes",
    "redirect_count",
    "body_bytes_downloaded",
    "content_range",
    "final_url",
    "mount_source",
    "canary_archive_path",
    "canary_sha256",
    "container_id",
    "stdout_sha256",
    "stderr_sha256",
    "used_image_default_user",
    "mount_directory_mode",
    "canary_file_mode",
    "canary_archive_mode",
    "container_cleanup_verified",
)
ENVIRONMENT_KEYS = (
    "git_commit",
    "python_version",
    "trafficlab_version",
    "docker_engine_version",
    "docker_compose_version",
    "platform",
    "target_image_id",
    "capture_image_id",
    "study_date_utc",
)
PROTOCOL_KEYS = (
    "study_id",
    "url",
    "capability",
    "prerequisites_sha256",
    "target_reference",
    "capture_image_id",
    "transfer_evidence_mount_source",
    "base_config_sha256",
    "primary_order",
    "seeds",
    "families",
    "methods",
    "workloads",
    "runtime_boundary",
)
TRANSFER_RESPONSE_KEYS = (
    "transfer_index",
    "requested_start",
    "requested_end",
    "status",
    "content_length",
    "content_range",
    "header_archive_path",
    "header_sha256",
    "scratch_precreate_mode",
    "archive_mode",
    "inode_preserved",
)
RAW_SEQUENCE_KEYS = (
    "seed",
    "observation_window_seconds",
    "trial_event_count",
    "final_event_count",
    "raw_events_equal",
    "held_out_score_reproduced",
    "reparsed_event_count",
    "reparsed_matches_quantized",
)
WORKLOAD_SUMMARY_KEYS = (
    "workload",
    "runtime",
    "family_champions",
    "winner_selection_fitness",
    "held_out",
    "published",
    "reference_descriptors",
    "winner_counts",
)
REPRODUCTION_COMPARISON_KEYS = (
    "winner_family_equal",
    "winner_genes_equal",
    "winner_selection_fitness_delta",
    "held_out_delta",
    "published_delta",
    "reference_similarity",
)
```

The reproduction object requires exactly these keys:

```text
source_key, execution_order, run_id, config_path, run_directory,
transfer_evidence_directory, command, guard_command, guard_exit_status,
guard_stdout_sha256, guard_stderr_sha256, elapsed_seconds, changed_config_fields,
same_locked_config, seeded_artifact_count, cleanup_verified, reuse,
transfer_responses, artifact_sha256, reference, generated, family_champions,
winner, held_out, published, raw_sequence, comparison_to_source
```

The JSON scalar and collection types are also locked; render frozen tuples as arrays and frozen mappings as
objects, then restore those immutable forms after parsing:

- `tools` values are nonempty strings. Image values are strings except `target_repo_digests`, which is a nonempty
  sorted string array; `target_config_user` is the only image string allowed to be empty.
- A prerequisite command has string `kind`, string-array `argv`, string timestamps/hashes, integer `exit_status`,
  and a test-count object. Every test-count field is an exact nonnegative integer.
- Capability string fields are `argv` elements, timestamps, range/final URL/paths/hashes/container ID; integer
  fields are status, lengths/counts, and modes; `used_image_default_user` and cleanup are booleans.
- Environment fields are strings. Protocol fields are string ID/URL/hashes/reference/path/runtime, nested
  capability/seeds, a profile-hash object, run-key array, string family/method arrays, and workload-definition array.
- Seeds are exact integers and an integer selection array. Workload definitions contain string name/argv plus float
  timeouts and float multiscale widths. Run keys contain string workload and integer repeat.
- Candidate IDs contain integers. Genes contain only family-ordered exact floats except Markov `r`, which is an
  integer. Score aggregate/components are floats. Descriptive numeric values are floats except integer `count`.
- Direction values contain nonnegative integers. Samples contain integer `count`/`zero_count` and otherwise floats.
  Scale totals contain float width, integer bins, and direction-value packet/byte objects.
- Trace summaries contain integer packet count, float window, direction-value totals, sample objects, and a
  scale-total array. Transfer responses contain integer indexes/ranges/status/length/modes, string range/path/hash,
  and boolean inode proof.
- Family champions contain string family, candidate object, gene array, float fitness, integer seed array, and score
  object. Winners omit seeds/score. Held-out records use integer seed, score, and one exact string authority;
  published records use integer seed and score.
- Raw-sequence records use integer seed/event counts, float W, and booleans for the three equality proofs. Reuse
  fields and cleanup are booleans. Artifact hash objects contain exactly nine hash strings.
- Study runs use integer order, string IDs/paths, float elapsed time, the nested records named in the dataclass, and
  arrays for responses/champions. Pair repeats are integers and their three values are score objects.
- Natural-variation and workload-summary names are strings; pairs are arrays; descriptor/family/count collections
  are exact objects. Family counts are nonnegative integers; all summary leaves are descriptive or score summaries.
- Reproduction uses integer order/status/seeded count, string IDs/paths/hashes, string arrays for commands and the
  sole changed field, float elapsed time, boolean config/cleanup claims, nested run evidence, and comparison object.
  Reproduction deltas are floats; winner equality fields are booleans; held-out/published deltas are score-delta
  objects; reference similarity is a score object.

Enforce these cross-record invariants during construction and parsing:

- schema versions are exact integer `1`; Git commit is 40 lowercase hexadecimal characters; every SHA-256 is 64;
  UTC timestamps use RFC 3339 and end in `Z`, and completion never precedes start;
- profile hash maps are exactly `short`, `streaming`, `bursty`; families are lexical; methods are published order;
- prerequisite commands are exactly Docker matrix then Internet smoke; both have status zero, positive test count,
  every test passed, and no failures, errors, or skips;
- capability is status 206, one downloaded byte, `Content-Range: bytes 0-0/TOTAL`, length one, total 4–16 MiB,
  zero through three redirects, exact final URL, default image user, modes 0755/0666/0600 in decimal, and verified
  container removal;
- result protocol capability is the byte-for-byte nested prerequisite capability object;
  `prerequisites_sha256` hashes the exact canonical prerequisite file, and environment commit/tool/image values
  equal both prerequisite evidence and the live study environment;
- genes use exact family order and scalar types, inclusive bounds, `q1 < q2`, and `lambda0 < lambda1`;
- every score is a finite float in `[0.0,1.0]`; deltas are finite in `[-1.0,1.0]`; exact statistic records
  recompute from their three source values;
- samples use the arithmetic median and nearest rank `ceil(0.95*n)`, frame zero count is zero, IAT count is packet
  count minus one, and direction totals match trace totals;
- primary runs are exactly nine in fixed order with unique keys/directories, false reuse, positive time, three
  lexical champions, winner consistency, seed 97 held-out/published evidence, and nine exact artifact hashes;
- pair order is `(1,2)`, `(1,3)`, `(2,3)` and each symmetric score is the arithmetic mean of forward/reverse;
- workload arrays are short, streaming, bursty; winner counts contain exactly the three families and sum to three;
- reproduction is streaming repeat 2, order 10, exact run ID/path, only `run.directory` changed, zero seeded
  artifacts, false reuse, exact guard command, fresh artifacts, and honestly recomputed source deltas;
- `command` is `uv run --locked trafficlab run CONFIG`; `guard_command` is the exact five-flag 20-minute wrapper;
- parse validation rebuilds natural variation descriptions, workload summaries, winner counts, and reproduction
  deltas from source records rather than trusting serialized derived values. It recomputes every symmetric pair
  from its stored forward/reverse scores; directional pair scores are recomputed from raw traces during live study
  extraction and retained-audit validation.

---

### Task 1: Strict study values, canonical schemas, and descriptive statistics

**Files:**

- Create: `scripts/run_validation_study.py`
- Create: `tests/unit/test_validation_study.py`
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/progress.md`
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-1-report.md`

**Interfaces:**

- Consumes: Locked Constants, aggregate dataclasses, schema key sets, scalar/path rules, and the existing
  `atomic_replace()` boundary.
- Produces: exact scalar validators, aggregate construction, canonical prerequisite/results parse/render, and
  `descriptive_statistics()` for all later tasks.

- [ ] **Step 1: Initialize the ignored execution ledger**

Create the SDD directory, then use `apply_patch` to write `progress.md` with plan commit, active Task 1, clean base
commit, first RED node, empty concerns, and an evidence table with columns `task`, `red`, `green`, `commit`,
`review`, `external_state`. Confirm it remains ignored:

```bash
git check-ignore -v .superpowers/sdd/2026-08-13-validation-study/progress.md
git status --short
```

- [ ] **Step 2: Write failing scalar, path, time, and statistic tests**

Add the exact node
`tests/unit/test_validation_study.py::test_study_id_url_repository_path_and_utc_validators_are_exact`. It accepts
`study-1`, a credential-free HTTPS URL, `evidence/study-1/file`, and `2026-08-13T12:00:00Z`; it rejects every invalid
value named after this code block. Add this exact statistics test body:

```python
def test_median_quantile_and_descriptive_statistics_use_published_formulas() -> None:
    assert study._sample_record([1.0, 3.0, 5.0], quantile_probability=0.95, zero_count=0)["median"] == 3.0
    assert study._sample_record([1.0, 3.0, 5.0, 9.0], quantile_probability=0.95, zero_count=0)["median"] == 4.0
    assert study._sample_record([1.0, 3.0, 5.0, 9.0], quantile_probability=0.5, zero_count=0)["quantile"] == 3.0
    assert study.descriptive_statistics([1, 2, 3]) == {
        "count": 3,
        "mean": 2.0,
        "minimum": 1.0,
        "maximum": 3.0,
        "range": 2.0,
        "sample_variance": 1.0,
        "sample_standard_deviation": 1.0,
    }
```

Reject empty/wrong-count statistics, booleans, nonfinite numbers, wrong score ranges, malformed hashes, duplicate
keys, non-UTC timestamps, absolute/backslash/dot/dot-dot paths, and a resolved path escaping the injected root.

- [ ] **Step 3: Run the guarded scalar/statistic RED**

```bash
validation_study_extraction_node="tests/integration/test_validation_study_pipeline.py::"
validation_study_extraction_node+="test_validation_study_extraction_uses_real_three_family_artifacts_fresh_seed_and_lineage"
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_study_id_url_repository_path_and_utc_validators_are_exact \
  tests/unit/test_validation_study.py::test_median_quantile_and_descriptive_statistics_use_published_formulas
```

Expected: collection/import failure because the study module and functions do not exist.

- [ ] **Step 4: Implement exact value primitives and aggregate dataclasses**

Implement the declarations under Locked Interfaces plus these narrow helper signatures:

```text
_exact_object(value: object, keys: Sequence[str], *, name: str) -> dict[str, object]
_strict_int(value: object, *, name: str, minimum: int | None = None) -> int
_strict_float(
    value: object,
    *,
    name: str,
    lower: float | None = None,
    upper: float | None = None,
) -> float
_strict_bool(value: object, *, name: str) -> bool
_strict_string(value: object, *, name: str, nonempty: bool = True) -> str
_sha256(value: object, *, name: str) -> str
_utc_timestamp(value: object, *, name: str) -> str
_repository_relative_path(value: object, *, repository_root: Path, name: str) -> str
_freeze_json(value: JsonValue) -> FrozenJsonValue
_thaw_json(value: FrozenJsonValue) -> JsonValue
_canonical_json(document: JsonObject) -> bytes
_load_json(content: bytes) -> JsonObject
_median(values: Sequence[int | float]) -> float
_nearest_rank(values: Sequence[int | float], probability: float) -> float
_sample_record(
    values: Sequence[int | float],
    *,
    quantile_probability: float,
    zero_count: int,
) -> JsonObject
```

Use exact type tests (`type(value) is int/float/bool`), `math.isfinite`, `PurePosixPath`, `Path.resolve()`,
`statistics.fmean`, `statistics.variance`, and `math.sqrt`. Do not coerce JSON scalars or permit null.

- [ ] **Step 5: Run the guarded scalar/statistic GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_study_id_url_repository_path_and_utc_validators_are_exact \
  tests/unit/test_validation_study.py::test_median_quantile_and_descriptive_statistics_use_published_formulas
```

Expected: both nodes pass.

- [ ] **Step 6: Write failing canonical prerequisite-codec tests**

Build one complete valid `PrerequisiteResults` test value with fixed RFC 3339 times, hashes, exact two-command
order, capability modes, and repository-relative paths. Add exact nodes
`test_prerequisite_codec_round_trips_exact_canonical_schema` and
`test_prerequisite_codec_rejects_each_contract_violation`. Parameterize the latter with these exact
mutation/message pairs:

```python
[
    ("unknown-root", "exact keys"),
    ("duplicate-key", "duplicate JSON key"),
    ("wrong-command-order", "docker_matrix"),
    ("skipped-test", "skipped"),
    ("wrong-image", "target reference"),
    ("wrong-capability-mode", "canary file mode"),
    ("path-escape", "repository-relative"),
    ("nan", "invalid JSON constant"),
]
```

Assert `render(parse(render(value)))` byte equality, lexical key order, compact separators, no null, no whitespace
before the final newline, and failure if a serialized derived field changes.

- [ ] **Step 7: Run the guarded prerequisite-codec RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_prerequisite_codec_round_trips_exact_canonical_schema \
  tests/unit/test_validation_study.py::test_prerequisite_codec_rejects_each_contract_violation
```

Expected: failure because prerequisite render/parse is absent.

- [ ] **Step 8: Implement strict prerequisite parse/render and atomic validator**

Implement one validator per leaf key set listed in Locked JSON Schema. Add
`_prerequisite_document(value: PrerequisiteResults) -> JsonObject` and
`_validate_prerequisite_document(document: JsonObject, *, repository_root: Path) -> PrerequisiteResults`, then
publish atomically as follows:

```python
def _publish_prerequisites(path: Path, value: PrerequisiteResults, *, repository_root: Path) -> None:
    content = render_prerequisite_results(value)

    def validate(persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=repository_root)
        if render_prerequisite_results(parsed) != content:
            raise ValueError("persisted prerequisite JSON is not canonical")

    atomic_replace(path, content, validator=validate)
```

The parser uses `object_pairs_hook` to reject duplicates and `parse_constant` to reject nonfinite constants.

- [ ] **Step 9: Run the guarded prerequisite-codec GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_prerequisite_codec_round_trips_exact_canonical_schema \
  tests/unit/test_validation_study.py::test_prerequisite_codec_rejects_each_contract_violation
```

Expected: all parameter cases pass.

- [ ] **Step 10: Write failing complete result-schema tests**

Construct an exact nine-run document in fixed order, three natural-variation records, three summaries, and one
reproduction. Keep source values deliberately unequal so recomputation is observable. Add exact nodes
`test_result_codec_round_trips_nine_runs_reproduction_and_recomputed_summaries` and
`test_result_codec_rejects_nested_schema_and_cross_record_inconsistency`. Parameterize rejection with:

```python
[
    "wrong-primary-order",
    "duplicate-run-key",
    "missing-family",
    "wrong-method-order",
    "nullable-value",
    "stale-statistic",
    "wrong-pair-average",
    "winner-count-mismatch",
    "wrong-reproduction-source",
    "extra-artifact-hash",
    "true-reuse",
    "wrong-guard",
]
```

Assert exact gene scalar types, trace/sample arithmetic, path confinement, score/delta ranges, runtime positivity,
the false reuse map, exact nine hashes, primary/reproduction paths, and a byte-stable render/parse cycle.

- [ ] **Step 11: Run the guarded result-codec RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_result_codec_round_trips_nine_runs_reproduction_and_recomputed_summaries \
  tests/unit/test_validation_study.py::test_result_codec_rejects_nested_schema_and_cross_record_inconsistency
```

Expected: failure because result construction, recomputation, and render/parse are absent.

- [ ] **Step 12: Implement the complete result validator and codec**

Implement all result leaf validators from the schema table, `_study_run_document`, `_reproduction_document`,
`_study_document`, `render_study_results`, `parse_study_results`, and atomic `_publish_results`. Parsing must derive
the expected statistics/pairs/counts/deltas from parsed source records and compare the serialized values exactly.
Do not accept a derived value merely because it has the right scalar range.

- [ ] **Step 13: Run Task 1 GREEN and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_validation_study.py
uv run --locked ruff format --check scripts/run_validation_study.py tests/unit/test_validation_study.py
uv run --locked ruff check scripts/run_validation_study.py tests/unit/test_validation_study.py
uv run --locked pyright scripts/run_validation_study.py tests/unit/test_validation_study.py
git diff --check
```

- [ ] **Step 14: Commit, report, and queue Task 1 for the Task 3 review checkpoint**

```bash
git add scripts/run_validation_study.py tests/unit/test_validation_study.py
git commit -m "feat: add validation study schema"
```

Write `task-1-report.md` with RED/GREEN output, schema coverage, type/static results, commit hash, and concerns. Save
the exact Task 1 diff in the ignored SDD directory for the consolidated independent Tasks 1–3 review after Task 3.

---

### Task 2: Workload realization, exhaustive configs, and range/header evidence

**Files:**

- Modify: `scripts/run_validation_study.py`
- Modify: `tests/unit/test_validation_study.py`
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-2-report.md`

**Interfaces:**

- Consumes: Task 1 validators/codecs, `ExperimentConfig`, `render_effective_config()`, and `load_experiment()`.
- Produces: exact URL/profile/config construction, checked/realized config validation, repository path projections,
  range response parsing, and scratch/archive evidence used by Tasks 4–5.

- [ ] **Step 1: Write failing URL and exact-profile tests**

Add exact nodes `test_endpoint_contract_rejects_noncredential_free_https_object_urls` and
`test_workload_specs_expand_exact_short_streaming_and_eight_bursty_argv`. The URL rejection inputs are:

```python
[
    "http://example.test/object",
    "https://user@example.test/object",
    "https://example.test/object?query=1",
    "https://example.test/object#fragment",
    "https://127.0.0.1/object",
    "https:///object",
]
```

Assert complete argv equality, eight unique headers/ranges, four-transfer parallel cap, HTTPS-only redirect options,
finite deadlines, exact rate limits, maximum sizes, no shell token, and no final `--next`.

- [ ] **Step 2: Run the guarded URL/profile RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_endpoint_contract_rejects_noncredential_free_https_object_urls \
  tests/unit/test_validation_study.py::test_workload_specs_expand_exact_short_streaming_and_eight_bursty_argv
```

Expected: failure because URL/profile construction is absent.

- [ ] **Step 3: Implement URL validation and all three `WorkloadSpec` values**

Use `urlsplit`, hostname/DNS validation consistent with the project, and the exact tuples in Locked Workloads. The
profile constructor validates its own transfer ranges, header basenames, timeout, protocol, redirect, range,
maximum-size, and URL tokens before returning immutable values.

- [ ] **Step 4: Run URL/profile GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_endpoint_contract_rejects_noncredential_free_https_object_urls \
  tests/unit/test_validation_study.py::test_workload_specs_expand_exact_short_streaming_and_eight_bursty_argv
```

Expected: pass.

- [ ] **Step 5: Write failing exhaustive config and realization tests**

Add exact nodes `test_base_config_contains_every_locked_value_and_only_profile_differences`, parameterized over all
three workload names; `test_checked_and_realized_configs_reload_to_exact_absolute_oracles`; and
`test_config_validation_rejects_every_protocol_change`, parameterized with:

```python
[
    "wrong-capture-image",
    "disabled-family",
    "changed-operator",
    "final-seed-reused",
    "wrong-mount",
    "wrong-profile-argv",
    "unexpected-config-difference",
    "existing-run-directory",
]
```

Compare full `model_dump(mode="python")` structures. The allowed difference audit names only URL, run directory,
profile argv, profile capture timeouts, profile multiscale widths, and the once-resolved capture image ID.

- [ ] **Step 6: Run the guarded config RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_base_config_contains_every_locked_value_and_only_profile_differences \
  tests/unit/test_validation_study.py::test_checked_and_realized_configs_reload_to_exact_absolute_oracles \
  tests/unit/test_validation_study.py::test_config_validation_rejects_every_protocol_change
```

Expected: failure because config construction/render/validation is absent.

- [ ] **Step 7: Implement base and realized config construction**

Implement `build_base_config`, `_config_with_run_directory`, `render_checked_base_config`,
`_render_realized_config`, and `validate_base_configs`. Construct every Pydantic section explicitly; do not start
from `examples/configs/minimal.toml` or depend on defaults. Render checked relative operands, reload through
`load_experiment`, require exact absolute equality, hash exact checked bytes, and reject existing run/config targets.

- [ ] **Step 8: Run config GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_base_config_contains_every_locked_value_and_only_profile_differences \
  tests/unit/test_validation_study.py::test_checked_and_realized_configs_reload_to_exact_absolute_oracles \
  tests/unit/test_validation_study.py::test_config_validation_rejects_every_protocol_change
```

Expected: pass.

- [ ] **Step 9: Write failing header scratch and response-evidence tests**

Use real temporary modes/inodes and multi-block header bytes in exact nodes
`test_scratch_files_are_exclusive_regular_0666_and_archives_are_sibling_0600`,
`test_range_header_parser_validates_redirect_chain_final_status_range_and_length`, and
`test_transfer_evidence_rejects_unsafe_or_inexact_headers`. Parameterize rejection with:

```python
[
    "symlink",
    "replacement-inode",
    "empty-header",
    "duplicate-status",
    "duplicate-content-range",
    "wrong-total",
    "range-ignored-200",
    "wrong-content-length",
    "credential-redirect",
    "http-redirect",
    "archive-exists",
]
```

Assert archive path is `examples/validation_study/.study-work/evidence/STUDY_ID/RUN_ID/FILENAME`, not beneath the production
run; header hash matches exact bytes; scratch is removed only after verified archive hash; and failure best-effort
archives ordinary bytes but preserves scratch/original error.

- [ ] **Step 10: Run the guarded header RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_scratch_files_are_exclusive_regular_0666_and_archives_are_sibling_0600 \
  tests/unit/test_validation_study.py::test_range_header_parser_validates_redirect_chain_final_status_range_and_length \
  tests/unit/test_validation_study.py::test_transfer_evidence_rejects_unsafe_or_inexact_headers
```

Expected: failure because scratch/archive/header functions are absent.

- [ ] **Step 11: Implement exact localized scratch and header evidence**

Use `lstat`, exclusive file creation, `chmod`, and stored `(path, inode)` values only for the exact profile header
names. Parse HTTP response blocks, case-insensitive singleton fields, redirect `Location` via `urljoin`, and final
status/range/length. Archive with exclusive creation and mode `0600`, re-read/hash, then unlink only the owned
scratch file. Do not generalize these checks into a filesystem or security layer.

- [ ] **Step 12: Run Task 2 GREEN and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_validation_study.py
uv run --locked ruff format --check scripts/run_validation_study.py tests/unit/test_validation_study.py
uv run --locked ruff check scripts/run_validation_study.py tests/unit/test_validation_study.py
uv run --locked pyright scripts/run_validation_study.py tests/unit/test_validation_study.py
git diff --check
```

- [ ] **Step 13: Commit, report, and queue Task 2 for the Task 3 review checkpoint**

```bash
git add scripts/run_validation_study.py tests/unit/test_validation_study.py
git commit -m "feat: realize Validation Study workloads"
```

Record exact profile/config/header evidence in `task-2-report.md` and save the Task 2 diff. Queue portability, exact
argv, allowed config differences, symlink/inode scope, archive ownership, and security/framework scope for the
consolidated independent Tasks 1–3 review after Task 3.

---

### Task 3: Strict run extraction, family champions, and natural variation

**Files:**

- Modify: `scripts/run_validation_study.py`
- Modify: `tests/unit/test_validation_study.py`
- Create: `tests/integration/test_validation_study_pipeline.py`
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-3-report.md`

**Interfaces:**

- Consumes: Task 1 schema/statistics, Task 2 config/header records, production run/checkpoint/model/comparison codecs,
  `quantize_generated_events`, `compare_traces`, and `evaluate_final`.
- Produces: strict primary/reproduction artifact extraction, all-family champion evidence, raw/published equality,
  trace summaries, symmetric natural variation, workload summaries, and the one required in-process integration
  test.

- [ ] **Step 1: Write failing champion and score-projection tests**

Build a terminal six-candidate checkpoint with two valid candidates tied inside one family. Add exact nodes
`test_family_champions_use_terminal_valid_candidates_stable_ids_and_selection_means` and
`test_winner_held_out_and_published_records_remain_distinct`.

Assert lexical family order; smaller `CandidateId` wins an equal-fitness tie; trials are exactly seeds 17/29;
selection component means and aggregate equal candidate fitness; winner equals checkpoint best and best-model
family/genes; held-out is seed 97 from its named authority; published is a separate seed-97 score projection.

- [ ] **Step 2: Run champion RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_family_champions_use_terminal_valid_candidates_stable_ids_and_selection_means \
  tests/unit/test_validation_study.py::test_winner_held_out_and_published_records_remain_distinct
```

Expected: failure because extraction helpers are absent.

- [ ] **Step 3: Implement candidate and score extraction**

Implement these exact helper signatures:

```text
_score_from_trial(trial: TrialResult) -> JsonObject
_score_from_comparison(result: ComparisonResult) -> JsonObject
_candidate_id(identifier: CandidateId) -> JsonObject
_canonical_genes(candidate: Candidate) -> list[int | float]
_family_champions(state: CheckpointState) -> tuple[JsonObject, JsonObject, JsonObject]
_winner(state: CheckpointState, best: BestModel) -> JsonObject
```

Choose a champion with `min(valid_family_candidates, key=lambda item: (-item.fitness, item.identifier))`. Reject a
missing/invalid family, wrong seeds, noncanonical genes, or mismatch between mean aggregate and fitness.

- [ ] **Step 4: Run champion GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_family_champions_use_terminal_valid_candidates_stable_ids_and_selection_means \
  tests/unit/test_validation_study.py::test_winner_held_out_and_published_records_remain_distinct
```

Expected: pass.

- [ ] **Step 5: Write failing unit and in-process extraction tests before the extraction implementation**

Add odd/even/tied-IAT trace fixtures and strict persisted artifact maps in exact nodes
`test_trace_summary_uses_canonical_events_and_multiscale_direction_totals`,
`test_primary_extraction_reloads_nine_artifacts_and_proves_raw_quantized_lineage`, and
`test_run_extraction_rejects_missing_malformed_inconsistent_or_reused_evidence`. Parameterize rejection with:

```python
[
    "missing-artifact",
    "tenth-run-entry",
    "reused-stage",
    "checkpoint-mismatch",
    "history-mismatch",
    "best-model-mismatch",
    "held-out-wrong-seed",
    "raw-trial-final-differ",
    "raw-score-differ",
    "quantized-events-differ",
    "similarity-lineage-differ",
    "cleanup-not-proven",
]
```

For trace summaries, assert packet/direction/byte totals, frame sample, IAT zero count, W, and each configured scale
against the role-specific `reference_totals` or `generated_totals` in strict multiscale diagnostics.

In `tests/integration/test_validation_study_pipeline.py`, create a temporary exhaustive Validation Study-like config and run
tree from the
checked capture fixture. Inject only the capture boundary so no Docker starts; use real preflight preparation,
`fit_experiment`, `generate_experiment`, `compare_experiment`, and the still-absent strict extractor:

```python
@pytest.mark.integration
def test_validation_study_extraction_uses_real_three_family_artifacts_fresh_seed_and_lineage(tmp_path: Path) -> None:
    result = run_experiment(experiment_path, dependencies=offline_dependencies)
    record = study.extract_primary_record(
        repository_root,
        run_spec,
        workload,
        result,
        1.25,
        transfer_responses,
    )
    assert tuple(item["family"] for item in record.family_champions) == (
        "markov_renewal",
        "mmpp",
        "poisson_empirical",
    )
    assert all(item["selection_seeds"] == (17, 29) for item in record.family_champions)
    held_out = cast(study.JsonObject, study._thaw_json(record.held_out))
    held_out_score = cast(study.JsonObject, held_out["score"])
    methods = cast(study.JsonObject, held_out_score["methods"])
    artifact_sha256 = cast(study.JsonObject, study._thaw_json(record.artifact_sha256))
    input_sha256 = result.comparison.input_sha256
    assert input_sha256 is not None
    assert held_out["seed"] == 97
    assert tuple(methods) == METHOD_ORDER
    assert artifact_sha256["capture.json"] == input_sha256["capture_json"]
    assert artifact_sha256["reference.pcapng"] == input_sha256["reference_pcapng"]
    assert artifact_sha256["generated.pcapng"] == input_sha256["generated_pcapng"]
```

Import `cast` from `typing`. Also require strict checkpoint/history/best-model/similarity reloading and exact nine
run names. The unit and integration paths must reach the same missing `extract_primary_record` behavior in RED;
do not add a fake extractor or defer integration until after the implementation.

- [ ] **Step 6: Run trace/extraction RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 \
  tests/unit/test_validation_study.py::test_trace_summary_uses_canonical_events_and_multiscale_direction_totals \
  tests/unit/test_validation_study.py::test_primary_extraction_reloads_nine_artifacts_and_proves_raw_quantized_lineage \
  tests/unit/test_validation_study.py::test_run_extraction_rejects_missing_malformed_inconsistent_or_reused_evidence \
  "$validation_study_extraction_node"
```

Expected: the named unit and real in-process integration cases fail because strict artifact extraction is absent.

- [ ] **Step 7: Implement one strict artifact loading/equality path for both failing test layers**

Implement the locked `_trace_summary` signature. Snapshot each artifact identity, run existing strict validation,
load and hash exact bytes, then require identities
unchanged. Rebuild `StrategyContext` from exact config/capture/reference bytes, parse the compatible terminal
checkpoint, require exact history projection and canonical best model, and parse/render the persisted comparison.
For primary runs require `RunResult` equality and all four reuse flags false. Treat successful `capture_published`
with `reused=false`, followed by returned `RunResult`, as proof that the capture-owned cleanup verifier succeeded.

Generate with stored fitted model, seed 97, W, trial limits, then final limits. Require both complete raw tuples
equal; compare the raw tuple and require aggregate, four methods, and diagnostics equal the authoritative held-out
`TrialResult`. Quantize, call `encode_pcapng(quantized, capture_metadata)`, reparse those bytes with the same
metadata, and require that tuple to equal parsed `generated.pcapng` and `RunResult.generation.events`. Bind the
reparsed published comparison to exact capture/reference/generated/settings hashes and require exact persisted
`ComparisonResult` equality.

- [ ] **Step 8: Run trace/extraction GREEN**

```bash
validation_study_extraction_node="tests/integration/test_validation_study_pipeline.py::"
validation_study_extraction_node+="test_validation_study_extraction_uses_real_three_family_artifacts_fresh_seed_and_lineage"
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 \
  tests/unit/test_validation_study.py::test_trace_summary_uses_canonical_events_and_multiscale_direction_totals \
  tests/unit/test_validation_study.py::test_primary_extraction_reloads_nine_artifacts_and_proves_raw_quantized_lineage \
  tests/unit/test_validation_study.py::test_run_extraction_rejects_missing_malformed_inconsistent_or_reused_evidence \
  "$validation_study_extraction_node"
```

Expected: every named unit and integration node passes without Docker or Internet after this one implementation.

- [ ] **Step 9: Write failing symmetric natural-variation and summary tests**

Use three unequal reference windows per workload and monkeypatch the module-local `compare_traces` name with a
comparison spy in exact nodes
`test_natural_variation_compares_each_pair_in_both_directions_and_averages_scores`,
`test_workload_summaries_recompute_runtime_family_score_variance_and_winner_counts`, and
`test_natural_variation_propagates_metric_precondition_failure`.

Assert call order `1->2,2->1,1->3,3->1,2->3,3->2`; each direction normalizes its own left reference and aligns/
crops the right to that W; pairs are ordered; descriptors and all requested score descriptions use the three
within-workload observations; no cross-workload summary is created.

- [ ] **Step 10: Run natural-variation RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_natural_variation_compares_each_pair_in_both_directions_and_averages_scores \
  tests/unit/test_validation_study.py::test_workload_summaries_recompute_runtime_family_score_variance_and_winner_counts \
  tests/unit/test_validation_study.py::test_natural_variation_propagates_metric_precondition_failure
```

Expected: failure because natural-variation/summary functions are absent.

- [ ] **Step 11: Implement natural variation and workload summaries**

Keep the normalized reference traces in an in-memory map during study extraction. Use only `normalize_reference`,
`align_generated`, and `compare_traces`. Build the exact three pair records and descriptor descriptions. Group
primary records by workload/repeat and derive runtime, each family's selection fitness/components, winner fitness,
held-out/published scores, reference descriptors, and exact family counts.

- [ ] **Step 12: Run natural-variation GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_natural_variation_compares_each_pair_in_both_directions_and_averages_scores \
  tests/unit/test_validation_study.py::test_workload_summaries_recompute_runtime_family_score_variance_and_winner_counts \
  tests/unit/test_validation_study.py::test_natural_variation_propagates_metric_precondition_failure
```

Expected: pass.

- [ ] **Step 13: Run Task 3 GREEN and exact explicit typing**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked ruff format --check scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked ruff check scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked pyright scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
git diff --check
```

- [ ] **Step 14: Commit, report, and obtain independent Task 3 review**

```bash
git add scripts/run_validation_study.py tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
git commit -m "feat: extract Validation Study evidence"
```

Report exact production boundaries and equality proofs in `task-3-report.md`. Give a fresh reviewer the Tasks 1–3
commits/reports/diffs, approved spec, and plan. Require schema/config plus scientific evidence separation, stable
ties, checkpoint compatibility, raw/quantized lineage, scale totals, symmetric windows, and no duplicated codec.
Fix every Critical/Important finding with guarded TDD, rerun affected gates, and commit before Task 4.

---

### Task 4: Prerequisite capability, image identity, and exact subprocess subcommand

**Files:**

- Modify: `scripts/run_validation_study.py`
- Modify: `tests/unit/test_validation_study.py`
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-4-report.md`

**Interfaces:**

- Consumes: Task 1 prerequisite codec, Task 2 URL/config/header functions, the exact target digest, Docker CLI,
  `scripts/run_bounded.sh`, existing Docker/Internet pytest markers, and the injected `CommandRunner`.
- Produces: `prerequisites` CLI behavior, exact image/capability/tool/JUnit evidence, three checked configs, and
  canonical `PrerequisiteResults` for Task 5.

- [ ] **Step 1: Write failing exact command and JUnit tests**

Add exact nodes `test_prerequisite_commands_are_exact_guarded_serial_argv_with_relative_projection` and
`test_junit_parser_requires_positive_all_passed_selection`. Parameterize the JUnit rejection node with:

```python
[
    b'<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
    b'<testsuite tests="2" failures="0" errors="0" skipped="1"/>',
    b'<testsuite tests="2" failures="1" errors="0" skipped="0"/>',
    b"not xml",
]
```

The Docker command is the exact 20-minute guard plus `pytest -vv -n 0 -m docker --junitxml PATH`; Internet is the
exact ten-minute guard plus `pytest -vv -n 0 -m internet --internet-url URL --junitxml PATH`. Checked argv keeps only
the JUnit path repository-relative; live argv resolves only that operand.

- [ ] **Step 2: Run command/JUnit RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_prerequisite_commands_are_exact_guarded_serial_argv_with_relative_projection \
  tests/unit/test_validation_study.py::test_junit_parser_requires_positive_all_passed_selection
```

Expected: failure because command construction/JUnit parsing is absent.

- [ ] **Step 3: Implement literal guarded argv and JUnit count parsing**

Implement `_docker_matrix_argv`, `_internet_smoke_argv`, `_project_command_argv`, `_live_argv`, and
`_parse_junit_counts`. Store exact stdout/stderr/JUnit bytes under
`examples/validation_study/.study-work/evidence/STUDY_ID/00-prerequisites/`, mode `0600`, and hash them. Call the injected
runner with `shell=False`, `check=False`, byte capture, repository cwd, and an outer timeout longer than each
guard's wall time plus kill grace: `1230.0` seconds for Docker and `630.0` seconds for Internet. A nonzero status or
nonpassing JUnit record fails immediately.

Use exact retained names `docker.stdout`, `docker.stderr`, `docker.xml`, `internet.stdout`, `internet.stderr`, and
`internet.xml`; capability retains `capability.headers`, `capability.stdout`, `capability.stderr`, and
`capability.cid` in that same directory.

- [ ] **Step 4: Run command/JUnit GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_prerequisite_commands_are_exact_guarded_serial_argv_with_relative_projection \
  tests/unit/test_validation_study.py::test_junit_parser_requires_positive_all_passed_selection
```

Expected: pass.

- [ ] **Step 5: Write failing image and capability happy-path test**

Use exact node `test_capability_records_digest_ids_default_user_range_canary_modes_and_cleanup` with a scripted
fake runner that asserts each argv and supplies pull/inspect/build/capability outputs. The fake capability call
mutates the precreated canary without replacing its inode and writes the CID file.

Assert order: clean URL/study/worktree validation, target pull/inspect, capture build/IID read, capability launch,
post-`--rm` absence, Docker matrix, Internet smoke, config rendering, prerequisite publication. Assert source hashes,
tool versions, exact target repo digest, exact capture ID in every config, canary/archive/CID modes, stdout/stderr
hashes, redirect count, object total, config hashes, command records, and no `--user` token.

- [ ] **Step 6: Run capability happy-path RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_capability_records_digest_ids_default_user_range_canary_modes_and_cleanup
```

Expected: failure because image/capability/prerequisite orchestration is absent.

- [ ] **Step 7: Implement image preparation and capability proof**

Use literal direct argv for:

```text
docker image pull TARGET_REFERENCE
docker image inspect TARGET_REFERENCE
docker build --pull=false --iidfile IID_ABS docker/capture
```

Require inspect JSON to contain exact ID, nonempty sorted repo digests including `TARGET_REFERENCE`, and exact
`Config.User`. Read the IID file as one digest-form SHA-256 value; do not assign a tag.

Build the capability argv exactly as the design: unique name/label/CID file, bridge network, identical bind source
and `/trafficlab-study` destination, pinned image, HTTPS-only curl, `0-0`, size one, header canary, exact four-line
write-out, and URL. Validate the name and CID path absent before launch. Use a 45-second subprocess timeout around
curl's 30-second limit. Parse every response/redirect and the exact `status,size,url,redirects` fields. Require
same-inode nonempty canary, final 206/range/length/total, and exact final URL.

```python
capability_argv = (
    "docker",
    "run",
    "--rm",
    "--name",
    f"trafficlab-validation-study-capability-{study_id}",
    "--label",
    f"org.trafficlab.validation-study.study={study_id}",
    "--cidfile",
    str(capability_cid),
    "--network",
    "bridge",
    "--mount",
    f"type=bind,src={mount_absolute},dst=/trafficlab-study",
    TARGET_REFERENCE,
    "--fail",
    "--silent",
    "--show-error",
    "--location",
    "--max-redirs",
    "3",
    "--proto",
    "=https",
    "--proto-redir",
    "=https",
    "--http1.1",
    "--connect-timeout",
    "15",
    "--max-time",
    "30",
    "--range",
    "0-0",
    "--max-filesize",
    "1",
    "--dump-header",
    "/trafficlab-study/.capability.headers",
    "--output",
    "/dev/null",
    "--write-out",
    "status=%{response_code}\nsize=%{size_download}\nurl=%{url_effective}\nredirects=%{num_redirects}\n",
    "--url",
    url,
)
```

The checked capability `argv` changes only `src=MOUNT_ABS` inside the Docker mount token to
`src=examples/validation_study/.study-work/mount/STUDY_ID` and stores the CID/evidence operand repository-relative. The live
builder resolves only those named path operands and rejects any other token difference.

After normal exit require both CID and name absent. On timeout/abnormal exit, read the exclusively created CID,
inspect that exact ID, require the study label and exact name, force-remove only that ID, verify ID/name absence, and
retain the original failure. If ownership cannot be proved, do not remove it and report the surviving resource.

- [ ] **Step 8: Run capability happy-path GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_capability_records_digest_ids_default_user_range_canary_modes_and_cleanup
```

Expected: pass.

- [ ] **Step 9: Write failing capability/prerequisite failure tests**

Add exact nodes `test_prerequisites_stop_at_first_failure_preserve_primary_and_publish_no_valid_json` and
`test_prerequisite_cli_requires_exact_subcommand_arguments_and_reports_errors`. Parameterize the former with:

```python
[
    "dirty-tree",
    "wrong-python",
    "target-digest-absent",
    "capture-iid-tag",
    "preexisting-name",
    "preexisting-cid",
    "capability-timeout-owned",
    "capability-timeout-unowned",
    "canary-not-written",
    "canary-replaced",
    "wrong-write-out",
    "range-ignored",
    "oversize-object",
    "docker-matrix-failed",
    "internet-skipped",
    "config-publication-failed",
]
```

Assert no later command after a failure, no valid prerequisite JSON, no runnable config before both test scopes
pass, preserved evidence, owned-only cleanup, and actionable stderr/exit status.

- [ ] **Step 10: Run prerequisite failure RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py::test_prerequisites_stop_at_first_failure_preserve_primary_and_publish_no_valid_json \
  tests/unit/test_validation_study.py::test_prerequisite_cli_requires_exact_subcommand_arguments_and_reports_errors
```

Expected: failure because failure translation and CLI dispatch are incomplete.

- [ ] **Step 11: Implement `run_prerequisites`, parser, and CLI dispatch**

Discover the root from the script by default; tests inject a temporary root. Validate the exact clean 40-character
commit before external mutation. Record Python `3.12.3`, Trafficlab version, platform, Docker Engine, and Compose
versions. After capability, run Docker matrix then Internet smoke, validate their JUnit, render all three configs,
reopen/validate them, compute hashes, then atomically publish `examples/validation_study/prerequisites.json`. Never publish a
success object early.

- [ ] **Step 12: Run Task 4 GREEN and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_validation_study.py
uv run --locked ruff format --check scripts/run_validation_study.py tests/unit/test_validation_study.py
uv run --locked ruff check scripts/run_validation_study.py tests/unit/test_validation_study.py
uv run --locked pyright scripts/run_validation_study.py tests/unit/test_validation_study.py
git diff --check
```

- [ ] **Step 13: Commit, report, and queue Task 4 for the Task 5 review checkpoint**

```bash
git add scripts/run_validation_study.py tests/unit/test_validation_study.py
git commit -m "feat: validate Validation Study prerequisites"
```

Report all exact argv, timeouts, ownership proofs, image identities, and publication ordering; save the Task 4 diff.
Queue shell avoidance, capability timeout cleanup, immutable image evidence, JUnit truthfulness, config publication
order, and framework scope for the consolidated independent Tasks 4–5 review after Task 5.

---

### Task 5: Serial primary study and fresh installed-CLI reproduction

**Files:**

- Modify: `scripts/run_validation_study.py`
- Modify: `tests/unit/test_validation_study.py`
- Modify: `tests/integration/test_validation_study_pipeline.py` only if an orchestration boundary needs integration
  assertion
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-5-report.md`

**Interfaces:**

- Consumes: Tasks 1–4, `run_experiment()`, exact transfer evidence, strict extraction, statistics, and the installed
  `trafficlab run` CLI.
- Produces: `study` CLI behavior, exactly nine fresh serial primary records, symmetric variation/summaries, one
  fresh guarded tenth reproduction, and atomic canonical `results.json`.

- [ ] **Step 1: Write failing primary-order, timing, and failure-restart tests**

Inject the public run callable and timer sequence; monkeypatch the module-local scratch/archive/extraction helpers
for deterministic call recording. Add exact nodes
`test_study_runs_nine_absent_primaries_serially_in_balanced_order_and_times_only_run_call`,
`test_primary_failure_stops_preserves_evidence_and_publishes_no_results` parameterized at positions 1, 5, and 9,
`test_study_rejects_incompatible_prerequisites_existing_targets_and_any_reuse`, and
`test_study_validates_variation_and_summaries_before_any_reproduction_runner_call`.

Assert validation occurs before the first run, exact config/run/evidence paths, absent destinations, scratch before
each call, archive/extract after each call, no overlap, timer immediately around `run(experiment_path)`, and failure
stderr names workload/repeat/position/raw path/new-ID correction. Failed attempts stay ignored and no partial
official results appear. In the ordering regression, return nine complete primary records, monkeypatch
`natural_variation` to raise its exact metric-precondition `TrafficlabError`, and make the injected runner record
every argv. Assert no recorded command contains the `trafficlab run` reproduction suffix, no reproduction
config/header directory exists, and no `results.json` exists; live identity calls may still precede primary work.
Repeat with an invalid summary result and the same zero-reproduction-call assertions.

- [ ] **Step 2: Run primary orchestration RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  "tests/unit/test_validation_study.py::"\
"test_study_runs_nine_absent_primaries_serially_in_balanced_order_and_times_only_run_call" \
  tests/unit/test_validation_study.py::test_primary_failure_stops_preserves_evidence_and_publishes_no_results \
  tests/unit/test_validation_study.py::test_study_rejects_incompatible_prerequisites_existing_targets_and_any_reuse \
  "tests/unit/test_validation_study.py::"\
"test_study_validates_variation_and_summaries_before_any_reproduction_runner_call"
```

Expected: failure because study validation/spec derivation/serial loop is absent.

- [ ] **Step 3: Implement prerequisite/live validation and the primary loop**

Require exact study ID/URL/commit/tool/image/capability/config hashes and commands against live state. Permit only
the expected generated Validation Study checked paths plus ignored raw evidence to differ from the prerequisite clean commit.
Do not repeat capability. Precompute all nine `StudyRunSpec` values and require every run/config/evidence target
absent before starting.

For each spec, derive only run directory from its strict base config, render/reload its ignored realized config,
prepare exact scratch files, time the cached-image full-lifecycle `run_experiment` call, archive headers, strictly
extract the record, and retain the reference trace in memory. On failure, best-effort archive ordinary scratch bytes
without replacing the primary error and stop.

Immediately after the ninth primary extraction, and before deriving or creating any reproduction path, call
`natural_variation(records, traces, settings)` and `workload_summaries(records)`. Validate the exact three workload
records through the same strict result-leaf validators used by `parse_study_results`. A comparison precondition or
summary validation failure stops the study with the primary error, creates no reproduction input or evidence path,
calls no reproduction subprocess, and publishes no result.

- [ ] **Step 4: Run primary orchestration GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  "tests/unit/test_validation_study.py::"\
"test_study_runs_nine_absent_primaries_serially_in_balanced_order_and_times_only_run_call" \
  tests/unit/test_validation_study.py::test_primary_failure_stops_preserves_evidence_and_publishes_no_results \
  tests/unit/test_validation_study.py::test_study_rejects_incompatible_prerequisites_existing_targets_and_any_reuse \
  "tests/unit/test_validation_study.py::"\
"test_study_validates_variation_and_summaries_before_any_reproduction_runner_call"
```

Expected: pass.

- [ ] **Step 5: Write failing tenth-config and exact guard tests**

Add exact nodes
`test_reproduction_changes_only_run_directory_seeds_nothing_and_invokes_exact_nonnested_guard`,
`test_cli_reproduction_reconstructs_fresh_held_out_lineage_and_honest_source_deltas`, and
`test_reproduction_rejects_nonfresh_or_inconsistent_evidence`. Parameterize rejection with:

```python
[
    "source-not-streaming-r2",
    "extra-config-change",
    "seeded-artifact",
    "wrong-cli-suffix",
    "nested-guard",
    "nonzero-status",
    "reused-log",
    "winner-best-model-mismatch",
    "evaluate-final-count",
    "unbound-published-comparison",
]
```

Assert source is preselected streaming repeat 2, config path/run/evidence paths exact, and the run-directory path is
strictly absent; reject an existing empty directory as well as any populated path. Require structural equality
except `run.directory`, one streaming header, stdout/stderr hashes, elapsed positive, and exact guard argv. The
checked `config_path` command element is the validated repository-relative POSIX record path and the live child runs
from repository root with that same relative token:

```python
command = ("uv", "run", "--locked", "trafficlab", "run", config_path_record)
guard = (
    "scripts/run_bounded.sh",
    "--memory-high",
    "2G",
    "--memory-max",
    "3G",
    "--swap-max",
    "512M",
    "--wall-time",
    "20m",
    "--kill-after",
    "10s",
    "--",
    *command,
)
```

- [ ] **Step 6: Run reproduction RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  "tests/unit/test_validation_study.py::"\
"test_reproduction_changes_only_run_directory_seeds_nothing_and_invokes_exact_nonnested_guard" \
  tests/unit/test_validation_study.py::test_cli_reproduction_reconstructs_fresh_held_out_lineage_and_honest_source_deltas \
  tests/unit/test_validation_study.py::test_reproduction_rejects_nonfresh_or_inconsistent_evidence
```

Expected: failure because reproduction derivation/runner/reconstruction is absent.

- [ ] **Step 7: Implement fresh CLI reproduction and direct read-only reconstruction**

Copy the saved source effective config object, change only the new absolute run directory, render it outside that
run tree, reload, and require all other fields structurally equal. Require the run-directory path not to exist;
never accept or reuse an existing empty directory. Validate `config_path_record` with `_repository_relative_path`,
prove its resolved path remains beneath repository root, pass its normalized relative string unchanged in live argv,
and keep the subprocess cwd at repository root.

Prepare the streaming scratch; time the injected runner around the exact guard with `shell=False`; archive output
as `guard.stdout` and `guard.stderr` in the reproduction evidence directory with mode `0600`; archive the header;
use outer subprocess timeout `1230.0` seconds; reject nonzero status.

Strictly load effective config, capture bytes/metadata/reference, checkpoint, best model, generated PCAPNG, and
similarity. Build `make_strategy_context()` with exact snapshot/reference/capture hashes, call
`validate_evaluation_context()`, select checkpoint best, require best-model family/genes, and call
`evaluate_final(candidate, validated_context, 97)` exactly once. Call the registered winner with its loaded fitted
model, seed `97`, stored W, and first `config.generation.trial` then `config.generation.final`; require both results
complete and their entire raw event tuples equal. Compare the trial-limit tuple with `compare_traces()` and require
its aggregate, all four components, and diagnostics equal the sole direct `evaluate_final()` trial.

Quantize raw events, render with `encode_pcapng(quantized, capture_metadata)`, reparse with
`parse_pcapng_bytes(rendered, capture_metadata, source=generated_path)`, and require parsed generated equality. Set
`reparsed_generated = align_generated(parsed, W)`; build exactly:

```python
input_sha256 = {
    "capture_json": sha256_bytes(capture_json_bytes),
    "reference_pcapng": sha256_bytes(reference_pcapng_bytes),
    "generated_pcapng": sha256_bytes(generated_pcapng_bytes),
    "similarity_settings": similarity_settings_sha256(config.similarity),
}
published = compare_traces(reference, reparsed_generated, W, config.similarity).with_input_sha256(input_sha256)
```

Require exact equality to persisted `similarity.json` and artifact hashes. Compare source/new references
symmetrically; record observed winner equality and exact selection/held-out/published deltas without imposing
scientific equality.

Require the reproduction `run.log` to contain fresh `capture_published` with `reused=false`,
`best_model_published`, `generated_pcapng_published`, `comparison_succeeded` with `reused=false`, and one terminal
`run_completed`; reject any reuse event or pre-existing stage artifact.

- [ ] **Step 8: Run reproduction GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  "tests/unit/test_validation_study.py::"\
"test_reproduction_changes_only_run_directory_seeds_nothing_and_invokes_exact_nonnested_guard" \
  tests/unit/test_validation_study.py::test_cli_reproduction_reconstructs_fresh_held_out_lineage_and_honest_source_deltas \
  tests/unit/test_validation_study.py::test_reproduction_rejects_nonfresh_or_inconsistent_evidence
```

Expected: pass.

- [ ] **Step 9: Write failing complete study/result-publication and CLI tests**

Add exact nodes `test_study_builds_variation_summaries_reproduction_and_publishes_one_canonical_result`,
`test_study_cli_requires_exact_url_id_and_prerequisite_path_and_never_wraps_itself`, and
`test_local_audit_revalidates_report_checkpoint_artifacts_and_lineage_without_external_calls`.

Assert results publish only after nine records, three variation records, three summaries, and reproduction validate;
reparse/render bytes exactly; no report/raw artifact is written by the JSON publisher; CLI supports only the two
approved subcommands and calls the correct injected boundary. Build a temporary seven-section report plus ten exact
run trees for the audit node; prove it strictly reparses JSON/configs/checkpoints/artifacts, rehashes all retained
files, reconstructs raw/quantized/published lineage, recomputes directional variation/summaries/deltas, and rejects
a report missing its study ID, commit, image IDs, required headings, or any of the ten run IDs. Monkeypatch external
subprocess and network entry points to fail so the audit proves it is local and read-only.

- [ ] **Step 10: Run complete study RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  "tests/unit/test_validation_study.py::"\
"test_study_builds_variation_summaries_reproduction_and_publishes_one_canonical_result" \
  tests/unit/test_validation_study.py::test_study_cli_requires_exact_url_id_and_prerequisite_path_and_never_wraps_itself \
  "tests/unit/test_validation_study.py::"\
"test_local_audit_revalidates_report_checkpoint_artifacts_and_lineage_without_external_calls"
```

Expected: failure because final `StudyResults` assembly/publication/CLI route is absent.

- [ ] **Step 11: Implement complete study assembly and atomic publication**

Use the already validated pre-reproduction natural-variation and workload-summary tuples; never recompute them after
the reproduction. Construct environment/protocol from validated prerequisites and current UTC; build
`StudyResults`; render, atomic publish, strict parse, and require byte equality. Implement
`audit_published_study()` as a read-only local composition of the existing strict codecs/extraction functions. It
confines all input and nested record paths beneath the resolved repository root and requires `REPORT_HEADINGS` plus
the study ID, commit, target/capture image IDs, and ten run IDs. It does not parse prose numbers or contact
Docker/Internet. The `study` support command itself remains unwrapped. It owns only individual guarded reproduction
and the exact guarded prerequisite tests; no nested systemd scope occurs.

- [ ] **Step 12: Run Task 5 GREEN and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked ruff format --check scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked ruff check scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked pyright scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
git diff --check
```

- [ ] **Step 13: Commit, report, and obtain independent Task 5 review**

```bash
git add scripts/run_validation_study.py tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
git commit -m "feat: orchestrate validation study"
```

Report exact primary/reproduction call ordering, timing boundaries, failure retention, and lineage reconstruction.
Give a fresh reviewer the Tasks 4–5 commits/reports/diffs, approved spec, and plan. Require capability/image evidence,
seriality, absent targets, pre-reproduction summaries, no stage reuse, guard nesting, direct `evaluate_final`, local
audit behavior, result publication, and honest deltas. Fix every Critical/Important finding and commit before Task 6.

---

### Task 6: Operator documentation, ignored raw evidence, and safe local/Docker gates

**Files:**

- Modify: `.gitignore`
- Create: `examples/validation_study/README.md`
- Modify after the dedicated Docker matrix passes: `architecture/ROADMAP.md`
- Modify for reviewed local fixes only: `scripts/run_validation_study.py`
- Modify for reviewed local fixes only: `tests/unit/test_validation_study.py`
- Modify for reviewed local fixes only: `tests/integration/test_validation_study_pipeline.py`
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-6-report.md`

**Interfaces:**

- Consumes: complete deterministic implementation from Tasks 1–5, existing deterministic fixtures, process guard,
  and the available controlled Docker suite.
- Produces: copyable operator instructions, exact ignore boundary, passing local gates, dedicated Phase 3 Docker
  evidence on this host, seven accurately checked Phase 3 Docker-backed test boxes, and a clean reviewed commit ready
  for external evidence. Phase 3 remains `Current`; its Internet box and Done-when condition remain unchecked.

- [ ] **Step 1: Add the exact ignore and operator instructions with `apply_patch`**

Add only:

```gitignore
examples/validation_study/.study-work/
```

`README.md` must state the 4–16 MiB credential-free HTTPS range-object contract, target digest, direct commands,
`STUDY_ID=validation-study-20260813` example, failure/new-ID rule, checked versus ignored paths, absence of raw checked PCAPs,
and manual removal only of these exact audit trees after acceptance:

```bash
uv run --locked python scripts/run_validation_study.py \
  prerequisites --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID"
uv run --locked python scripts/run_validation_study.py \
  study --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID" \
  --prerequisites examples/validation_study/prerequisites.json
```

Also document the exact guarded Docker, Internet, focused, fast, coverage, and explicit Pyright commands from this
plan. Explain that saved-run reproduction is automatically the fresh tenth run, not byte-identical replay.

- [ ] **Step 2: Verify ignore scope and docs statically**

```bash
git check-ignore -v examples/validation_study/.study-work/probe
test -z "$(git check-ignore examples/validation_study/README.md)"
test -z "$(git check-ignore examples/validation_study/prerequisites.json)"
test -z "$(git check-ignore examples/validation_study/results.json)"
git diff --check
```

- [ ] **Step 3: Run locked sync, lock, fixture, and process-containment checks serially**

```bash
uv sync --locked --all-groups
uv lock --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_phase2_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_model_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_fit_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

- [ ] **Step 4: Run focused Validation Study and explicit static/type gates**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pyright scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
git diff --check
```

- [ ] **Step 5: Run the exact fast gate once, with no overlap**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

- [ ] **Step 6: Run the exact branch-coverage gate once, after fast completes**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Require package branch-aware coverage at least 90%. The script remains directly covered by its tests even though it
is intentionally outside `--cov=trafficlab`.

- [ ] **Step 7: Run the dedicated Phase 3 Docker matrix serially as safe local evidence**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
```

Require actual selected tests, zero skips/failures, and no labelled Docker residue. Diagnose and fix Classes 1–4
without asking for routine confirmation. Do not claim the Internet smoke or Validation Study from this command.

- [ ] **Step 8: Map the passing Docker matrix to exactly seven Phase 3 test boxes**

Only after Step 7 passes, use `apply_patch` on `architecture/ROADMAP.md` to check these exact seven existing boxes,
with this evidence mapping:

1. controlled TCP/UDP capture and Phase 2 address/protocol/count/direction assertions:
   `test_full_preflight_and_capture_observe_controlled_tcp_udp_and_broadcast`;
2. outbound, inbound unicast, and inbound broadcast source-MAC classification: the packet-level assertions in that
   same controlled-traffic test;
3. exact natural nonzero status, diagnostic-only capture, and no reusable pair:
   `test_natural_nonzero_status_is_exact_and_only_diagnostic_capture_remains`;
4. normal/timed-out background children, readiness failure, interruption, bounded flush, malformed output, and
   automatic per-test resource inspection: `test_background_child_is_closed_when_direct_target_exits`,
   `test_workload_timeout_kills_target_and_any_child`, `test_readiness_failure_never_starts_target_and_cleans_project`,
   `test_interruption_kills_target_flushes_once_and_returns_interruption_primary`,
   `test_capture_ignoring_sigint_reaches_flush_timeout_and_rejects_output`, and
   `test_malformed_output_is_rejected_after_bounded_flush`;
5. next-action target kill, under-five-second stop, and natural-versus-induced status:
   `test_capture_early_exit_kills_long_target_next_and_within_five_seconds`,
   `test_natural_nonzero_status_is_exact_and_only_diagnostic_capture_remains`,
   `test_workload_timeout_kills_target_and_any_child`,
   `test_interruption_kills_target_flushes_once_and_returns_interruption_primary`,
   `test_natural_target_failure_remains_primary_through_flush_validation_and_cleanup`,
   `test_interruption_remains_primary_through_induced_exit_flush_and_cleanup`,
   `test_capture_failure_remains_primary_through_induced_exit_and_cleanup`,
   `test_stage_timeout_remains_primary_through_induced_exit_total_timeout_and_cleanup`, and
   `test_total_timeout_remains_primary_through_induced_exit_and_cleanup`;
6. SIGINT-ignoring capture, live-versus-stopped signaling, zero-budget cleanup, and hanging cleanup:
   `test_capture_ignoring_sigint_reaches_flush_timeout_and_rejects_output`,
   `test_capture_early_exit_kills_long_target_next_and_within_five_seconds`,
   `test_expired_flush_stage_kills_capture_without_sending_a_late_signal`,
   `test_zero_budget_makes_no_docker_call_and_preserves_last_known_inventory`, and
   `test_hanging_cleanup_terminates_then_kills_local_cli_without_later_docker_query` from the passing local gates;
7. no-shell target and direct launch without wrapper/PID/Compose exec:
   `test_direct_no_shell_target_exits_zero_without_wrapper_or_exec` and
   `test_endpoint_overlay_is_test_only_and_production_remains_two_services`.

Replace the Phase 3 evidence-pending paragraph with a dated note recording the exact Docker matrix command, selected
pass count, zero skips/failures, and clean tracker inspection. State explicitly that the opt-in Internet smoke has
not run, the Internet test box and Done-when remain unchecked, and Phase 3 remains `Current`. Do not change the phase
heading or mark any Validation Study box.

- [ ] **Step 9: Commit documentation/Docker evidence, report, and obtain independent Task 6 review**

```bash
git add .gitignore examples/validation_study/README.md architecture/ROADMAP.md
git diff --cached --name-only
git commit -m "docs: record Phase 3 Docker evidence"
git status --short
```

If gate-driven fixes exist, commit them in narrow TDD commits before the docs commit. Record every output, coverage,
Docker test count, resource residue query, and the state of `TRAFFICLAB_INTERNET_URL` in `task-6-report.md`. Give a
fresh reviewer the full Task 1–6 range and require architecture compliance plus code-quality verdicts. Fix every
Critical/Important finding, rerun affected guarded gates, and end on a clean reviewed commit. Review must compare
the seven checked boxes to the named test bodies and reject any Internet/Done-when/phase-status overclaim.

After review fixes are committed, bind the exact clean Task 6 Roadmap state that Task 7 recovery must preserve:

```bash
TASK6_BASE="$(git rev-parse HEAD)"
git diff --exit-code "$TASK6_BASE" -- architecture/ROADMAP.md
git show "$TASK6_BASE:architecture/ROADMAP.md" | sha256sum
git status --short
```

Record `TASK6_BASE` and the Roadmap SHA-256 in `task-6-report.md` and `progress.md`. The recorded blob must have the
seven named Docker-backed test boxes checked, the Internet test unchecked, the Task 6 Docker evidence note saying
that Internet/Done-when remain unsatisfied, and Phase 3 still marked `Current`.

- [ ] **Step 10: Apply the Class 5 boundary exactly**

If `TRAFFICLAB_INTERNET_URL` is empty or unset, record the precise endpoint contract and remaining Task 7/8 steps in
the ledger and report the Class 5 blocker. All safe local implementation is complete; leave Validation Study configs,
`prerequisites.json`, `results.json`, `REPORT.md`, the Phase 3 Internet/Done-when boxes, and all Validation Study Roadmap boxes
absent/unchecked. Keep the seven proved Docker-backed Phase 3 boxes checked. Once the operator supplies a valid URL,
resume at Task 7 without repeating completed work except the narrow verification required by the persistence
instructions.

---

### Task 7: Execute ten real experiments and publish evidence/report/Roadmap

**Files:**

- Create from successful prerequisite output: `examples/validation_study/configs/short.toml`
- Create from successful prerequisite output: `examples/validation_study/configs/streaming.toml`
- Create from successful prerequisite output: `examples/validation_study/configs/bursty.toml`
- Create from successful prerequisite output: `examples/validation_study/prerequisites.json`
- Create from successful study output: `examples/validation_study/results.json`
- Create with evidence-backed `apply_patch`: `examples/validation_study/REPORT.md`
- Modify after exact evidence: `architecture/ROADMAP.md`
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-7-report.md`

**Interfaces:**

- Consumes: one operator-supplied conforming URL, clean reviewed Task 6 commit, exact prerequisite/study subcommands,
  ignored audit trees, and all validators.
- Produces: Phase 3 Internet evidence, three real workload configs, nine balanced fresh primary runs, one fresh CLI
  reproduction, canonical checked evidence, concise scientific interpretation, truthful Roadmap closure, and no
  checked raw capture/run data. Task 6 already owns the Phase 3 Docker evidence and boxes.

- [ ] **Step 1: Resume from the reviewed clean commit and validate external inputs**

```bash
test -n "${TRAFFICLAB_INTERNET_URL:-}"
STUDY_ID=validation-study-20260813
TASK6_BASE="$(git rev-parse HEAD)"
git status --short
git diff --exit-code "$TASK6_BASE" -- architecture/ROADMAP.md
git show "$TASK6_BASE:architecture/ROADMAP.md" | sha256sum
uv run --locked python --version
```

Require empty status, Python 3.12.3, and equality between `TASK6_BASE`/the Roadmap SHA-256 and the values recorded by
Task 6. Keep `TASK6_BASE` unchanged for this attempt. If the fixed ID already names retained failed evidence, choose
the next valid new ID before running anything; never reuse a failed protocol's directories.

- [ ] **Step 2: Run the support prerequisite command directly**

```bash
uv run --locked python scripts/run_validation_study.py \
  prerequisites --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID"
```

This direct support command internally runs, in order, exact digest/image/capability operations and these two
individual nonnested guarded selections with JUnit paths:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker \
  --junitxml "examples/validation_study/.study-work/evidence/$STUDY_ID/00-prerequisites/docker.xml"
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet \
  --internet-url "$TRAFFICLAB_INTERNET_URL" \
  --junitxml "examples/validation_study/.study-work/evidence/$STUDY_ID/00-prerequisites/internet.xml"
```

Do not manually substitute collection, a skip, an unavailable-environment result, or Task 6 Docker output for this
exact prerequisite record. If endpoint capability fails, preserve evidence and obtain a new conforming operator URL;
then restart prerequisites with a new study ID.

- [ ] **Step 3: Inspect prerequisite evidence without committing it**

Use the script parser through a bounded read-only command and require the three config hashes/files, capability
archive, exact image IDs, CID cleanup, two passing JUnit records, and repository-relative checked paths. Confirm the
realized effective configs have one shared mount and capture image ID and that their primary run directories remain
absent. Do not run `trafficlab preflight --config-only` on these primary configs because it would create their run
directories. Do not commit here; the study must see the prerequisite-bound commit.

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python -c \
  'from pathlib import Path; import scripts.run_validation_study as study; root=Path.cwd(); '\
'path=Path("examples/validation_study/prerequisites.json"); content=path.read_bytes(); '\
'value=study.parse_prerequisite_results(content, repository_root=root); '\
'assert study.render_prerequisite_results(value)==content; study.validate_base_configs(root, value)'
```

Require exit zero and unchanged `git status --short`; then perform the named ignored-file hash and CID-absence
checks recorded by the parser value. This command does not contact Docker or the URL.

- [ ] **Step 4: Run the nine-primary plus tenth-reproduction study directly**

```bash
uv run --locked python scripts/run_validation_study.py \
  study --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID" \
  --prerequisites examples/validation_study/prerequisites.json
```

Require nine serial successful primary records in exact balanced order, false reuse, sibling header evidence,
strict nine-entry run trees, three family champions per run, fresh seed-97 held-out records, raw equality, symmetric
variation, descriptions, then a fresh installed-CLI reproduction under the exact 20-minute guard. A failure retains
its evidence, publishes no official results, and requires a complete restart under a new ID.

- [ ] **Step 5: On any protocol/evidence failure, retain exact evidence and restore a reviewed base before retry**

Set `FAILED_ID` to the exact attempted `STUDY_ID` before changing `STUDY_ID`; the first bounded command below is the
required validation of that value. Apply this step to prerequisite, primary, reproduction, or local evidence-audit
failure; an evidence-faithful report-only wording correction does not invalidate otherwise unchanged JSON/runs.

First validate the failed ID and hash, without following symlinks, every retained regular file beneath only its three
ignored run, evidence, and mount roots. Save the bounded command output in `task-7-report.md`; do not remove or move
any root:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python -c \
  'from hashlib import sha256; from pathlib import Path; import sys; '\
'import scripts.run_validation_study as study; study_id=study.validate_study_id(sys.argv[1]); '\
'roots=(Path("runs/validation_study")/study_id, '\
'Path("examples/validation_study/.study-work/evidence")/study_id, '\
'Path("examples/validation_study/.study-work/mount")/study_id); '\
'paths=sorted(p for root in roots if root.exists() for p in root.rglob("*") '\
'if p.is_file() and not p.is_symlink()); '\
'[print(f"{sha256(p.read_bytes()).hexdigest()}  {p.as_posix()}") for p in paths]' \
  "$FAILED_ID"
```

Classify the checked publication paths before changing them. First reject a regular or dangling source symlink and
any other non-regular candidate independently of Git tracking, then print the exact tracked subset:

```bash
for candidate in \
  examples/validation_study/prerequisites.json examples/validation_study/results.json examples/validation_study/REPORT.md \
  examples/validation_study/configs/short.toml examples/validation_study/configs/streaming.toml \
  examples/validation_study/configs/bursty.toml
do
  if [ -e "$candidate" ] || [ -L "$candidate" ]; then
    if [ -L "$candidate" ]; then
      printf 'publication candidate must not be a symlink: %s\n' "$candidate" >&2
      exit 1
    fi
    if [ ! -f "$candidate" ]; then
      printf 'publication candidate is not a regular file: %s\n' "$candidate" >&2
      exit 1
    fi
  fi
done
git ls-files -- \
  examples/validation_study/prerequisites.json examples/validation_study/results.json examples/validation_study/REPORT.md \
  examples/validation_study/configs/short.toml examples/validation_study/configs/streaming.toml \
  examples/validation_study/configs/bursty.toml
```

Empty `git ls-files` output selects the uncommitted branch. Any printed candidate selects only the corrective-commit
branch below. For the uncommitted branch, first require that `HEAD` is still the recorded reviewed Task 6 commit.
Inspect the Roadmap diff and stop if it includes anything outside Task 7's Phase 3 status/Internet/evidence edits or
Validation Study status/checkbox/evidence edits:

```bash
test "$(git rev-parse HEAD)" = "$TASK6_BASE"
git diff -- architecture/ROADMAP.md
```

The allowed Roadmap diff is exact:

1. Phase 3's heading may have lost `Current`; restore the exact Task 6 heading.
2. Keep checked the seven Docker-backed test boxes mapped in Task 6 Step 8. Do not patch those seven lines.
3. Restore the Phase 3 Internet-smoke box to unchecked.
4. Replace any Task 7 Phase 3 completion/evidence text with the exact reviewed Task 6 evidence note, including its
   measured Docker pass count and its statements that Internet/Done-when remain unsatisfied.
5. Restore the exact Task 6 Validation Study heading; leave all five deliverables and all three tests unchecked.
6. Remove every Task 7 Validation Study verification/evidence paragraph and leave the existing Done-when prose unclaimed.

Print the authoritative before-bytes, then use `apply_patch` to make only those six Roadmap reversions. Copy the
replacement lines byte-for-byte from these two reviewed spans; do not use `git checkout`, `git restore`, shell
redirection, or a generated whole-file rewrite:

```bash
git show "$TASK6_BASE:architecture/ROADMAP.md" | \
  sed -n '/^## Phase 3 /,/^## Phase 4 /p'
git show "$TASK6_BASE:architecture/ROADMAP.md" | \
  sed -n '/^## Validation Study /,/^## Later,/p'
```

After the `apply_patch`, exact byte equality is mandatory. This check both protects Task 6's seven proved boxes and
catches a missed Task 7 status, Done-when evidence claim, or Validation Study evidence line:

```bash
git diff --exit-code -- architecture/ROADMAP.md
git diff --exit-code "$TASK6_BASE" -- architecture/ROADMAP.md
```

Now require a fresh ignored archive leaf and move only the six named regular publication candidates. Every source,
archive parent, archive leaf, and archive file check treats either existence or a symlink as occupancy. A dangling
symlink therefore cannot pass an absence check. Never use `rm`, a glob, a recursive move, or move a run/evidence
root:

```bash
ARCHIVE_WORK="examples/validation_study/.study-work"
ARCHIVE_PARENT="$ARCHIVE_WORK/failed-publication"
FAILED_PUBLICATION="$ARCHIVE_PARENT/$FAILED_ID"
if [ -e "$ARCHIVE_WORK" ] || [ -L "$ARCHIVE_WORK" ]; then
  if [ -L "$ARCHIVE_WORK" ] || [ ! -d "$ARCHIVE_WORK" ]; then
    printf 'archive work root must be a real directory: %s\n' "$ARCHIVE_WORK" >&2
    exit 1
  fi
else
  printf 'archive work root is absent: %s\n' "$ARCHIVE_WORK" >&2
  exit 1
fi
if [ -e "$ARCHIVE_PARENT" ] || [ -L "$ARCHIVE_PARENT" ]; then
  if [ -L "$ARCHIVE_PARENT" ] || [ ! -d "$ARCHIVE_PARENT" ]; then
    printf 'archive parent must be a real directory: %s\n' "$ARCHIVE_PARENT" >&2
    exit 1
  fi
else
  mkdir -- "$ARCHIVE_PARENT"
fi
if [ -e "$ARCHIVE_PARENT" ] || [ -L "$ARCHIVE_PARENT" ]; then
  if [ -L "$ARCHIVE_PARENT" ] || [ ! -d "$ARCHIVE_PARENT" ]; then
    printf 'archive parent changed type: %s\n' "$ARCHIVE_PARENT" >&2
    exit 1
  fi
else
  printf 'archive parent disappeared: %s\n' "$ARCHIVE_PARENT" >&2
  exit 1
fi
if [ -e "$FAILED_PUBLICATION" ] || [ -L "$FAILED_PUBLICATION" ]; then
  printf 'archive leaf already exists or is a symlink: %s\n' "$FAILED_PUBLICATION" >&2
  exit 1
fi
mkdir -- "$FAILED_PUBLICATION"
mkdir -- "$FAILED_PUBLICATION/configs"
if [ -e "$FAILED_PUBLICATION" ] || [ -L "$FAILED_PUBLICATION" ]; then
  if [ -L "$FAILED_PUBLICATION" ] || [ ! -d "$FAILED_PUBLICATION" ]; then
    printf 'archive leaf changed type: %s\n' "$FAILED_PUBLICATION" >&2
    exit 1
  fi
else
  printf 'archive leaf disappeared: %s\n' "$FAILED_PUBLICATION" >&2
  exit 1
fi
if [ -e "$FAILED_PUBLICATION/configs" ] || [ -L "$FAILED_PUBLICATION/configs" ]; then
  if [ -L "$FAILED_PUBLICATION/configs" ] || [ ! -d "$FAILED_PUBLICATION/configs" ]; then
    printf 'archive config directory changed type: %s\n' "$FAILED_PUBLICATION/configs" >&2
    exit 1
  fi
else
  printf 'archive config directory disappeared: %s\n' "$FAILED_PUBLICATION/configs" >&2
  exit 1
fi
for destination in \
  "$FAILED_PUBLICATION/prerequisites.json" "$FAILED_PUBLICATION/results.json" \
  "$FAILED_PUBLICATION/REPORT.md" "$FAILED_PUBLICATION/configs/short.toml" \
  "$FAILED_PUBLICATION/configs/streaming.toml" "$FAILED_PUBLICATION/configs/bursty.toml"
do
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    printf 'archive destination exists or is a symlink: %s\n' "$destination" >&2
    exit 1
  fi
done
archive_candidate() {
  local source_path="$1"
  local destination_path="$2"
  if [ -e "$destination_path" ] || [ -L "$destination_path" ]; then
    printf 'archive destination exists or is a symlink: %s\n' "$destination_path" >&2
    exit 1
  fi
  if [ -e "$source_path" ] || [ -L "$source_path" ]; then
    if [ -L "$source_path" ] || [ ! -f "$source_path" ]; then
      printf 'publication candidate changed type: %s\n' "$source_path" >&2
      exit 1
    fi
    mv -- "$source_path" "$destination_path"
  fi
}
archive_candidate \
  examples/validation_study/prerequisites.json "$FAILED_PUBLICATION/prerequisites.json"
archive_candidate \
  examples/validation_study/results.json "$FAILED_PUBLICATION/results.json"
archive_candidate \
  examples/validation_study/REPORT.md "$FAILED_PUBLICATION/REPORT.md"
archive_candidate \
  examples/validation_study/configs/short.toml "$FAILED_PUBLICATION/configs/short.toml"
archive_candidate \
  examples/validation_study/configs/streaming.toml "$FAILED_PUBLICATION/configs/streaming.toml"
archive_candidate \
  examples/validation_study/configs/bursty.toml "$FAILED_PUBLICATION/configs/bursty.toml"
for retained in \
  "$FAILED_PUBLICATION/prerequisites.json" "$FAILED_PUBLICATION/results.json" \
  "$FAILED_PUBLICATION/REPORT.md" "$FAILED_PUBLICATION/configs/short.toml" \
  "$FAILED_PUBLICATION/configs/streaming.toml" "$FAILED_PUBLICATION/configs/bursty.toml"
do
  if [ -e "$retained" ] || [ -L "$retained" ]; then
    if [ -L "$retained" ] || [ ! -f "$retained" ]; then
      printf 'retained candidate is not a regular file: %s\n' "$retained" >&2
      exit 1
    fi
    sha256sum -- "$retained"
  fi
done
for candidate in \
  examples/validation_study/prerequisites.json examples/validation_study/results.json examples/validation_study/REPORT.md \
  examples/validation_study/configs/short.toml examples/validation_study/configs/streaming.toml \
  examples/validation_study/configs/bursty.toml
do
  if [ -e "$candidate" ] || [ -L "$candidate" ]; then
    printf 'publication candidate remains after archive: %s\n' "$candidate" >&2
    exit 1
  fi
done
git diff --exit-code -- architecture/ROADMAP.md
git status --short
```

Require empty status; the retained archive is ignored. The earlier source preflight independently rejected every
source symlink before any `mv`, while the archive checks independently rejected symlink parents, leaf, config
directory, and file destinations.

If any stale publication candidate is tracked because a prior study publication was committed, do not run the
archive branch. Read the two authoritative Task 6 Roadmap spans with the `git show` commands above. Use
`apply_patch` to delete exactly `prerequisites.json`, `results.json`, `REPORT.md`, and the three profile TOMLs. In the
same change, use `apply_patch` to restore the six exact Roadmap conditions listed above: preserve the seven Task 6
Docker boxes/note, restore Phase 3 `Current`, uncheck Internet, remove Phase 3 Internet/Done-when evidence claims,
uncheck all Validation Study boxes, and remove Validation Study evidence claims. Verify the Roadmap matches the reviewed Task 6 blob:

```bash
git diff --exit-code "$TASK6_BASE" -- architecture/ROADMAP.md
git add architecture/ROADMAP.md \
  examples/validation_study/prerequisites.json examples/validation_study/results.json examples/validation_study/REPORT.md \
  examples/validation_study/configs/short.toml examples/validation_study/configs/streaming.toml \
  examples/validation_study/configs/bursty.toml
git diff --cached --name-status
git commit -m "docs: withdraw invalid validation study"
git diff --exit-code -- architecture/ROADMAP.md
git status --short
```

The staged deletion list must contain only the existing subset of those six publication artifacts plus the Roadmap
correction. Independently review the corrective commit, verify the retained Task 6 Docker evidence byte-for-byte,
and require no stale scientific claim. Never amend the old publication or silently overwrite its paths.

Use this recovery matrix during the Task 7 review:

1. No candidate and only allowed Roadmap edits: apply the exact Roadmap reversal; status becomes clean.
2. Untracked regular candidate: move it to its unique ignored destination, hash it, and restore the Roadmap.
3. Source regular symlink or dangling symlink: reject before any move and retain the source unchanged.
4. Source directory or special file: reject before any move and retain the source unchanged.
5. Existing or dangling-symlink archive parent, leaf, config directory, or file destination: reject it independently;
   never follow or replace it.
6. Tracked candidate: use the reviewed corrective commit; never archive or overwrite it as untracked output.
7. Unexpected Roadmap hunk: stop without reverting it; only the six enumerated Task 7 conditions are authorized.
8. Committed stale publication: make and review the withdrawal commit before choosing another study ID.

Before retry, record the new reviewed base commit, require empty status and an unchanged Roadmap, and choose a new
validated study ID. Prove each new run/evidence/mount/archive root is absent even when the path is a dangling
symlink. Restart at Step 2; never selectively replace a run:

```bash
RETRY_BASE="$(git rev-parse HEAD)"
git diff --exit-code -- architecture/ROADMAP.md
git status --short
for fresh_root in \
  "runs/validation_study/$STUDY_ID" \
  "examples/validation_study/.study-work/evidence/$STUDY_ID" \
  "examples/validation_study/.study-work/mount/$STUDY_ID" \
  "examples/validation_study/.study-work/failed-publication/$STUDY_ID"
do
  if [ -e "$fresh_root" ] || [ -L "$fresh_root" ]; then
    printf 'new study root exists or is a symlink: %s\n' "$fresh_root" >&2
    exit 1
  fi
done
```

Record `RETRY_BASE`; a new ID is not authorized until all commands above pass on the clean reviewed base.

- [ ] **Step 6: Validate checked result bytes and audit raw/check-in boundaries**

Parse and re-render prerequisites/results byte-identically; verify every config hash and result artifact hash against
the retained ignored files; verify no raw evidence path is tracked. Confirm all ten run directories retain exactly
the nine production names and the checked tree contains only README, three configs, prerequisite JSON, and result
JSON before report creation.

- [ ] **Step 7: Inspect traces/diagnostics and author the evidence-backed report**

Use `apply_patch` to create `REPORT.md` with the seven exact `REPORT_HEADINGS` strings in their locked order and
these actual values from `results.json`:

1. question, scope, environment commit/tool/image IDs, URL endpoint contract, balanced protocol, and pilot limits;
2. natural-variation table for all three workload pairs and descriptor variances;
3. every family's champion fitness and four component summaries, run-to-run variance, and winner counts;
4. held-out winner, final published score, and full-lifecycle runtime tables;
5. trace summaries plus concrete diagnostic fields explaining every major component disagreement;
6. streaming-repeat-2 reproduction config equality, fresh capture similarity, winner observations, and score deltas;
7. limitations and exactly one evidence-backed next-work decision.

Include the exact study ID, evidence commit, target/capture image IDs, and all nine primary run IDs plus
`10-streaming-r2-reproduction` so the local audit binds the prose to the checked and retained evidence.

Sections 1 and 7 must disclose the invalidated pilot attempt `validation-study-20260814-ovh`, its observed
`W = 0.7874600887298584`, the all-invalid MMPP-family root cause, and the `lambda0` lower-bound amendment used only
by the Validation Study from `0.01` to `10.0`. State that no failed-pilot score or winner was accepted, analyzed, or used to
choose the bound; the failed attempt was excluded and retained locally; and all nine primary runs plus the
reproduction were rerun under one fresh study ID. Do not present failed-pilot scores.

Call the streaming workload rate-limited, not exactly paced. Label family champion scores as selection evidence, not
held-out generalization. If no repeated model/metric gap supports later work, say that the pilot does not justify a
Roadmap expansion. Never add confidence intervals, hypothesis tests, protocol claims, payload analysis, or rounded
values that contradict full-precision JSON.

Interpret only patterns supported by named trace/diagnostic values: frame-size agreement with weak multiscale
means marks match but local directional volume does not; IAT agreement with weak ACF means marginal timing matches
but selected serial dependence does not; ACF agreement with weak multiscale means selected lags match while burst
placement/silence/directional volume does not. Strong held-out raw evidence with weaker published evidence may be
attributed only to the already proved timestamp quantization and PCAPNG reparse, never to different raw randomness
or final guards.

- [ ] **Step 8: Build the Roadmap evidence ledger before checking boxes**

Map exact Internet JUnit counts, ten run IDs, config/result/report paths, seeds, families, component scores, runtime
summaries, trace disagreements, reproduction guard, cleanup records, image identities, and raw evidence hashes to
the remaining Phase 3 Internet/Done-when boxes and every Validation Study checkbox. Preserve Task 6's Docker mappings. Check a
box only when its exact evidence exists. Validation Study Done-when requires the report's specific fidelity/gap decision, not
merely successful execution.

- [ ] **Step 9: Update Roadmap truthfully and run local publication checks**

Leave Task 6's seven Docker-backed Phase 3 boxes unchanged. Mark the opt-in Internet smoke, Phase 3 Done-when, all
Validation Study deliverables/tests, and Validation Study Done-when only when the ledger proves them. Add a dated concise verification
note naming the study ID, ten fresh runs, report/results, and retained ignored audit locations.

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked pyright scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked ruff format --check .
uv run --locked ruff check .
git diff --check
```

Then run the exact local, read-only publication audit. It validates canonical prerequisite/result/report linkage,
all configs, ten checkpoints and artifact sets, raw/quantized/published lineage, natural variation, summaries, and
reproduction deltas without Docker, the public Internet, or subprocess children:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python -c \
  'from pathlib import Path; import scripts.run_validation_study as study; '\
'study.audit_published_study(repository_root=Path.cwd(), '\
'prerequisite_path=Path("examples/validation_study/prerequisites.json"), '\
'result_path=Path("examples/validation_study/results.json"), '\
'report_path=Path("examples/validation_study/REPORT.md"))'
```

- [ ] **Step 10: Obtain independent evidence/report review before publication commit**

Give a fresh reviewer the approved spec, checked configs/JSON/report/Roadmap diff, ignored prerequisite command
output/JUnit/header evidence, exact ten run trees, and Task 7 ledger. Require separate architecture/evidence and
code-quality verdicts. Fix every Critical/Important finding. A code or protocol fix invalidates the current study and
requires a new ID plus all ten reruns; a report-only correction must remain faithful to unchanged JSON.

- [ ] **Step 11: Commit only checked Validation Study publication files**

```bash
git add architecture/ROADMAP.md \
  examples/validation_study/README.md examples/validation_study/REPORT.md \
  examples/validation_study/prerequisites.json examples/validation_study/results.json \
  examples/validation_study/configs/short.toml examples/validation_study/configs/streaming.toml \
  examples/validation_study/configs/bursty.toml
git diff --cached --name-only
git commit -m "docs: publish Validation Study"
```

Require the staged list to contain no `.study-work`, `runs/`, PCAPNG, checkpoint, header, JUnit, or command-output
path. Write `task-7-report.md` with commit hash, file sizes/hashes, exact command results, interpretation decisions,
review verdict, and retained audit paths.

---

### Task 8: Final locked gates, whole-project review, and clean completion

**Files:**

- Modify only for verified Critical/Important fixes: the owning Validation Study script/test/doc/report/Roadmap files.
- Create ignored: `.superpowers/sdd/2026-08-13-validation-study/task-8-report.md`
- Update ignored: `.superpowers/sdd/2026-08-13-validation-study/progress.md`

**Interfaces:**

- Consumes: committed Validation Study publication, retained raw audit evidence, all architecture completion requirements,
  process guard, and the operator URL.
- Produces: fresh nonoverlapping final gate evidence, Critical/Important-free independent review, accurate complete
  Roadmap, and a clean local Git history.

- [ ] **Step 1: Audit every Roadmap checkbox and checked artifact before final gates**

Strictly parse both JSON files, all three configs, every retained audit hash, and all ten run directories. Confirm
Phase 1–7 boxes have direct evidence, all examples/fixtures exist, no raw Internet data is tracked, result/report
values agree, and the published evidence identifies the clean commit actually used for the study.

Run the same exact local, read-only publication audit used before the publication commit:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python -c \
  'from pathlib import Path; import scripts.run_validation_study as study; '\
'study.audit_published_study(repository_root=Path.cwd(), '\
'prerequisite_path=Path("examples/validation_study/prerequisites.json"), '\
'result_path=Path("examples/validation_study/results.json"), '\
'report_path=Path("examples/validation_study/REPORT.md"))'
```

Require exit zero and no changed path before continuing.

- [ ] **Step 2: Run locked sync, lock, all fixture checks, and process containment**

```bash
uv sync --locked --all-groups
uv lock --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_phase2_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_model_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_fit_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

- [ ] **Step 3: Run formatting, linting, ordinary strict types, and explicit Validation Study types**

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pyright scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
git diff --check
```

- [ ] **Step 4: Run the exact fast and coverage gates sequentially**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Require at least 90% branch-aware package coverage and direct named coverage for every mathematical, configuration,
arbitration, artifact, and Validation Study behavior. Do not overlap or reuse pre-fix results.

- [ ] **Step 5: Run available Docker and Internet gates serially**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet \
  --internet-url "$TRAFFICLAB_INTERNET_URL"
```

Require actual selected passes and clean labelled Docker state. The already completed nine-plus-one study is the
external Validation Study evidence; do not rerun it merely as a generic test gate.

- [ ] **Step 6: Obtain independent whole-project final review**

Give a fresh reviewer the full implementation range from the Phase 1 base through Validation Study publication, all
architecture, current Roadmap, local/Docker/Internet gate outputs, Validation Study checked and retained evidence, SDD task
reports, and commit log. Require explicit architecture-compliance and code-quality verdicts with severity. Fix every
Critical/Important finding and re-run affected gates.

If a fix changes production/study code, locked protocol/configuration, or evidence extraction, invalidate the
published study, revert its completion claims through an ordinary corrective commit, execute a new prerequisite
and ten-run study under a new ID, regenerate JSON/report, and re-review. Do not silently keep results from different
code.

- [ ] **Step 7: Close the evidence ledger and require a clean worktree**

Record exact commands, status, test counts, coverage, Docker/Internet outcomes, review verdict, publication commit,
and final `git status` in `task-8-report.md` and `progress.md`. If review fixes changed checked files, commit them with
a narrow truthful message after verification. Then require:

```bash
git diff --check
git status --short
git log -12 --oneline --decorate
```

Completion requires empty `git status`, every Roadmap checkbox accurate, every final gate green, retained local
implementation commits, and no Critical/Important final finding.

## Plan Self-Review

- Every Validation Study design section maps to one owning task: schema/statistics (Task 1), profiles/config/header evidence
  (Task 2), production artifact extraction/natural variation (Task 3), prerequisite subprocesses/images (Task 4),
  serial primary/reproduction execution (Task 5), docs/local/Docker readiness (Task 6), real evidence/report/Roadmap
  (Task 7), and final gates/review (Task 8).
- The file map contains one support script, one unit module, one in-process integration module, the exact example
  tree, ignore rule, Roadmap update, and ignored SDD records. It contains no production-core or dependency change.
- Aggregate dataclass names are exactly the six approved names. Leaf schema validation uses small typed helpers and
  dictionaries rather than a serialization framework or dozens of record classes.
- Function names/types used by later tasks match Locked Interfaces. Production imports exist in the current source:
  configuration, checkpoint, model, evaluation, trace, quantization, comparison, and run boundaries are reused.
- Workload argv, balanced order, seeds, families, methods, generation limits, operator/bound oracle, timeouts,
  scratch paths/modes, capability constraints, image digest/ID evidence, and exact guard commands are explicit.
- Primary extraction distinguishes selection, fresh held-out, and published evidence; reproduction reconstructs
  held-out authority with `evaluate_final` and binds published comparison to exact input hashes.
- The one in-process extraction test joins the unit extraction RED before implementation and joins the same GREEN;
  its typed schema assertions cast the methods object, compare its key tuple to `METHOD_ORDER`, and narrow optional
  comparison lineage before indexing.
- Natural variation is symmetric for unequal W; every requested descriptive statistic and winner count is derived
  from exactly three within-workload records. Study orchestration validates both derived tuples immediately after
  primary nine and proves a precondition/summary failure makes zero reproduction calls.
- No task checks in raw PCAP/run/evidence data. The report contains observed values only after successful external
  execution, and absence of the URL leaves Phase 3 Internet/Done-when and all Validation Study artifacts/boxes untouched while
  retaining Task 6's seven evidence-mapped Docker boxes.
- Reproduction requires an absent directory, uses a confined repository-relative config token under repository cwd,
  and the identical guarded local audit in Tasks 7/8 rechecks report/checkpoint/artifact/lineage evidence offline.
- Failed external attempts retain and hash exact ignored roots. Uncommitted recovery uses `apply_patch` to restore
  the complete reviewed Task 6 Roadmap state before a new ID, then moves only exact untracked regular publication
  candidates to an ignored archive. Every absence check includes symlinks; tracked stale publication requires a
  reviewed corrective commit, with no broad deletion.
- Every pytest command is guarded with memory-high, memory-max, swap-max, wall-time, and kill-after. Focused/external
  commands use `-n 0`; broad commands use `-n 4 --dist worksteal`; commands are sequential and nonnested.
- Strict Pyright includes the script and both tests exactly. Fixture, process containment, fast, coverage, Docker,
  Internet, config construction, JSON parse/render, raw artifact validation, and clean-tree gates are all named.
- Every task ends with a coherent commit and ignored report. Independent review checkpoints cover Tasks 1–3,
  Tasks 4–5, Task 6, Task 7, and final Task 8 with mandatory Critical/Important fixes; any late code/protocol
  correction explicitly invalidates and reruns external evidence.
- Deferred-work language, vague cross-task references, undefined functions/types, generic error-handling requests,
  and fabricated external values were removed in this review.
