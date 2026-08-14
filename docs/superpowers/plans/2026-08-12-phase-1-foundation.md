# Trafficlab Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a typed installable package whose Python API and CLI parse,
validate, locally preflight, and snapshot one complete effective experiment
configuration without contacting Docker.

**Architecture:** Pydantic models define the one strict immutable TOML schema;
Python `tomllib` reads it and Tomli-W writes deterministic effective snapshots.
Local preflight and run-directory publication are ordinary typed functions used
directly by an `argparse` CLI, with no framework or subprocess boundary.

**Tech Stack:** CPython 3.12, uv, Pydantic 2, tomllib, Tomli-W, argparse,
pytest, pytest-cov, pytest-xdist, Ruff, Pyright

## Global Constraints

- Use `src/trafficlab/`; public functions and models are strictly typed.
- Every Pydantic model uses
  `ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)`; scalar fields
  use Pydantic strict scalar types while TOML arrays may become tuples and path
  strings may become `Path` values.
- TOML argv values are arrays and are never parsed as shell strings.
- Resolve relative run and mount-source paths against the experiment file's
  parent; retain container paths as POSIX paths.
- `preflight --config-only` must not import a Docker adapter or invoke a
  subprocess.
- Existing run directories are errors; never silently replace one.
- Write `experiment.toml` atomically and validate its parse/model round trip
  before renaming.
- Keep lines at no more than 120 characters.
- The non-Docker suite must use branch coverage and finish at or above 90%.
- Commit only after the focused tests, Ruff, and Pyright pass.

---

### Task 1: Installable package and minimal CLI boundary

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/trafficlab/__init__.py`
- Create: `src/trafficlab/__main__.py`
- Create: `src/trafficlab/cli.py`
- Create: `src/trafficlab/errors.py`
- Create: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: no application code
- Produces: `trafficlab.__version__: str`, `TrafficlabError`, and
  `trafficlab.cli.main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: Write failing package and CLI tests**

```python
from trafficlab import __version__
from trafficlab.cli import main


def test_version_is_project_version() -> None:
    assert __version__ == "0.1.0"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "trafficlab 0.1.0\n"


def test_no_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err
```

- [ ] **Step 2: Run the tests and confirm the red state**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_package.py
```

Expected: collection fails because `trafficlab` does not exist.

- [ ] **Step 3: Add runtime dependencies and package metadata**

Run:

```bash
uv add 'pydantic>=2.11,<3' 'tomli-w>=1.2,<2'
```

Add to `pyproject.toml`:

```toml
[project.scripts]
trafficlab = "trafficlab.cli:entrypoint"

