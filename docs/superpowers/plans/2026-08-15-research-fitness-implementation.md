# Minimum Research Fitness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement every reopened requirement in `architecture/ROADMAP.md`, retain reproducible study evidence,
and pass the Phase 8 acceptance gate without expanding the approved prototype scope.

**Architecture:** Extend the existing one-process pipeline rather than replacing it. First make configuration and
scientific artifacts faithful and compatible, then correct model and genetic semantics, propagate exact identities
and canonical failures through orchestration, and only then collect and audit a replacement validation study.

**Tech Stack:** CPython 3.12, Pydantic 2, standard-library TOML/JSON/hash/path/process APIs, pytest, Ruff, Pyright,
Docker Engine and Compose for capture-only gates, and the existing uv-managed project.

## Global Constraints

- `architecture/` is authoritative; the approved remediation design is
  `docs/superpowers/specs/2026-08-14-research-fitness-remediation-design.md`.
- Keep one Python process and the existing two production capture containers. Add no service, database, queue,
  security subsystem, plug-in framework, Node.js dependency, or speculative infrastructure.
- Ordinary run directories retain exactly the existing nine names. Publication metadata belongs only to an
  accepted evidence bundle under `examples/validation_study/evidence/<study-id>/`.
- Use `SCIENTIFIC_ARTIFACT_SCHEMA_VERSION = 2`. A missing value or any value other than `2` is incompatible before
  generation, resume, or reuse; do not migrate legacy fitted models or checkpoints.
- A portable configuration is fully validated and defaulted while retaining config-relative `run.directory` and
  declared bind-mount host sources. Its realized form changes only those paths to absolute paths.
- All four similarity settings and result records are mandatory. A zero weight changes only aggregate arithmetic;
  execution, validation, diagnostics, fixed shape, and failure behavior remain mandatory.
- Derive family priority exactly once with
  `random.Random(master_seed).sample(sorted_family_names, len(sorted_family_names))`; this temporary RNG never
  consumes the search RNG stream.
- Preserve existing primary-error arbitration and cleanup behavior while adding the canonical failure outcome.
- Use deterministic checked fixtures and predeclared seeds. Ordinary tests never use Docker or the public Internet.
- Run every pytest command through `scripts/run_bounded.sh`; use `uv run --locked` for every Python tool.
- Follow RED, minimal GREEN, focused refactor for each behavior. Commit each task only after focused pytest, Ruff,
  and strict Pyright pass for its change surface.
- After every roadmap phase, obtain independent review and fix all Critical and Important findings before proceeding.
- Keep branch-aware non-Docker package coverage at or above 90%. A function exposed by a failing unit test receives
  100% executable-line and branch coverage.
- Hand-authored edits use `apply_patch`; generated PCAPNG, JSON, CSV, hashes, and lock outputs use their owning tool.
- Docker resources use a unique Compose project name and bounded cleanup. Internet and Docker commands remain serial.

## File Map

| Area | Existing owners and planned additions |
|---|---|
| Configuration | `src/trafficlab/config_io.py`, `preflight.py`, `artifacts.py`, configuration tests |
| Similarity | comparison/evaluation tests; production changes only if RED proves a gap |
| Publication | new `src/trafficlab/study_evidence.py`, `.gitignore`, audit and study scripts/tests |
| Schema/models | scientific schema, registry/checkpoint, MMPP, and fixtures |
| Genetic search | population, operators, strategy, checkpoint, fitting, and genetic tests |
| Capture identity | capture Dockerfile, new image lock, preflight/Docker adapters, Docker tests |
| Diagnostics | `src/trafficlab/errors.py`, stage writers, new checked JSONL fixture |
| Compatibility | new focused `src/trafficlab/compatibility.py`, existing stage artifacts and orchestration |
| Validation study | existing study runner, new offline auditor, accepted evidence, report and examples |
| Acceptance | roadmap/assessment updates only after retained evidence and final independent review |

## Execution Order

Execute tasks in roadmap dependency order: `1, 2, 3, 9, 10, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 16`.
Tasks 9 and 10 close the capture and diagnostic foundation before corrected model semantics begin. Task numbers retain
their stable brief and ledger identities; this ordering rule overrides their document position.

---

### Task 1: Portable and Realized Configuration Pair

**Files:**

- Modify: `src/trafficlab/config_io.py`
- Modify: `src/trafficlab/preflight.py`
- Modify: `src/trafficlab/artifacts.py`
- Test: `tests/unit/test_config_io.py`
- Test: `tests/integration/test_preflight_cli.py`

**Interfaces:**

- Produce `ConfigurationPair(portable: ExperimentConfig, realized: ExperimentConfig)`.
- Produce `load_configuration_pair(path: Path) -> ConfigurationPair` and
  `realize_configuration(portable: ExperimentConfig, config_directory: Path) -> ExperimentConfig`.
- Preserve `load_experiment(path: Path) -> ExperimentConfig` as the realized-form compatibility API.
- Add `portable_config` to `PreparedExperiment`; keep `config` as the realized form.

- [ ] **Step 1: Write relocation and fidelity RED tests**

Create a temporary portable TOML with relative run and mount paths, explicit nondefault argv/environment/seeds,
all operator values, all four method settings, and one zero method weight. Load equivalent copies below two different
roots and assert:

```python
assert first.portable == second.portable
assert first.realized.run.directory != second.realized.run.directory
assert first.realized.target.mounts[0].source != second.realized.target.mounts[0].source
assert non_path_dump(first.portable) == non_path_dump(first.realized)
assert non_path_dump(first.realized) == non_path_dump(second.realized)
```

Parametrize forbidden implicit differences across image, argv, environment, URL, every seed/bound/limit/operator,
and every similarity value. Extend the CLI/Python integration case to compare the realized `experiment.toml` with
the injected API result and to inspect `PreparedExperiment.portable_config` without importing or calling Docker.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_config_io.py \
    tests/integration/test_preflight_cli.py
```

Expected: failures because `ConfigurationPair`, `load_configuration_pair`, and `portable_config` do not exist.

- [ ] **Step 3: Implement the minimal pair**

Parse and validate once into the portable model. Resolve only the two permitted path classes on a deep model dump,
validate that dump into the realized model, and return both immutable values. Do not preserve arbitrary source
formatting and do not create a tenth run artifact. Render the existing `experiment.toml` from `pair.realized`.

```python
@dataclass(frozen=True, slots=True)
class ConfigurationPair:
    portable: ExperimentConfig
    realized: ExperimentConfig
```

- [ ] **Step 4: Run GREEN and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_config_io.py \
    tests/integration/test_preflight_cli.py
uv run --locked ruff check src/trafficlab/config_io.py src/trafficlab/preflight.py \
  src/trafficlab/artifacts.py tests/unit/test_config_io.py tests/integration/test_preflight_cli.py
uv run --locked pyright
```

- [ ] **Step 5: Commit**

```bash
git add src/trafficlab/config_io.py src/trafficlab/preflight.py src/trafficlab/artifacts.py \
  tests/unit/test_config_io.py tests/integration/test_preflight_cli.py
git commit -m "feat: retain portable configuration pair"
```

### Task 2: Mandatory Similarity Weight Evidence

**Files:**

- Test: `tests/unit/test_config_validation.py`
- Test: `tests/unit/test_comparison.py`
- Test: `tests/unit/genetic/test_evaluation.py`
- Test: `tests/integration/test_compare_cli.py`
- Modify only if RED fails semantically: `src/trafficlab/comparison.py`, `src/trafficlab/genetic/evaluation.py`

**Interfaces:** Preserve the current four-result `ComparisonResult` and eager four-method execution.

- [ ] **Step 1: Add direct matrix tests**

Parametrize the four one-hot vectors, representative mixed vectors, and one zero-weight position per method. Spy on
each component function and assert four calls, four diagnostics, fixed JSON shape, and the exact weighted sum. For
each zero-weight method, inject its invalid input or exception and require comparison failure and candidate-invalid
propagation. Parametrize omission of each setting and weight as configuration failure.

- [ ] **Step 2: Run RED or characterization GREEN**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_config_validation.py \
    tests/unit/test_comparison.py tests/unit/genetic/test_evaluation.py \
    tests/integration/test_compare_cli.py
```

Expected: new evidence cases may pass against existing production behavior. Any failure must identify a specific
mandatory-execution gap before production is changed.

- [ ] **Step 3: Make only RED-proven corrections and rerun**

Keep the execution sequence independent of weights:

```python
components = {
    "frame_size_ks": frame_size_ks(...),
    "iat_ks": iat_ks(...),
    "autocorrelation": autocorrelation(...),
    "multiscale_rate": multiscale_rate(...),
}
aggregate = sum(weights[name] * components[name].score for name in REQUIRED_METHODS)
```

Run the focused pytest command, Ruff on touched files, and `uv run --locked pyright`.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_config_validation.py tests/unit/test_comparison.py \
  tests/unit/genetic/test_evaluation.py tests/integration/test_compare_cli.py \
  src/trafficlab/comparison.py src/trafficlab/genetic/evaluation.py
git commit -m "test: prove mandatory similarity execution"
```

### Task 3: Audit-Gated Accepted Evidence Namespace

**Files:**

- Create: `src/trafficlab/study_evidence.py`
- Modify: `.gitignore`
- Test: `tests/unit/test_study_evidence.py`
- Test: `tests/unit/test_readme.py`

**Interfaces:**

```python
type BundleAudit = Callable[[Path], None]


def publish_accepted_bundle(
    candidate: Path,
    evidence_root: Path,
    study_id: str,
    audit: BundleAudit,
) -> Path: ...
```

- [ ] **Step 1: Write publication RED tests**