[tool.coverage.report]
show_missing = true
fail_under = 90
```

- [ ] **Step 4: Implement the package boundary**

Use this public shape:

```python
# src/trafficlab/errors.py
class TrafficlabError(Exception):
    def __init__(self, message: str, *, corrective_action: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.corrective_action = corrective_action
        self.exit_code = exit_code


# src/trafficlab/cli.py
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trafficlab")
    parser.add_argument("--version", action="version", version=f"trafficlab {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        parser.print_usage(sys.stderr)
        return 2
    try:
        parser.parse_args(arguments)
    except SystemExit as error:
        return int(error.code)
    return 0


def entrypoint() -> NoReturn:
    raise SystemExit(main())
```

`src/trafficlab/__main__.py` calls `entrypoint()`. Read the version with
`importlib.metadata.version("trafficlab")` and fall back to `"0.1.0"` only for
an unpackaged source-tree import.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_package.py
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

Expected: all commands exit zero.

Commit:

```bash
git add pyproject.toml uv.lock src/trafficlab tests/unit/test_package.py
git commit -m "feat: add installable trafficlab package"
```

---

### Task 2: Strict experiment schema

**Files:**
- Create: `src/trafficlab/config.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/test_config_schema.py`

**Interfaces:**
- Consumes: Pydantic 2
- Produces: immutable models `ExperimentConfig`, `RunConfig`, `TargetConfig`,
  `MountConfig`, `CaptureConfig`, `GenerationConfig`, `GeneticConfig`,
  `ModelsConfig`, and `SimilarityConfig`

- [ ] **Step 1: Create one complete valid mapping fixture**

In `tests/conftest.py`, expose `valid_config_data(tmp_path: Path) -> dict[str,
object]` with this exact shape:

```python
{
    "run": {
        "directory": str(tmp_path / "run"),
        "minimum_free_bytes": 1_048_576,
        "master_seed": 12345,
        "final_seed": 54321,
    },
    "target": {
        "image": "curlimages/curl:8.10.1",
        "argv": ["https://example.invalid/data"],
        "environment": {"LANG": "C"},
        "working_directory": "/work",
        "mounts": [],
    },
    "capture": {
        "image": "trafficlab-capture:local",
        "network_probe_url": "https://example.invalid/",
        "readiness_timeout_seconds": 10.0,
        "workload_timeout_seconds": 30.0,
        "flush_timeout_seconds": 5.0,
        "total_timeout_seconds": 60.0,
    },
    "generation": {
        "trial": {"max_packets": 2_000, "max_output_bytes": 4_000_000, "max_wall_seconds": 5.0},
        "final": {"max_packets": 20_000, "max_output_bytes": 40_000_000, "max_wall_seconds": 30.0},
    },
    "genetic": {
        "population_size": 9,
        "generation_count": 3,
        "tournament_size": 3,
        "elite_count": 1,
        "trial_seeds": [101, 102],
        "duplicate_mutation_attempts": 3,
        "early_stopping_generations": 0,
        "resume": False,
    },
    "models": {
        "enabled": ["poisson_empirical", "markov_renewal", "mmpp"],
        "poisson_empirical": {
            "crossover_probability": 0.9,
            "mutation_probability": 1.0,
            "mutation_scale": 0.1,
            "c_lambda": {"lower": 0.25, "upper": 4.0},
        },
        "markov_renewal": {
            "crossover_probability": 0.9,
            "mutation_probability": 0.2,
            "mutation_scale": 0.1,
            "q1": {"lower": 0.1, "upper": 0.4},
            "q2": {"lower": 0.6, "upper": 0.9},
            "alpha": {"lower": 0.0, "upper": 2.0},
            "r": {"lower": 1, "upper": 8},
            "c_t": {"lower": 0.25, "upper": 4.0},
        },
        "mmpp": {
            "crossover_probability": 0.9,
            "mutation_probability": 0.25,
            "mutation_scale": 0.1,
            "q01": {"lower": 0.01, "upper": 10.0},
            "q10": {"lower": 0.01, "upper": 10.0},
            "lambda0": {"lower": 0.01, "upper": 100.0},
            "lambda1": {"lower": 0.1, "upper": 1_000.0},
        },
    },
    "similarity": {
        "iat_diagnostic_quantile": 0.95,
        "acf_lags": [1],
        "acf_lag_weights": [1.0],
        "acf_iat_weight": 0.5,
        "acf_size_weight": 0.5,
        "multiscale_widths_seconds": [0.1, 1.0],
        "multiscale_scale_weights": [0.5, 0.5],
        "multiscale_packet_weight": 0.5,
        "multiscale_byte_weight": 0.5,
        "max_direction_bin_cells": 100_000,
        "method_weights": {
            "frame_size_ks": 0.25,
            "iat_ks": 0.25,
            "autocorrelation": 0.25,
            "multiscale_rate": 0.25,
        },
    },
}
```

- [ ] **Step 2: Write failing schema tests**

Test that the mapping creates `ExperimentConfig`, models are frozen, an unknown
root or nested key fails with `extra_forbidden`, strict strings/integers are not
coerced, nonfinite floats fail, empty argv fails, relative container paths fail,
and an environment value that is not a string fails.

Use explicit examples:

```python
def test_unknown_nested_key_is_rejected(valid_config_data: dict[str, object]) -> None:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["capture"])["typo"] = 1
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExperimentConfig.model_validate(data)


def test_models_are_frozen(valid_config_data: dict[str, object]) -> None:
    config = ExperimentConfig.model_validate(valid_config_data)
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.run.master_seed = 9
```

- [ ] **Step 3: Confirm the schema tests fail**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_config_schema.py
```

Expected: import fails because `trafficlab.config` does not exist.

- [ ] **Step 4: Implement the complete schema types**

Use these exact fields and base configuration:

```python
FamilyName = Literal["poisson_empirical", "markov_renewal", "mmpp"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FloatBounds(StrictModel):
    lower: StrictFloat
    upper: StrictFloat


class IntegerBounds(StrictModel):
    lower: StrictInt
    upper: StrictInt


class MountConfig(StrictModel):
    source: Path
    target: StrictStr
    read_only: StrictBool = True


class RunConfig(StrictModel):
    directory: Path
    minimum_free_bytes: StrictInt
    master_seed: StrictInt
    final_seed: StrictInt


class TargetConfig(StrictModel):
    image: StrictStr
    argv: tuple[StrictStr, ...]
    environment: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    working_directory: StrictStr
    mounts: tuple[MountConfig, ...] = ()


class CaptureConfig(StrictModel):
    image: StrictStr
    network_probe_url: StrictStr
    readiness_timeout_seconds: StrictFloat
    workload_timeout_seconds: StrictFloat
    flush_timeout_seconds: StrictFloat
    total_timeout_seconds: StrictFloat


class GenerationLimits(StrictModel):
    max_packets: StrictInt
    max_output_bytes: StrictInt
    max_wall_seconds: StrictFloat


class GenerationConfig(StrictModel):
    trial: GenerationLimits
    final: GenerationLimits


class GeneticConfig(StrictModel):
    population_size: StrictInt
    generation_count: StrictInt
    tournament_size: StrictInt
    elite_count: StrictInt
    trial_seeds: tuple[StrictInt, ...]
    duplicate_mutation_attempts: StrictInt
    early_stopping_generations: StrictInt
    resume: StrictBool = False


class FamilyOperators(StrictModel):
    crossover_probability: StrictFloat
    mutation_probability: StrictFloat
    mutation_scale: StrictFloat


class PoissonConfig(FamilyOperators):
    c_lambda: FloatBounds


class MarkovRenewalConfig(FamilyOperators):
    q1: FloatBounds
    q2: FloatBounds
    alpha: FloatBounds
    r: IntegerBounds
    c_t: FloatBounds


class MmppConfig(FamilyOperators):
    q01: FloatBounds
    q10: FloatBounds
    lambda0: FloatBounds
    lambda1: FloatBounds


class ModelsConfig(StrictModel):
    enabled: tuple[FamilyName, ...]
    poisson_empirical: PoissonConfig | None = None
    markov_renewal: MarkovRenewalConfig | None = None
    mmpp: MmppConfig | None = None


class MethodWeights(StrictModel):
    frame_size_ks: StrictFloat
    iat_ks: StrictFloat
    autocorrelation: StrictFloat
    multiscale_rate: StrictFloat


class SimilarityConfig(StrictModel):
    iat_diagnostic_quantile: StrictFloat
    acf_lags: tuple[StrictInt, ...]
    acf_lag_weights: tuple[StrictFloat, ...]
    acf_iat_weight: StrictFloat
    acf_size_weight: StrictFloat
    multiscale_widths_seconds: tuple[StrictFloat, ...]
    multiscale_scale_weights: tuple[StrictFloat, ...]
    multiscale_packet_weight: StrictFloat
    multiscale_byte_weight: StrictFloat
    max_direction_bin_cells: StrictInt
    method_weights: MethodWeights


class ExperimentConfig(StrictModel):
    run: RunConfig
    target: TargetConfig
    capture: CaptureConfig
    generation: GenerationConfig
    genetic: GeneticConfig
    models: ModelsConfig
    similarity: SimilarityConfig
```

Add field-level constraints with `Field`: nonempty strings/tuples, nonnegative
seeds and retry counts, positive counts/bytes/times, and absolute POSIX mount
targets and working directories via `field_validator`.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_config_schema.py
uv run --locked ruff format --check src/trafficlab/config.py tests
uv run --locked ruff check src/trafficlab/config.py tests
uv run --locked pyright
```

Commit:

```bash
git add src/trafficlab/config.py tests/conftest.py tests/unit/test_config_schema.py
git commit -m "feat(config): define strict experiment schema"
```

---

### Task 3: Semantic and cross-section validation

**Files:**
- Modify: `src/trafficlab/config.py`
- Create: `tests/unit/test_config_validation.py`

**Interfaces:**
- Consumes: Task 2 schema
- Produces: a valid `ExperimentConfig` whose bounds, weights, enabled families,
  operator values, timeouts, and genetic sizes satisfy all Phase 1 invariants

- [ ] **Step 1: Write table-driven failing tests for primitive invariants**

Cover these exact cases:

- every probability below `0`, above `1`, or nonfinite;
- every positive scale/count/timeout at `0` or below;
- `FloatBounds.lower >= upper` and `IntegerBounds.lower >= upper`;
- logarithmic bounds with `lower <= 0` for `c_lambda`, `c_t`, all MMPP genes;
- Markov `q1`/`q2` outside `(0, 1)`, `alpha.lower < 0`, and `r.lower < 1`;
- duplicate enabled family names, an empty enabled list, missing enabled-family
  table, and a table supplied for a disabled family;
- operator defaults are exactly `(0.9, 1.0, 0.1)`, `(0.9, 0.2, 0.1)`, and
  `(0.9, 0.25, 0.1)` when omitted from the three enabled family tables;
- distinct overrides affect only their own family;
- zero duplicate attempts is valid and a negative value fails.

For defaults, remove the three keys before validation and assert:

```python
assert config.models.poisson_empirical is not None
assert config.models.markov_renewal is not None
assert config.models.mmpp is not None
assert config.models.poisson_empirical.operator_values == (0.9, 1.0, 0.1)
assert config.models.markov_renewal.operator_values == (0.9, 0.2, 0.1)
assert config.models.mmpp.operator_values == (0.9, 0.25, 0.1)
```

- [ ] **Step 2: Write failing tests for cross-section invariants**

Require:

```text
population_size >= 2
population_size >= elite_count + enabled_family_count
1 <= elite_count < population_size
2 <= tournament_size <= population_size
generation_count >= 1
trial_seeds is nonempty and unique
early_stopping_generations == 0 or <= generation_count
each stage timeout <= total_timeout_seconds
final limits >= trial limits for packets, bytes, and wall seconds
all weight vectors are nonnegative and sum to one within 1e-12
acf lags are unique positive integers
acf lags and lag weights have equal nonzero length
multiscale widths are finite, positive, unique, strictly increasing
multiscale widths and scale weights have equal nonzero length
max_direction_bin_cells >= 2
iat_diagnostic_quantile is strictly between zero and one
```

- [ ] **Step 3: Run and confirm failures**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_config_validation.py
```

Expected: invalid cases are accepted or defaults are absent.

- [ ] **Step 4: Implement exact validators and defaults**

Add `operator_values` properties, subclass defaults, `field_validator` and
`model_validator(mode="after")` methods. Use one shared helper:

```python
def _weights_sum_to_one(values: Sequence[float], name: str) -> None:
    if any(value < 0.0 for value in values) or not math.isclose(sum(values), 1.0, abs_tol=1e-12):
        raise ValueError(f"{name} must be nonnegative and sum to one")
```

The `ModelsConfig` validator compares `enabled` to the non-`None` family table
names exactly. The `ExperimentConfig` validator enforces the population and
timeout relationships using the already validated nested models.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_config_schema.py tests/unit/test_config_validation.py
uv run --locked ruff format --check src/trafficlab/config.py tests
uv run --locked ruff check src/trafficlab/config.py tests
uv run --locked pyright
```

Commit:

```bash
git add src/trafficlab/config.py tests/unit/test_config_validation.py
git commit -m "feat(config): validate experiment invariants"
```

---

### Task 4: TOML loading, path resolution, and effective snapshots

**Files:**
- Create: `src/trafficlab/config_io.py`
- Create: `src/trafficlab/artifacts.py`
- Create: `tests/unit/test_config_io.py`
- Create: `tests/unit/test_artifacts.py`

**Interfaces:**
- Consumes: `ExperimentConfig`
- Produces:
  `load_experiment(path: Path) -> ExperimentConfig`,
  `render_effective_config(config: ExperimentConfig) -> bytes`, and
  `create_run_directory(config: ExperimentConfig) -> Path`

- [ ] **Step 1: Write failing loader tests**

Test valid TOML, malformed TOML, missing file, UTF-8 text, Pydantic path-rich
errors, source-relative run/mount paths, and an absolute mount source. The
loader must raise `TrafficlabError` with `corrective_action` and must not expose
a raw `TOMLDecodeError` or `ValidationError` through the CLI boundary.

Use this path assertion:

```python
config = load_experiment(tmp_path / "config" / "experiment.toml")
assert config.run.directory == (tmp_path / "config" / "runs" / "case").resolve()
assert config.target.mounts[0].source == (tmp_path / "config" / "data").resolve()
```

- [ ] **Step 2: Write failing snapshot tests**

Require deterministic bytes, TOML/model round-trip equality, exact resolved
operator defaults, atomic rename, no temporary sibling after success, refusal
to replace an existing directory, and cleanup of a just-created directory after
an injected snapshot-write failure.

```python
run_path = create_run_directory(config)
snapshot = load_experiment(run_path / "experiment.toml")
assert snapshot == config
assert (run_path / "run.log").is_file()
assert list(run_path.glob(".experiment.toml.*.tmp")) == []
```

- [ ] **Step 3: Run and confirm failures**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_config_io.py tests/unit/test_artifacts.py
```

Expected: imports fail because both modules are absent.

- [ ] **Step 4: Implement loader and deterministic renderer**

Use `tomllib.load()` and convert Pydantic failures into dotted messages built
from `error["loc"]`. Resolve only `run.directory` and mount `source` paths with
`model_copy(update=...)`.

Render with:

```python
def render_effective_config(config: ExperimentConfig) -> bytes:
    data = config.model_dump(mode="json", exclude_none=True)
    text = tomli_w.dumps(data)
    reparsed = tomllib.loads(text)
    if ExperimentConfig.model_validate(reparsed) != config:
        raise TrafficlabError(
            "effective configuration did not round-trip",
            corrective_action="report the deterministic configuration renderer defect",
        )
    return text.encode("utf-8")
```

For snapshot loading, already absolute paths remain unchanged.

- [ ] **Step 5: Implement atomic run creation**

`create_run_directory` uses `Path.mkdir(parents=True, exist_ok=False)`, creates
`run.log`, writes a uniquely named temporary sibling inside the new directory,
flushes and `os.fsync()`s it, validates the bytes again, then calls
`os.replace(temp, experiment.toml)`. It catches publication failures, removes
only the directory it created when still empty apart from its own files, and
raises `TrafficlabError`.

- [ ] **Step 6: Verify and commit**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_config_io.py tests/unit/test_artifacts.py
uv run --locked ruff format --check src/trafficlab tests
uv run --locked ruff check src/trafficlab tests
uv run --locked pyright
```

Commit:

```bash
git add src/trafficlab/config_io.py src/trafficlab/artifacts.py \
  tests/unit/test_config_io.py tests/unit/test_artifacts.py
git commit -m "feat(config): snapshot effective experiments"
```

---

### Task 5: Local preflight without Docker

**Files:**
- Create: `src/trafficlab/preflight.py`
- Create: `tests/unit/test_preflight.py`

**Interfaces:**
- Consumes: `ExperimentConfig`
- Produces:
  `PreflightFinding`, `PreflightReport`, and
  `check_local(config: ExperimentConfig, *, disk_usage: DiskUsage = shutil.disk_usage,
  writable: Writable = default_writable) -> PreflightReport`

- [ ] **Step 1: Write failing local-preflight tests**

Cover:

- every mount source exists;
- the run directory does not exist;
- the nearest existing run-directory parent is a directory and writable, using
  an injected `writable(path) -> bool` check in permission tests;
- available free bytes are at least `minimum_free_bytes`;
- a valid configuration returns findings named `mounts`, `run_directory`, and
  `free_space`, all with `ok=True`;
- missing mount, existing output, unwritable/non-directory parent, and
  insufficient space each produce a direct failing finding and
  `report.require_success()` raises `TrafficlabError`;
- no test imports `subprocess` or passes a Docker runner.

Use protocols rather than the private `shutil._ntuple_diskusage` type:

```python
class SupportsFree(Protocol):
    free: int


class DiskUsage(Protocol):
    def __call__(self, path: Path) -> SupportsFree: ...
```

- [ ] **Step 2: Run and confirm failures**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_preflight.py
```

Expected: import fails because `trafficlab.preflight` does not exist.

- [ ] **Step 3: Implement findings and local checks**

Use immutable dataclasses:

```python
@dataclass(frozen=True, slots=True)
class PreflightFinding:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    config: ExperimentConfig
    findings: tuple[PreflightFinding, ...]

    def require_success(self) -> None:
        failures = [finding for finding in self.findings if not finding.ok]
        if failures:
            detail = "; ".join(f"{item.name}: {item.detail}" for item in failures)
            raise TrafficlabError(detail, corrective_action="correct the reported local preflight failures")
```

Find the nearest existing parent without creating it. Evaluate all local checks
so one invocation reports every independent problem.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run --locked pytest -q -n 0 tests/unit/test_preflight.py
uv run --locked ruff format --check src/trafficlab/preflight.py tests/unit/test_preflight.py
uv run --locked ruff check src/trafficlab/preflight.py tests/unit/test_preflight.py
uv run --locked pyright
```

Commit:

```bash
git add src/trafficlab/preflight.py tests/unit/test_preflight.py
git commit -m "feat(preflight): add local configuration checks"
```

---

### Task 6: Preflight Python API, CLI, logs, and example experiment

**Files:**
- Modify: `src/trafficlab/preflight.py`
- Modify: `src/trafficlab/cli.py`
- Create: `examples/configs/minimal.toml`
- Create: `examples/data/request.txt`
- Create: `tests/integration/test_preflight_cli.py`

**Interfaces:**
- Consumes: `load_experiment`, `check_local`, `create_run_directory`
- Produces:
  `prepare_experiment(path: Path) -> PreparedExperiment` and public command
  `trafficlab preflight EXPERIMENT --config-only`

- [ ] **Step 1: Write failing Python-API integration tests**

Define:

```python
@dataclass(frozen=True, slots=True)
class PreparedExperiment:
    source: Path
    config: ExperimentConfig
    report: PreflightReport
    run_directory: Path
```

Test that `prepare_experiment` loads, runs local checks, creates the directory,
writes the effective snapshot/log, and returns the same config obtained from
reloading `experiment.toml`. Assert failure before publication leaves no run
directory.

- [ ] **Step 2: Write failing CLI integration tests**

Call `main()` directly with an injected preparation callable and also execute
the installed `trafficlab` script. Require:

```text
trafficlab preflight EXPERIMENT --config-only -> exit 0 and concise run path
trafficlab preflight EXPERIMENT               -> exit 2 until Phase 3 adds full Docker checks
trafficlab capture EXPERIMENT                 -> argparse unknown-command exit 2
trafficlab preflight --config-only            -> argparse missing-argument exit 2
trafficlab preflight EXPERIMENT --unknown      -> argparse unknown-option exit 2
```

For a malformed configuration, require `preflight:` plus a corrective action on
stderr and no traceback. Inject a callable that records invocations and assert
the config-only command makes no Docker/subprocess call.

- [ ] **Step 3: Run and confirm failures**

Run:

```bash
uv run --locked pytest -q -n 0 -m integration tests/integration/test_preflight_cli.py
```

Expected: `preflight` is not a recognized command.

- [ ] **Step 4: Implement the API and CLI handler**

`prepare_experiment` performs `load -> check_local -> require_success ->
create_run_directory`. Add `preflight` with `EXPERIMENT` and `--config-only` to
the parser. Phase 1 rejects omission of `--config-only` with this stable message:

```text
preflight: full Docker checks are unavailable until capture support is installed;
rerun with --config-only
```

Catch `TrafficlabError` in `main`, print
`preflight: {message}; {corrective_action}` to stderr, and return its exit code.
Never append to an already existing run directory. Loader, local-preflight, and
existing-output errors occur before publication and therefore use stderr. After
the new run directory is published, later command failures append full detail to
its `run.log`.

- [ ] **Step 5: Add a complete checked-in example**

Create `examples/configs/minimal.toml` with the Task 2 valid shape, paths relative
to the repository, all resolved operator values explicit, `run.directory =
"../../runs/minimal"`, and a read-only mount from `../data` to `/work/data`.
Create `examples/data/request.txt` containing one line: `trafficlab example`.

- [ ] **Step 6: Verify and commit**

Run:

```bash
uv run --locked pytest -q -n 0 -m integration tests/integration/test_preflight_cli.py
uv run --locked trafficlab preflight examples/configs/minimal.toml --config-only
test -f runs/minimal/experiment.toml
test -f runs/minimal/run.log
```

Remove only the generated ignored `runs/minimal` directory after inspection.
Then run:

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

Commit:

```bash
git add src/trafficlab/preflight.py src/trafficlab/cli.py examples tests/integration/test_preflight_cli.py
git commit -m "feat(preflight): expose config-only command"
```

---

### Task 7: Phase 1 quality gate and Roadmap closure

**Files:**
- Modify: `architecture/ROADMAP.md`
- Modify: `architecture/TESTING.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: Tasks 1–6
- Produces: verified Phase 1 completion and durable commands for later phases

- [ ] **Step 1: Run the clean locked-environment checks**

Run:

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

Expected: every command exits zero and `git status --short` shows no lockfile
change.

- [ ] **Step 2: Run the fast and detailed suites**

Run:

```bash
uv run --locked pytest -q -n auto --dist worksteal -m "not integration and not docker and not internet"
uv run --locked pytest -n auto --dist worksteal --cov=trafficlab --cov-branch \
  --cov-report=term-missing -m "not docker and not internet"
uv run --locked pytest -vv -x -n 0 tests/unit/test_config_validation.py::test_operator_defaults
```

Expected: zero failures and at least 90% branch-aware package coverage.

- [ ] **Step 3: Verify exact Phase 1 behavior**

In a temporary copy of the example configuration, point `run.directory` at a
fresh temporary path and run the installed command. Reparse its snapshot and
assert it equals `load_experiment` output. Instrument `subprocess.Popen` and
`subprocess.run` to fail if called; the config-only integration test must still
pass.

- [ ] **Step 4: Update documentation truthfully**

Mark every Phase 1 deliverable/test checkbox complete only after Steps 1–3 pass.
Change the Roadmap's `(Current)` marker to Phase 2. Ensure `TESTING.md` names the
90% branch-aware threshold already enforced by `pyproject.toml`.

- [ ] **Step 5: Request independent phase review**

Give the reviewer the Phase 1 commit range, this plan, `architecture/SYSTEM.md`,
`architecture/DEVELOPMENT.md`, `architecture/TESTING.md`, and Phase 1 Roadmap
requirements. Fix all Critical and Important findings and rerun Steps 1–3.

- [ ] **Step 6: Commit Phase 1 closure**

```bash
git add architecture/ROADMAP.md architecture/TESTING.md pyproject.toml uv.lock
git commit -m "docs: complete roadmap phase 1"
```

Record the resulting commit as the base for the Phase 2 implementation plan and
proceed automatically without requesting confirmation.