Require a single safe study-ID path component, a regular candidate directory, an audit call before destination
creation, cleanup of a temporary sibling after audit/copy/fsync/rename failure, and exclusive no-replace rename.
Test failed audit publishes nothing; occupied ID remains byte-for-byte unchanged; success appears only at
`evidence_root/study_id`. Check that ordinary `runs/`, `.study-work/`, and evidence candidates are ignored while a
completed accepted directory is trackable.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 1m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_study_evidence.py tests/unit/test_readme.py
```

Expected: import failure for `trafficlab.study_evidence`.

- [ ] **Step 3: Implement exclusive local publication**

Use standard-library path, copy, fsync, and atomic rename primitives. The audit receives the candidate and raises on
rejection. Never overwrite, merge, fetch missing bytes, or infer audit success from a hash-only record. Add narrow
ignore rules for candidate/temp paths without ignoring accepted study-ID directories.

- [ ] **Step 4: Run GREEN, Ruff, Pyright, and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 1m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_study_evidence.py tests/unit/test_readme.py
uv run --locked ruff check src/trafficlab/study_evidence.py tests/unit/test_study_evidence.py
uv run --locked pyright
git add .gitignore src/trafficlab/study_evidence.py tests/unit/test_study_evidence.py tests/unit/test_readme.py
git commit -m "feat: gate accepted evidence publication"
```

### Task 4: Global Scientific Artifact Schema

**Files:**

- Create: `src/trafficlab/scientific_schema.py`
- Modify: `src/trafficlab/models/registry.py`
- Modify: `src/trafficlab/genetic/checkpoint.py`
- Modify: `src/trafficlab/fitting.py`
- Modify: `src/trafficlab/generation.py`
- Test: `tests/unit/models/test_registry.py`
- Test: `tests/unit/genetic/test_checkpoint.py`
- Test: `tests/integration/test_generate_cli.py`
- Modify generated fixture: `examples/data/fit/checkpoint.json`
- Create generated fixture: `examples/data/fit/best_model.json`

**Interfaces:**

```python
SCIENTIFIC_ARTIFACT_SCHEMA_VERSION: Final = 2


def require_current_scientific_schema(value: object, *, artifact: str) -> None: ...
```

Every `best_model.json`, checkpoint compatibility record, and checkpoint state requires
`scientific_artifact_schema: 2`.

- [ ] **Step 1: Write schema RED tests**

Test current-schema round trips and reject missing, `1`, `3`, boolean, string, and nonintegral values as incompatible
before model generation, checkpoint RNG restoration, or reused-stage work. Distinguish a well-formed old schema from
corrupt JSON in the diagnostic text.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/models/test_registry.py \
    tests/unit/genetic/test_checkpoint.py tests/integration/test_generate_cli.py
```

- [ ] **Step 3: Add the required field and fail before reuse**

Centralize the version and validator in `scientific_schema.py`; do not give serialized Pydantic fields a permissive
default. Thread the exact field through fitting and checkpoint construction. Validate before decoding RNG state or
calling a model generator. Regenerate the small canonical fixtures through owning serializers.

- [ ] **Step 4: Run GREEN/static checks and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/models/test_registry.py \
    tests/unit/genetic/test_checkpoint.py tests/integration/test_generate_cli.py
uv run --locked ruff check src/trafficlab/scientific_schema.py src/trafficlab/models/registry.py \
  src/trafficlab/genetic/checkpoint.py src/trafficlab/fitting.py src/trafficlab/generation.py
uv run --locked pyright
git add src/trafficlab/scientific_schema.py src/trafficlab/models/registry.py \
  src/trafficlab/genetic/checkpoint.py src/trafficlab/fitting.py src/trafficlab/generation.py \
  tests/unit/models/test_registry.py tests/unit/genetic/test_checkpoint.py \
  tests/integration/test_generate_cli.py examples/data/fit/checkpoint.json examples/data/fit/best_model.json
git commit -m "feat: version scientific artifacts"
```

### Task 5: Correct MMPP Arrival-Epoch Generation

**Files:**

- Modify: `src/trafficlab/models/mmpp.py`
- Test: `tests/unit/models/test_mmpp.py`

**Interfaces:** Add an internal typed `_arrival_epoch_probabilities(...) -> tuple[float, float]`; keep serialized model
parameters unchanged apart from Task 4's global schema.

- [ ] **Step 1: Write mathematical and RNG RED tests**

For `q01=1`, `q10=3`, `lambda0=1`, `lambda1=9`, require `pi=(3/4, 1/4)` and
`a=(1/4, 3/4)`. Assert an initial uniform draw exactly at `a0` selects regime 1, the first conditioned arrival is at
`t=0`, and subsequent draws remain mark, arrival clock, transition clock. Add finite normalization cases near the
largest accepted rates and verify exact-clock ties still choose a transition.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 1m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/models/test_mmpp.py
```

Expected: initial-state cases reveal the current arbitrary-time `pi0` threshold.

- [ ] **Step 3: Implement stable rate-weighted normalization**

Compute log weights `log(q10) + log(lambda0)` and `log(q01) + log(lambda1)`, subtract their maximum, exponentiate,
and normalize. Draw the initial regime from `a0`, emit the existing conditioned mark at zero, and leave every later
draw and guard in its existing order.

- [ ] **Step 4: Run GREEN/static checks and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 1m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/models/test_mmpp.py
uv run --locked ruff check src/trafficlab/models/mmpp.py tests/unit/models/test_mmpp.py
uv run --locked pyright
git add src/trafficlab/models/mmpp.py tests/unit/models/test_mmpp.py
git commit -m "fix: initialize MMPP at arrival epochs"
```

### Task 6: Bounded Direct Model Validation and Current-Schema Fixtures

**Files:**

- Create: `tests/scientific/test_model_validation.py`
- Create: `tests/scientific/oracles.py`
- Modify: model fixture files under `examples/data/fit/`
- Test: `tests/integration/test_generate_cli.py`

**Interfaces:** Test-only oracles may parse public model output but must not call production estimators or similarity
functions to calculate their own expected result.

- [ ] **Step 1: Add the predeclared validation matrix**

Use Poisson seeds `1103, 2207, 3301, 4409`, Markov Renewal seeds `5101, 5209, 5303, 5413`, and MMPP seeds
`7103, 7207, 7309, 7411`. Predeclare sample/window sizes and tolerances as named constants before sampling. Cover
Poisson exponential quantiles/rate/completion/joint marks; Markov transition probabilities, occupancy, conditional
holding, both fallbacks, completion, and marks; and MMPP arrival mixture, mean rate, time occupancy, serial dependence,
completion, marks, threshold order, and finite normalization. Use binomial/mean standard-error bounds fixed at six
standard errors plus a small deterministic rounding allowance rather than output-fitted tolerances.

- [ ] **Step 2: Run RED and classify only genuine defects**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 3m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/scientific/test_model_validation.py \
    tests/integration/test_generate_cli.py
```

Expected: fixture/schema omissions fail; statistical cases either pass deterministically or identify a production
defect that receives direct branch-complete regression coverage before correction.

- [ ] **Step 3: Check deterministic all-family artifact round trips**

For each family, fit or load a current-schema checked model, generate canonical events and PCAPNG at the stored `W`,
reload both, and reproduce byte-identical output with the same fixed seed. Generate fixture bytes with production
serializers; expected statistics remain test-only calculations.

- [ ] **Step 4: Run GREEN, the fast model scope, Ruff, Pyright, and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 4m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/scientific tests/unit/models \
    tests/integration/test_generate_cli.py
uv run --locked ruff check tests/scientific tests/integration/test_generate_cli.py
uv run --locked pyright
git add tests/scientific tests/integration/test_generate_cli.py examples/data/fit
git commit -m "test: validate traffic model science"
```

### Task 7: Neutral Family Priority and Population Ordering

**Files:**

- Modify: `src/trafficlab/genetic/types.py`
- Modify: `src/trafficlab/genetic/population.py`
- Modify: `src/trafficlab/genetic/operators.py`
- Test: `tests/unit/genetic/test_population.py`
- Test: `tests/unit/genetic/test_operators.py`

**Interfaces:**

```python
type FamilyPriority = tuple[str, ...]


def derive_family_priority(master_seed: int, family_names: Iterable[str]) -> FamilyPriority: ...
def family_quotas(population_size: int, family_priority: FamilyPriority) -> dict[str, int]: ...
```

The shared comparator uses fitness first, retained priority for exact cross-family ties, and stable candidate ID only
inside one family.

- [ ] **Step 1: Replace obsolete lexical tests with explicit-priority RED tests**

Exercise each family at each priority position, quota remainders, initial slots, valid equal-fitness ties, symmetric
invalids, tournaments, global elites, champions, and same-family stable-ID ties. Retain the zero-retry repaired
cross-family child whose source did not survive and assert its exhaustion diagnostic.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/genetic/test_population.py \
    tests/unit/genetic/test_operators.py
```

- [ ] **Step 3: Thread one validated priority through pure population operations**

Reject duplicates, missing enabled families, and foreign names in the priority tuple. Remove input-order and lexical
scientific tie behavior; lexical ordering remains allowed only in display tables.

- [ ] **Step 4: Run GREEN/static checks and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/genetic/test_population.py \
    tests/unit/genetic/test_operators.py
uv run --locked ruff check src/trafficlab/genetic/types.py src/trafficlab/genetic/population.py \
  src/trafficlab/genetic/operators.py tests/unit/genetic/test_population.py tests/unit/genetic/test_operators.py
uv run --locked pyright
git add src/trafficlab/genetic/types.py src/trafficlab/genetic/population.py \
  src/trafficlab/genetic/operators.py tests/unit/genetic/test_population.py tests/unit/genetic/test_operators.py
git commit -m "feat: neutralize family population order"
```

### Task 8: Priority-Aware Search, Checkpoint, and Exact Resume

**Files:**

- Modify: `src/trafficlab/genetic/strategy.py`
- Modify: `src/trafficlab/genetic/checkpoint.py`
- Modify: `src/trafficlab/fitting.py`
- Test: `tests/unit/genetic/test_strategy.py`
- Test: `tests/unit/genetic/test_checkpoint.py`
- Test: `tests/integration/test_genetic_fitting.py`
- Regenerate: `examples/data/fit/checkpoint.json`, dependent history/winner fixtures

**Interfaces:** Retain `family_priority` in checkpoint compatibility and state and expose it in the fit result used by
final publication.

- [ ] **Step 1: Write derivation, permutation, and resume RED tests**

Require the exact temporary `Random(master_seed).sample(sorted_names, len(sorted_names))` result, unchanged first
search-RNG draw, registry/config permutations with identical priority/populations/children/winner, seeds `4, 0, 6`
covering each priority leader, equal ties, symmetric invalids, and a controlled unique winner. Interrupt after a
generation and compare repaired genes, child IDs, history, RNG state, priority, and winner with uninterrupted search.
Alter schema, CPython patch, priority, bounds/operators, seeds, and similarity settings one at a time and require the
first mismatch before any reproduction draw.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 4m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/genetic/test_strategy.py \
    tests/unit/genetic/test_checkpoint.py tests/integration/test_genetic_fitting.py
```

- [ ] **Step 3: Derive once, persist, validate, and reuse**

Construct the temporary priority RNG separately from the dedicated search RNG. Pass the immutable tuple into every
Task 7 operation and final winner reevaluation. On resume, validate compatibility and priority before decoding or
restoring random state. Regenerate canonical fixture files through owning serializers.

- [ ] **Step 4: Run GREEN/static checks and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 4m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/genetic tests/integration/test_genetic_fitting.py
uv run --locked ruff check src/trafficlab/genetic src/trafficlab/fitting.py tests/unit/genetic \
  tests/integration/test_genetic_fitting.py
uv run --locked pyright
git add src/trafficlab/genetic src/trafficlab/fitting.py tests/unit/genetic \
  tests/integration/test_genetic_fitting.py examples/data/fit
git commit -m "feat: retain neutral family priority"
```

### Task 9: Reproducible Capture Image and Environment Identity

**Files:**

- Modify: `docker/capture/Dockerfile`
- Create generated lock: `docker/capture/image-lock.json`
- Modify: `src/trafficlab/docker_cli.py`
- Modify: `src/trafficlab/preflight.py`
- Modify: `tests/docker/support.py`
- Test: `tests/unit/test_docker_preflight.py`
- Test: `tests/unit/test_docker_fixture_support.py`
- Test: `tests/integration/test_full_preflight.py`
- Test: `tests/docker/test_capture_docker.py`

**Interfaces:** The canonical image lock records base reference/digest, Debian snapshot timestamp, every direct package
version, capture-tool version, and expected resolved capture-image content ID. Full preflight returns target and
capture references plus resolved content IDs.

- [ ] **Step 1: Write offline lock and injected-preflight RED tests**

Strictly reject unknown/missing lock fields, tag-only bases, live apt sources, unversioned direct packages, malformed
digests, expected/resolved mismatch, target mismatch, unavailable snapshot/package, and silent lock rewriting. Inject
Docker command results so ordinary tests require no daemon.

- [ ] **Step 2: Pin primary inputs**

Resolve the approved Debian tag's exact OCI digest from the official registry, direct package versions from one dated
`snapshot.debian.org` state, and the capture tool version from that package. Put only those exact values in the
Dockerfile and canonical lock. Build with network only in the bounded Docker step, inspect the resulting content ID,
write it once as the expected ID, rebuild without changing inputs, and require the same resolved ID.

- [ ] **Step 3: Run unit/in-process GREEN and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 3m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_docker_preflight.py \
    tests/unit/test_docker_fixture_support.py tests/integration/test_full_preflight.py
uv run --locked ruff check src/trafficlab/docker_cli.py src/trafficlab/preflight.py \
  tests/docker/support.py tests/unit/test_docker_preflight.py tests/unit/test_docker_fixture_support.py \
  tests/integration/test_full_preflight.py
uv run --locked pyright
```

- [ ] **Step 4: Run bounded serial Docker identity proof**

Use a unique `COMPOSE_PROJECT_NAME`, a hard wall-clock bound, and a trap that runs labelled `docker compose down -v
--remove-orphans` plus labelled-resource inspection. Run only the capture-image build/preflight/capture node IDs and
record the expected/resolved IDs in test output.

- [ ] **Step 5: Commit**

```bash
git add docker/capture/Dockerfile docker/capture/image-lock.json src/trafficlab/docker_cli.py \
  src/trafficlab/preflight.py tests/docker/support.py tests/unit/test_docker_preflight.py \
  tests/unit/test_docker_fixture_support.py tests/integration/test_full_preflight.py \
  tests/docker/test_capture_docker.py
git commit -m "feat: pin capture environment identity"
```

### Task 10: Canonical Failure Outcomes

**Files:**

- Modify: `src/trafficlab/errors.py`
- Modify: `src/trafficlab/preflight.py`
- Modify: `src/trafficlab/capture.py`
- Modify: `src/trafficlab/fitting.py`
- Modify: `src/trafficlab/generation.py`
- Modify: `src/trafficlab/comparison.py`
- Modify: `src/trafficlab/run.py`
- Create fixture: `tests/fixtures/canonical_adverse_conditions.jsonl`
- Create test: `tests/unit/test_failure_outcomes.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class FailureOutcome:
    kind: str
    stage: str
    detail: str
    affected_evidence: str
    evidence_state: Literal["not_published", "diagnostic_only", "preserved", "possibly_remaining"]
    corrective_action: str
    authority: Literal["primary", "secondary"]
    status: int | str | None = None
```

- [ ] **Step 1: Write a table-driven boundary RED matrix**

Cover configuration/path, Docker/preflight, external exit/timeout/interruption/malformed output, missing/changed/
foreign/corrupt artifact, incompatible schema, metric/sample/numeric infeasibility, generation guard/deadline,
publication, cleanup, and combined failures. Assert every exact field, nonpublication, preserved earlier outputs, and
unchanged established primary/secondary arbitration. Parse the checked JSONL fixture offline and reproduce outcomes.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 3m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_failure_outcomes.py \
    tests/unit/test_run.py
```

- [ ] **Step 3: Add one immutable value, not a workflow layer**

Attach or render `FailureOutcome` at existing failure boundaries while retaining current exception types, exit codes,
human messages, log destinations, event arbitration, and cleanup. Candidate-invalid history records use equivalent
scientific fields rather than a second vocabulary.

- [ ] **Step 4: Run GREEN, existing failure scopes, static checks, and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 4m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_failure_outcomes.py tests/unit/test_run.py \
    tests/unit/test_fitting.py tests/unit/test_comparison.py
uv run --locked ruff check src/trafficlab tests/unit/test_failure_outcomes.py
uv run --locked pyright
git add src/trafficlab/errors.py src/trafficlab/preflight.py src/trafficlab/capture.py \
  src/trafficlab/fitting.py src/trafficlab/generation.py src/trafficlab/comparison.py src/trafficlab/run.py \
  tests/fixtures/canonical_adverse_conditions.jsonl tests/unit/test_failure_outcomes.py tests/unit/test_run.py \
  tests/unit/test_fitting.py tests/unit/test_comparison.py
git commit -m "feat: standardize failure evidence"
```

### Task 11: Stage Compatibility, Artifact Identity, and Lineage

**Files:**

- Create: `src/trafficlab/compatibility.py`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `src/trafficlab/models/registry.py`
- Modify: `src/trafficlab/genetic/checkpoint.py`
- Modify: `src/trafficlab/fitting.py`
- Modify: `src/trafficlab/generation.py`
- Modify: `src/trafficlab/comparison.py`
- Modify: `src/trafficlab/run.py`
- Test: `tests/unit/test_compatibility.py`
- Test: `tests/unit/test_artifacts.py`
- Test: `tests/integration/test_run_pipeline.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ContentIdentity:
    size: int
    sha256: str


def identify_file(path: Path) -> ContentIdentity: ...
def require_compatible(expected: Mapping[str, object], actual: Mapping[str, object]) -> None: ...
```

Mappings use a declared ordered field tuple per architecture matrix so the raised diagnostic always names the first
incompatible field.

- [ ] **Step 1: Write exact positive and first-mismatch RED matrices**

Cover portable transfer, capture reuse, fit resume, generate reuse, compare reuse, and offline reconstruction. Positive
transfer varies only checkout, run-directory, host mount-source absolute paths, permitted Docker/Compose patch, and
kernel observations after feature preflight. Negative cases independently alter host architecture, target/capture ID,
capture tool, mounted-input hash, realized scientific value, source artifact identity, schema, CPython patch,
family/genes/operators/seeds/limits/similarity settings, generated bytes, and lineage.

- [ ] **Step 2: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 4m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_compatibility.py \
    tests/unit/test_artifacts.py tests/integration/test_run_pipeline.py
```

- [ ] **Step 3: Add canonical identities within existing artifacts**

Hash authoritative bytes and retain the minimum lineage fields inside existing checkpoint, best-model, and similarity
JSON structures. Recompute/compare deterministic generated PCAPNG rather than creating a sidecar. Capture reuse
requires exact realized snapshot bytes, capture identity, and both capture files. Add no tenth ordinary artifact and
no general lineage graph.

- [ ] **Step 4: Run GREEN/static checks and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 4m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_compatibility.py tests/unit/test_artifacts.py \
    tests/integration/test_run_pipeline.py
uv run --locked ruff check src/trafficlab/compatibility.py src/trafficlab/artifacts.py \
  src/trafficlab/models/registry.py src/trafficlab/genetic/checkpoint.py src/trafficlab/fitting.py \
  src/trafficlab/generation.py src/trafficlab/comparison.py src/trafficlab/run.py tests/unit/test_compatibility.py
uv run --locked pyright
git add src/trafficlab tests/unit/test_compatibility.py tests/unit/test_artifacts.py \
  tests/integration/test_run_pipeline.py
git commit -m "feat: validate stage lineage"
```

### Task 12: Full-Pipeline Resume Equivalence and Adverse Reconstruction

**Files:**

- Modify: `tests/integration/test_run_pipeline.py`
- Modify: `tests/integration/test_genetic_fitting.py`
- Create: `tests/integration/test_pipeline_equivalence.py`
- Modify: `tests/unit/test_failure_outcomes.py`
- Modify fixture: `tests/fixtures/canonical_adverse_conditions.jsonl`

**Interfaces:** No new production API unless a RED test exposes an uninjectable stage boundary; prefer existing
in-process `fit -> generate -> compare` functions over subprocesses.

- [ ] **Step 1: Add uninterrupted/resumed final-publication comparison**

Run the same checked reference and realized configuration once uninterrupted and once interrupted at a declared
checkpoint boundary. Compare configuration pair, priority, checkpoint continuation, best model, generated PCAPNG,
all four similarity diagnostics, identities, lineage, and final nine-file inventory. Permit only predeclared
wall-clock, log timestamp, process-status, and absolute operational-path differences.

- [ ] **Step 2: Add every adverse reconstruction case**

Recreate each checked JSONL failure with injected local fixtures, including missing, corrupt, changed, foreign, stale,
and incompatible schema at capture, fit, generate, compare, and publication. Assert the first mismatch and exact
evidence state before reuse or publication.

- [ ] **Step 3: Run the bounded integration matrix**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 6m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_pipeline_equivalence.py \
    tests/integration/test_run_pipeline.py tests/integration/test_genetic_fitting.py \
    tests/unit/test_failure_outcomes.py
```

Fix only RED-proven production defects, then rerun Ruff and Pyright.

- [ ] **Step 4: Commit and complete Phase 6 review**

```bash
git add src/trafficlab tests/integration/test_pipeline_equivalence.py \
  tests/integration/test_run_pipeline.py tests/integration/test_genetic_fitting.py \
  tests/unit/test_failure_outcomes.py tests/fixtures/canonical_adverse_conditions.jsonl
git commit -m "test: prove resumed pipeline equivalence"
```

Request independent review covering reopened Phases 1 through 6 and fix all Critical and Important findings before
collecting external evidence.

### Task 13: Bounded Offline Study Auditor

**Files:**

- Create: `scripts/audit_validation_study.py`
- Create: `scripts/generate_validation_study_fixture.py`
- Modify: `scripts/run_validation_study.py`
- Modify: `tests/unit/test_validation_study.py`
- Modify: `tests/integration/test_validation_study_pipeline.py`
- Create fixture tree: `tests/fixtures/validation_study_candidate/`
- Modify: `README.md`
- Modify: `examples/validation_study/README.md`
- Modify: `examples/validation_study/REPORT.md`

**Interfaces:**

```python
def audit_bundle(bundle: Path, *, repository: Path) -> AuditResult: ...
def write_manifest(candidate: Path, ownership: Mapping[str, str], lineage: Mapping[str, object]) -> Path: ...
```

The script exits `0` only after full reconstruction and nonzero with one canonical `FailureOutcome` otherwise.

- [ ] **Step 1: Write clean relocated audit RED tests**

Copy the complete credential-free candidate fixture to a temporary relocated repository. Block socket creation,
Docker subprocesses, network clients, high-level `trafficlab run`, and reads outside the clone/bundle. Require exact
inventory and hashes, strict parsing of TOML/JSON/CSV/PCAPNG and canonical JSONL, independent normalized `W`,
checkpoint/history/winner consistency, deterministic trace regeneration, all four component and aggregate scores,
nine training and fresh-simulation records, three independent fixed-model heldout bundles, report arithmetic, and
every lineage edge.

- [ ] **Step 2: Add representative rejection and collision tests**

Remove one byte owner, corrupt canonical bytes, substitute a valid foreign artifact, alter one manifest owner/lineage,
and occupy the accepted study ID. Require the canonical first mismatch, no publication, and byte-identical preservation
of the occupied bundle.

- [ ] **Step 3: Run RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_validation_study.py \
    tests/integration/test_validation_study_pipeline.py
```

- [ ] **Step 4: Implement manifest, reconstruction, and publisher integration**

The canonical manifest lists each relative regular-file path, byte size, SHA-256, logical owner, and lineage relation
in strict UTF-8-byte order. Reuse strict production parsers/public scientific functions where they own the public
format, but recompute report values rather than trusting them. Generate the credential-free fixture through owning
production APIs, with immutable image identities and nonzero source commit/tree identities; `--check` must reproduce
its exact bytes. Call Task 3 publication only with `audit_bundle` as its audit callback.

- [ ] **Step 5: Run GREEN/static checks and commit**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_validation_study.py \
    tests/integration/test_validation_study_pipeline.py
uv run --locked ruff check scripts/audit_validation_study.py scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
uv run --locked pyright
git add scripts/audit_validation_study.py scripts/run_validation_study.py tests/unit/test_validation_study.py \
  scripts/generate_validation_study_fixture.py tests/integration/test_validation_study_pipeline.py \
  tests/fixtures/validation_study_candidate README.md examples/validation_study/README.md \
  examples/validation_study/REPORT.md
git commit -m "feat: audit retained study evidence"
```

### Task 14: Freeze and Execute the Replacement Validation Study

**Files:**

- Modify: portable configurations under `examples/validation_study/`
- Replace generated accepted bundle: `examples/validation_study/evidence/<new-study-id>/`
- Modify generated report inputs/results under the accepted bundle
- Modify: `examples/validation_study/REPORT.md`
- Modify: `examples/validation_study/README.md`

**Interfaces:** Use the existing three approved workload shapes. Training and selection consume training references
only; each workload receives one independent held-out reference after protocol freeze.

- [ ] **Step 1: Freeze protocol and clean implementation revision**

Commit all Tasks 1 through 13 and their review fixes. Record source commit/tree, `uv.lock` SHA-256, exact CPython
patch, schema `2`, image lock, workload configurations, capture repetitions, GA budgets, all seeds, final simulation
seed, held-out timing, tolerances, report formulas, and URL/transfer headers before collecting results.

- [ ] **Step 2: Run same-revision prerequisites**

Run locked sync/lock, focused fast gates, the pinned capture-image rebuild/identity test, controlled Docker capture,
and opt-in credential-free Internet smoke serially under resource guards. Retain stdout, stderr, exit status, JUnit,
resolved identities, environment record, transfer header, and external observation in the candidate evidence tree.

- [ ] **Step 3: Collect training and natural variation evidence**

For each approved workload, make the predeclared repeated primary captures, run all three families with common windows
and seeds, use the fresh simulation seed on the training-selected winner, and retain every report-cited strict
nine-file tree. Record component scores, winner, runtime, run-to-run variation, one-factor weight cases, and invalid
chromosome diagnostics.

- [ ] **Step 4: Collect genuine held-out evidence**

After training protocol and selections are frozen, capture one new independent reference per workload. Load each
fixed training-selected model, generate over held-out `W` with the predeclared final seed, and compare without refit,
family reselection, seed choice, or protocol amendment. Report held-out results separately.

- [ ] **Step 5: Interpret, audit, and publish**

Document trace inspection and major metric disagreements plus finite-sample, model, metric, and generalization limits.
Generate the exact manifest, run the offline audit from a relocated clean clone with Docker/network blocked, then use
the exclusive publisher. Rerun the audit against the checked destination and reject representative mutations on a
copy. Commit only the accepted bundle and concise report, never scratch/failed runs.

- [ ] **Step 6: Commit retained evidence**

```bash
git add examples/validation_study/evidence examples/validation_study/REPORT.md \
  examples/validation_study/README.md examples/validation_study/*.toml
git commit -m "evidence: publish corrected validation study"
```

### Task 15: Full Verification, Coverage, and Independent Final Review

**Files:** Verification first; fixes may touch only files implicated by fresh failures or review findings.

- [ ] **Step 1: Run the complete non-Docker gate**

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 -m "not docker and not internet"
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 -m "not docker and not internet" \
    --cov=trafficlab --cov-branch --cov-report=term-missing --cov-fail-under=90
```

- [ ] **Step 2: Run available serial external gates**

Run all Docker tests with one unique project scope and bounded cleanup, then the opt-in Internet prerequisite using
the accepted study's exact source/tree/lock/Python/schema inputs. If an external dependency fails, retain diagnostics,
exhaust safe local and primary-source remedies, and use a deterministic substitute only where the architecture permits.

- [ ] **Step 3: Audit from a relocated clean clone**

Clone locally without borrowing untracked bytes, create its locked environment, block Docker/network, and reconstruct
the checked accepted bundle and report. Compare permitted/forbidden portable realization changes and verify the
canonical adverse-condition fixture there.

- [ ] **Step 4: Request independent whole-branch review**

Give the reviewer the implementation base, current head, this plan, authoritative architecture, verification outputs,
coverage report, Docker/Internet evidence, offline-audit output, and deferred-minor ledger. Require zero Critical or
Important findings; fix all such findings in one reviewed wave and rerun every affected gate.

### Task 16: Research Fitness Reassessment and Roadmap Closure

**Files:**

- Modify: `docs/RESEARCH_FITNESS_ASSESSMENT.md`
- Modify: `architecture/ROADMAP.md`
- Modify only if navigation evidence requires it: `architecture/README.md`

- [ ] **Step 1: Reassess the unchanged 17-row rubric**

For criteria `1.5, 1.7, 2.1, 2.2, 2.4, 2.6, 3.3, 3.4, 3.8, 3.9, 4.7, 5.2, 5.3, 5.4, 5.6, 5.7, 5.8`, cite only
fresh checked artifacts, commands, audit results, and review evidence. Preserve the rubric and report the evidence-led
grade; every row must be Acceptable or better before closure.

- [ ] **Step 2: Mark roadmap truthfully**

Check an item only when its implementation and named evidence exist. Check every Phase 1 through 8 Done-when gate only
after the complete gate has passed. Keep dated historical evidence clearly separate from fresh evidence.

- [ ] **Step 3: Verify documentation and final state**

```bash
uv run --locked ruff format --check architecture docs
uv run --locked ruff check .
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 2m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_readme.py
git diff --check
```

Request one final independent evidence/roadmap review after these documentation edits. Fix every Critical or Important
finding and rerun the affected verification.

- [ ] **Step 4: Commit and require a clean tree**

```bash
git add docs/RESEARCH_FITNESS_ASSESSMENT.md architecture/ROADMAP.md architecture/README.md
git commit -m "docs: accept minimum research fitness"
git status --short --branch
```

Expected: all implementation/evidence commits retained locally, every roadmap checkbox accurate, and no uncommitted
or untracked project work.
