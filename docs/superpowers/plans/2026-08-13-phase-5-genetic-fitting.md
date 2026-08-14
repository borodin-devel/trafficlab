# Phase 5 Genetic Fitting and Checkpoint Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement deterministic heterogeneous genetic fitting that fairly evaluates all enabled traffic-model
families, resumes exactly from strict checkpoints, and publishes an independently validated best model.

**Architecture:** Keep genetic ownership in a new `trafficlab.genetic` package: immutable data contracts and
coordinate transforms are pure; population/reproduction own the sole dedicated RNG; evaluation owns
candidate-science classification; checkpoint owns exact persistence; strategy owns the generation lifecycle.
`fitting.py` is the single stage boundary that reads and normalizes inputs once, while the existing registry
remains the source of model-family behavior and `BestModel` remains the final artifact codec.

**Tech Stack:** Python 3.12, standard-library `random.Random`, `math`, `json`, `csv`, and `hashlib`;
Pydantic strict models already used by configuration; pytest/pytest-xdist/pytest-cov; Ruff and strict Pyright;
uv locked environment.

## Global Constraints

- Implement Phase 5 only: no Phase 3, Phase 6, Docker, Internet, registry-extension, worker, database,
  pickle, security, Node.js, or new dependency work.
- Use the existing three-family `trafficlab.models.REGISTRY`; sort enabled family names lexicographically for
  all GA family order, quotas, family history rows, and deterministic tie-adjacent operations.
- Preserve the shared family contract exactly: `repair(genes, bounds, reference)`,
  `fit(reference, genes, *, W, bounds)`, `generate(model, seed, W, limits, *, clock=monotonic)`,
  `dump_fitted`, and `load_fitted`.
- `W` is the one normalized-reference observation window and is identical for every fit, trial generation,
  final validation generation, and all four similarity methods.
- `GeneticConfig.early_stopping_tolerance` is an exact finite `StrictFloat` in `[0, 1]`, defaults to `0.0`;
  a run stagnates when best improvement is `<= tolerance`, resets only on `> tolerance`, and
  `early_stopping_generations == 0` disables early stopping.
- `run.final_seed` is required and must not occur in `genetic.trial_seeds`; selection trials use exactly
  `genetic.trial_seeds`, final validation uses exactly `(run.final_seed,)`, and both use
  `generation.trial` limits in Phase 5.
- A generation count `G` means evaluated generation zero followed by at most `G` reproduced/evaluated
  generations `1..G`; initial population is checkpointed before any reproduction.
- `resume = true` starts fresh when `checkpoint.json` is absent and resumes when it is present;
  `resume = false` rejects an existing checkpoint rather than silently overwriting it.
- Use exactly one dedicated `random.Random(master_seed)` in genetic search. Never use module-global randomness,
  `random.gauss`, parallel evaluation, `pickle`, or a generated worker count.
- Lock RNG primitives and order: continuous init `random()`, inclusive integer init `randrange(L, U + 1)`,
  Bernoulli `random()` with `u < p` even at endpoints, tournament index `randrange(P)`, forced index
  `randrange(d)`, Gaussian `normalvariate(0.0, sigma)`.
- Same-family reproduction draws crossover decision, optional per-gene parent draws, all per-gene mutation
  decisions, selected Gaussian draws, then forced-index/Gaussian only when required; duplicate attempts
  repeat mutation selection and forced draws only.
- Candidate identity is `CandidateId(birth_generation: int, birth_index: int)`, compared lexicographically;
  duplicate identity is same family and exact repaired numeric gene tuple, with no tolerance.
- Validate the shared reference, `W`, family/bounds registry, limits, similarity configuration, and every enabled
  metric once before candidate evaluation; a real reference-versus-reference `compare_traces` call proves common
  metric preconditions before the first candidate.
- Only a `CandidateEvaluationError` explicitly translated at the registered-family repair/fit/generate boundary,
  incomplete-generation boundary, candidate-generated metric-precondition boundary, or nonfinite-score check
  produces fitness `0.0`. Never broadly catch `TrafficlabError`, `ValueError`, or `Exception`; unclassified
  `TrafficlabError`, filesystem/parser/checkpoint failures, and unexpected exceptions abort the stage.
- Checkpoint JSON is canonical, strict, duplicate-free, finite, atomically replaced after a whole evaluated
  generation; checkpoint first, then `ga_history.csv` is derived/repaired atomically.
- Encode `rng.getstate()` losslessly as JSON with engine `python.random.Random/MT19937`, exact Python version,
  state version, MT array, index, and `gauss_next: null`; reject non-null Gaussian cache and restore with
  `setstate()` before reproduction.
- History columns are exactly `generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,`
  `best_birth_generation,best_birth_index`; write lexical family rows then one overall row, and treat invalid
  fitness as zero in the mean.
- The current population stores full candidates; history stores summaries. Trial diagnostics reuse the existing
  recursively immutable `JsonDiagnostics`/`FrozenJsonValue` contract from `similarity.common`; no mutable
  `dict[str, object]` or arbitrary Python object crosses the evaluator/checkpoint boundary.
- `best_model.json` publication is exclusive, except byte-identical validated reuse; it is created only after
  fresh final validation succeeds and that validation never reselects a candidate. Final validation performs one
  deterministic fit; `make_best_model` deliberately repairs/refits once more to construct the artifact because
  neither checkpoints nor `FitOutcome` persist a fitted Python object.
- All source and test lines remain at most 120 characters; public interfaces are typed; use `apply_patch` for
  authored edits and `uv run --locked` for all project commands.
- Every pytest command uses `scripts/run_bounded.sh` with all five named limit flags before
  `-- uv run --locked pytest`.
- Focused tests use `2G/3G/512M`, five-minute wall time, `-n 0`; broad deterministic tests use
  `6G/8G/1G`, ten-minute wall time, exact `-n 4 --dist worksteal`; coverage uses the same broad limits and
  twenty minutes.
- After timeout, interruption, OOM, or a non-completing command, inspect the guard result and process state
  before another pytest command; never overlap test runs.

## File Map

- `architecture/SYSTEM.md`: Clarify Phase 5 fit input, resume, checkpoint, history, and final-validation
  semantics.
- `architecture/TESTING.md`: Add named deterministic Phase 5 unit/integration evidence without changing
  broader test policy.
- `architecture/genetic_models/basic_generational.md`: Align documented tolerance, generation, seed, and
  checkpoint-first details.
- `src/trafficlab/config.py`: Add strict tolerance and cross-section final-seed/trial validation.
- `src/trafficlab/genetic/types.py`: Immutable candidate, trial, diagnostics, population, history, and outcome
  contracts.
- `src/trafficlab/genetic/coordinates.py`: Exact coordinate metadata, transforms, reflection, initialization,
  and RNG primitive helpers.
- `src/trafficlab/genetic/population.py`: Quotas, stable ranking, tournament selection, elites, champions,
  and population validation.
- `src/trafficlab/genetic/operators.py`: Same/different-family reproduction, mutation, repair, duplicate retry,
  and exact draw order.
- `src/trafficlab/genetic/evaluation.py`: Fit-once candidate trials, four-score aggregation, invalid
  classification, and final validation.
- `src/trafficlab/genetic/checkpoint.py`: Strict checkpoint codec, RNG codec, compatibility, atomic JSON,
  and derived CSV publication.
- `src/trafficlab/genetic/strategy.py`: Generation zero, reproduce/evaluate/checkpoint loop, termination,
  resume, and `FitOutcome`.
- `src/trafficlab/fitting.py`: Prepared fit stage, exact input reads/hashes/normalization, strategy invocation,
  winner artifact, and logs.
- `src/trafficlab/artifacts.py`: Reusable atomic exclusive bytes publication helper for checkpoint/history/
  best-model artifacts.
- `src/trafficlab/cli.py`: `trafficlab fit EXPERIMENT` parser, injected fit boundary, success/error output.
- `examples/data/fit/`: Checked-in offline fit input, expected artifacts, nondefault competition, resume, and
  tamper fixtures.
- `scripts/generate_fit_fixtures.py`: Deterministically regenerate fixtures or fail under `--check`.
- `tests/unit/genetic/`: Pure contracts, coordinates, population, operators, evaluation, checkpoint, and
  strategy tests.
- `tests/unit/test_config_schema.py`: Strict tolerance and final-seed/trial configuration tests.
- `tests/unit/test_fitting.py`: Fit stage artifact/input/failure/reuse tests.
- `tests/unit/test_cli.py`: Injected `fit` command CLI tests.
- `tests/integration/test_genetic_fitting.py`: Three-family competition, checkpoint interruption/resume,
  tampering, and offline `fit -> generate -> compare`.
- `tests/unit/test_fit_fixture_generator.py`: Fixture generator `--check` determinism and checked-in-output
  parity.

## Locked Interfaces

The evaluator and checkpoint use the existing `FrozenJsonValue` and `JsonDiagnostics` aliases from
`trafficlab.similarity.common`; `JsonDiagnostics` is a `Mapping[str, FrozenJsonValue]`, where a frozen value is
exactly a `str`, `int`, finite `float`, `bool`, `None`, or a recursively composed tuple/string-keyed mapping of
those values. Construction recursively copies/freezes mappings and sequences and rejects every other type and every
nonfinite float.

```python
type CandidateStatus = Literal["pending", "valid", "invalid"]
type MethodName = Literal["autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate"]
type CandidateFailureKind = Literal[
    "repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score"
]
type DuplicateOutcome = Literal["invalid", "duplicate", "exhausted"]
type TerminalReason = Literal["running", "hard_limit", "early_stop"]

METHOD_ORDER: tuple[MethodName, ...] = ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")


@dataclass(frozen=True, order=True, slots=True)
class CandidateId:
    birth_generation: int
    birth_index: int


@dataclass(frozen=True, slots=True)
class MethodTrialResult:
    name: MethodName
    score: float
    diagnostics: JsonDiagnostics


@dataclass(frozen=True, slots=True)
class TrialResult:
    seed: int
    aggregate_score: float
    methods: tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult]


@dataclass(frozen=True, slots=True)
class CandidateFailure:
    kind: CandidateFailureKind
    seed: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class DuplicateDiagnostic:
    attempt: int
    outcome: DuplicateOutcome
    detail: str


@dataclass(frozen=True, slots=True)
class Candidate:
    identifier: CandidateId
    family: FamilyName
    genes: Genes | None
    status: CandidateStatus
    fitness: float
    trials: tuple[TrialResult, ...]
    invalid: CandidateFailure | None
    duplicate_diagnostics: tuple[DuplicateDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class HistoryRow:
    generation: int
    scope: Literal["family", "overall"]
    family: FamilyName | None
    candidate_count: int
    valid_count: int
    best_fitness: float
    mean_fitness: float
    best_identifier: CandidateId


@dataclass(frozen=True, slots=True)
class FitOutcome:
    winner: Candidate
    final_trials: tuple[TrialResult, ...]
    generation: int
    terminal_reason: Literal["hard_limit", "early_stop"]
```

`CandidateEvaluationError` is a frozen slotted exception with fields
`kind: CandidateFailureKind`, `seed: int | None`, and `detail: str`. The locked callable signatures are:

```text
family_coordinates(name: FamilyName, bounds: FamilyBounds) -> tuple[GeneCoordinate, ...]
initialize_candidate(family: ModelFamily, bounds: FamilyBounds, reference: Sequence[TraceEvent],
                     rng: Random) -> Genes
validate_evaluation_context(context: EvaluationContext) -> ValidatedEvaluationContext
evaluate_candidate(candidate: Candidate, context: ValidatedEvaluationContext) -> Candidate
evaluate_final(candidate: Candidate, context: ValidatedEvaluationContext,
               final_seed: int) -> tuple[TrialResult, ...]
load_checkpoint(path: Path, compatibility: CheckpointCompatibility) -> CheckpointState
publish_checkpoint(path: Path, state: CheckpointState) -> None
run_strategy(context: StrategyContext) -> FitOutcome
fit_experiment(experiment_path: Path) -> FitStageResult
```

---

### Task 1: Lock configuration, owning documents, and fit-stage policy

**Files:**

- Modify: `src/trafficlab/config.py`
- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/TESTING.md`
- Modify: `architecture/genetic_models/basic_generational.md`
- Modify: `tests/unit/test_config_schema.py`
- Modify: `tests/unit/test_config_validation.py`

**Interfaces:**

- Consumes: current immutable `RunConfig`, `GeneticConfig`, `ExperimentConfig`, and deterministic TOML rendering.
- Produces: `GeneticConfig.early_stopping_tolerance: Annotated[StrictFloat, Field(ge=0.0, le=1.0)] = 0.0`;
  `generation_count: NonNegativeInteger`; cross-section rejection when
  `run.final_seed in genetic.trial_seeds`; authoritative prose for lexical order, `G`, resume, validation seeds,
  limits, and checkpoint/history ordering.

- [ ] **Step 1: Write failing strict configuration tests**

```python
def test_genetic_tolerance_defaults_to_exact_zero(valid_experiment: dict[str, object]) -> None:
    config = ExperimentConfig.model_validate(valid_experiment)
    assert config.genetic.early_stopping_tolerance == 0.0
    assert type(config.genetic.early_stopping_tolerance) is float


@pytest.mark.parametrize("value", [True, 1, -0.0001, 1.0001, math.inf, math.nan])
def test_genetic_tolerance_requires_a_finite_exact_float(valid_experiment: dict[str, object], value: object) -> None:
    genetic = dict(cast(dict[str, object], valid_experiment["genetic"]))
    genetic["early_stopping_tolerance"] = value
    with pytest.raises(ValidationError, match="early_stopping_tolerance"):
        ExperimentConfig.model_validate({**valid_experiment, "genetic": genetic})


def test_final_seed_cannot_be_a_selection_trial_seed(valid_experiment: dict[str, object]) -> None:
    run = dict(cast(dict[str, object], valid_experiment["run"]))
    run["final_seed"] = 11
    genetic = dict(cast(dict[str, object], valid_experiment["genetic"]))
    genetic["trial_seeds"] = [7, 11]
    with pytest.raises(ValidationError, match="final seed"):
        ExperimentConfig.model_validate({**valid_experiment, "run": run, "genetic": genetic})


def test_generation_count_zero_means_only_generation_zero(valid_experiment: dict[str, object]) -> None:
    genetic = dict(cast(dict[str, object], valid_experiment["genetic"]))
    genetic["generation_count"] = 0
    config = ExperimentConfig.model_validate({**valid_experiment, "genetic": genetic})
    assert config.genetic.generation_count == 0
```

- [ ] **Step 2: Run the configuration RED test through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/test_config_schema.py tests/unit/test_config_validation.py \
  -k 'tolerance or final_seed or generation_count' -q
```

Expected: failure because the field is absent and final seed can overlap trials.

- [ ] **Step 3: Add the smallest strict model and cross-section checks**

```python
Tolerance = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]


class GeneticConfig(StrictModel):
    # existing required fields stay in their current order
    generation_count: NonNegativeInteger
    early_stopping_tolerance: Tolerance = 0.0


class ExperimentConfig(StrictModel):
    @model_validator(mode="after")
    def cross_section_values_are_compatible(self) -> Self:
        # retain existing checks
        if self.run.final_seed in self.genetic.trial_seeds:
            raise ValueError("final seed must not be one of the genetic trial seeds")
        return self
```

Update the three owning documents with explicit statements rather than inferred behavior:

```text
GA order is the lexical order of enabled family names.
G means evaluated generation zero plus reproductions 1 through G.
resume=true starts without a checkpoint and resumes with one; false rejects one.
Selection uses trial seeds; final validation uses exactly final_seed and trial limits.
Checkpoint publication completes before derived history repair/publication.
```

- [ ] **Step 4: Run GREEN, format, lint, and type checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/test_config_schema.py tests/unit/test_config_validation.py \
  -k 'tolerance or final_seed or generation_count or genetic' -q
uv run --locked ruff format src/trafficlab/config.py tests/unit/test_config_schema.py \
  tests/unit/test_config_validation.py
uv run --locked ruff check src/trafficlab/config.py tests/unit/test_config_schema.py \
  tests/unit/test_config_validation.py
uv run --locked pyright src/trafficlab/config.py tests/unit/test_config_schema.py tests/unit/test_config_validation.py
```

- [ ] **Step 5: Review and commit the configuration contract**

Verify documentation says `<= tolerance` stagnates, `> tolerance` resets, and zero early generations disables stopping.

```bash
git diff --check
git add src/trafficlab/config.py architecture/SYSTEM.md architecture/TESTING.md \
  architecture/genetic_models/basic_generational.md tests/unit/test_config_schema.py \
  tests/unit/test_config_validation.py
git commit -m "feat: define genetic fitting configuration"
```

### Task 2: Build immutable genetic types and exact coordinate machinery

**Files:**

- Create: `src/trafficlab/genetic/__init__.py`
- Create: `src/trafficlab/genetic/types.py`
- Create: `src/trafficlab/genetic/coordinates.py`
- Create: `tests/unit/genetic/__init__.py`
- Create: `tests/unit/genetic/test_types.py`
- Create: `tests/unit/genetic/test_coordinates.py`

**Interfaces:**

- Consumes: `FamilyName`, `FloatBounds`, `IntegerBounds`, `FamilyBounds`, `Genes`, `REGISTRY`, and family `gene_names`.
- Produces: the frozen `CandidateId`, `MethodTrialResult`, `TrialResult`, `CandidateFailure`,
  `DuplicateDiagnostic`, `Candidate`, and `HistoryRow` types locked above; `GeneCoordinate`, `CoordinateKind`,
  `CandidateEvaluationError`; `reflect`, `encode_gene`, `decode_gene`, `family_coordinates`,
  `initialize_candidate`, `bernoulli`, and `mutate_coordinate`.

- [ ] **Step 1: Write failing type and transform examples**

```python
def test_coordinate_metadata_is_exact_for_all_registered_families() -> None:
    assert tuple(item.kind for item in family_coordinates("poisson_empirical", POISSON_BOUNDS)) == ("log",)
    assert tuple(item.kind for item in family_coordinates("markov_renewal", MARKOV_BOUNDS)) == (
        "linear",
        "linear",
        "linear",
        "integer",
        "log",
    )
    assert tuple(item.kind for item in family_coordinates("mmpp", MMPP_BOUNDS)) == ("log", "log", "log", "log")


def test_reflect_and_integer_decode_use_locked_endpoint_rules() -> None:
    integer = GeneCoordinate("r", "integer", IntegerBounds(lower=1, upper=5))
    assert (reflect(-0.2), reflect(1.2), reflect(2.2)) == (0.2, 0.8, 0.2)
    assert decode_gene(integer, 0.125) == 2
    assert decode_gene(integer, 0.875) == 5


def test_initialization_uses_the_documented_rng_primitives() -> None:
    rng = ScriptedRandom(random_values=[0.25], ranges=[3])
    assert initialize_candidate(MARKOV_FAMILY, MARKOV_BOUNDS, REFERENCE, rng) == EXPECTED_REPAIRED
    assert rng.calls == [("random",), ("random",), ("random",), ("randrange", 1, 6), ("random",)]


def test_method_trial_diagnostics_are_recursively_frozen_and_ordered() -> None:
    method = MethodTrialResult("autocorrelation", 1.0, {"nested": [{"value": 1.0}]})
    assert method.name == METHOD_ORDER[0]
    with pytest.raises(TypeError):
        cast(dict[str, object], method.diagnostics)["changed"] = True


@pytest.mark.parametrize("value", [object(), math.inf, math.nan])
def test_method_trial_rejects_nested_non_json_or_nonfinite_diagnostics(value: object) -> None:
    with pytest.raises(ValueError, match="diagnostic"):
        MethodTrialResult("autocorrelation", 1.0, {"nested": {"value": value}})
```

- [ ] **Step 2: Run the coordinate RED test through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_types.py tests/unit/genetic/test_coordinates.py -q
```

Expected: collection fails because `trafficlab.genetic` does not exist.

- [ ] **Step 3: Implement immutable contracts and pure coordinate functions**

```python
type CoordinateKind = Literal["linear", "log", "integer"]


@dataclass(frozen=True, order=True, slots=True)
class CandidateId:
    birth_generation: int
    birth_index: int


@dataclass(frozen=True, slots=True)
class GeneCoordinate:
    name: str
    kind: CoordinateKind
    bounds: FloatBounds | IntegerBounds


@dataclass(frozen=True, slots=True)
class CandidateEvaluationError(Exception):
    kind: CandidateFailureKind
    seed: int | None
    detail: str


def reflect(value: float) -> float:
    remainder = value % 2.0
    return remainder if remainder <= 1.0 else 2.0 - remainder


def bernoulli(rng: Random, probability: float) -> bool:
    return rng.random() < probability
```

Implement exact linear, logarithmic, and integer formulas. Integer decode is
`L + floor(z * (U - L) + 0.5)`. Initialization visits coordinates in family order, uses the locked primitive by
kind, then invokes family repair exactly once without RNG. Catch `TrafficlabError` only around that direct
registered-family repair call and raise `CandidateEvaluationError("repair", None, str(error))`; population code
catches only that typed error and creates the required zero-fitness invalid initial candidate.

- [ ] **Step 4: Add validation and constructor failure tests before extending behavior**

```python
@pytest.mark.parametrize("identifier", [CandidateId(-1, 0), CandidateId(0, -1)])
def test_candidate_id_rejects_negative_components(identifier: CandidateId) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        validate_candidate_id(identifier)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_reflect_rejects_nonfinite_coordinates(value: float) -> None:
    with pytest.raises(TrafficlabError, match="coordinate"):
        reflect(value)
```

- [ ] **Step 5: Run GREEN and targeted branch coverage**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_types.py tests/unit/genetic/test_coordinates.py \
  --cov=trafficlab.genetic.types --cov=trafficlab.genetic.coordinates --cov-branch --cov-report=term-missing -q
uv run --locked ruff format src/trafficlab/genetic tests/unit/genetic
uv run --locked ruff check src/trafficlab/genetic tests/unit/genetic
uv run --locked pyright src/trafficlab/genetic tests/unit/genetic
```

- [ ] **Step 6: Review coordinate semantics and commit**

Check that every continuous MMPP/Poisson coordinate is logarithmic, Markov `r` alone is integer, and endpoint
Bernoulli still calls `random()`.

```bash
git diff --check
git add src/trafficlab/genetic tests/unit/genetic
git commit -m "feat: add genetic coordinate primitives"
```

### Task 3: Implement population construction, selection, and reproduction

**Files:**

- Create: `src/trafficlab/genetic/population.py`
- Create: `src/trafficlab/genetic/operators.py`
- Create: `tests/unit/genetic/test_population.py`
- Create: `tests/unit/genetic/test_operators.py`

**Interfaces:**

- Consumes: Task 2 types/coordinates, registry families, family bounds/operators, and a materialized reference trace.
- Produces: `family_quotas`, `initial_population`, `rank_candidates`, `tournament_select`, `global_elites`,
  `family_champions`, `reproduce_child`, and `fill_next_population`.

- [ ] **Step 1: Write failing quota, rank, champion, and tournament tests**

```python
def test_quotas_assign_remainder_in_lexical_family_order() -> None:
    assert family_quotas(("poisson_empirical", "mmpp", "markov_renewal"), 8) == {
        "markov_renewal": 3,
        "mmpp": 3,
        "poisson_empirical": 2,
    }


def test_global_elites_and_missing_champions_keep_all_families_represented() -> None:
    next_population = retained_population(POPULATION, elite_count=2)
    assert [candidate.identifier for candidate in next_population] == [
        CandidateId(0, 1),
        CandidateId(0, 0),
        CandidateId(0, 4),
    ]
    assert {candidate.family for candidate in next_population} == set(LEXICAL_FAMILIES)


def test_tournament_uses_replacement_and_stable_id_for_equal_fitness() -> None:
    rng = ScriptedRandom(ranges=[1, 0, 1])
    assert tournament_select(POPULATION, tournament_size=3, rng=rng).identifier == CandidateId(0, 0)
    assert rng.calls == [("randrange", 5), ("randrange", 5), ("randrange", 5)]
```

- [ ] **Step 2: Run the population RED test through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_population.py tests/unit/genetic/test_operators.py \
  -k 'quota or tournament or champion or crossover or duplicate' -q
```

Expected: import failures for the two absent modules.

- [ ] **Step 3: Implement deterministic population order and retained slots**

```python
def rank_candidates(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    return tuple(sorted(candidates, key=lambda item: (-item.fitness, item.identifier)))


def retained_population(candidates: Sequence[Candidate], *, elite_count: int) -> tuple[Candidate, ...]:
    elites = list(rank_candidates(candidates)[:elite_count])
    present = {candidate.family for candidate in elites}
    for family in sorted({candidate.family for candidate in candidates} - present):
        elites.append(next(candidate for candidate in rank_candidates(candidates) if candidate.family == family))
    return tuple(elites)
```

Validate fixed `P >= E + F`, contiguous lexical initializer slots, all candidates evaluated before ranking, and
retained IDs unchanged. Use `rng.randrange(P)` once per tournament sample, then rank selected entries by
fitness and ID.

- [ ] **Step 4: Write failing exact reproduction draw-order tests**

```python
def test_same_family_crossover_then_mutation_uses_exact_draw_order() -> None:
    child = reproduce_child(PARENT_A, PARENT_B, context=CONTEXT, identifier=CandidateId(1, 0), rng=SCRIPTED)
    assert child.genes == (EXPECTED_GENE,)
    assert SCRIPTED.calls == [("random",), ("random",), ("random",), ("normalvariate", 0.0, 0.1)]


def test_cross_family_clone_forces_mutation_when_no_gene_selected() -> None:
    child = reproduce_child(POISSON_PARENT, MMPP_PARENT, context=CONTEXT, identifier=CandidateId(1, 3), rng=SCRIPTED)
    assert child.family == "poisson_empirical"
    assert child.duplicate_diagnostics == ()
    assert SCRIPTED.calls[-2:] == [("randrange", 1), ("normalvariate", 0.0, 0.1)]
```

- [ ] **Step 5: Implement same-family, cross-family, and duplicate rules minimally**

```python
def reproduce_child(
    parent_a: Candidate, parent_b: Candidate, *, context: ReproductionContext, identifier: CandidateId, rng: Random
) -> Candidate:
    source = choose_fitter(parent_a, parent_b)
    if parent_a.family == parent_b.family:
        genes = crossover_or_clone(parent_a, parent_b, rng=rng, operators=context.operators_for(source.family))
    else:
        genes = source.genes
    genes = mutate_selected(
        genes, source.family, context=context, rng=rng, force_if_none=parent_a.family != parent_b.family
    )
    return repair_and_retry(genes, source=source, identifier=identifier, context=context, rng=rng)
```

Visit genes only in published order. Same-family `p_c=0` clones the fitter; exact fitness tie chooses lower
`CandidateId`. Different families never cross; clone the fitter then force an index if ordinary mutation selected
none. Mandatory integer mutation uses Gaussian sign, zero positive, and one-step endpoint reflection. Wrap only
the direct registered-family repair call: its `TrafficlabError` becomes
`CandidateEvaluationError("repair", None, detail)`, which creates an invalid candidate with zero fitness; other
exceptions propagate. A valid duplicate retries at most configured count and
records `DuplicateDiagnostic(attempt, outcome, detail)` for invalid/still-duplicate attempts plus a final
`outcome="exhausted"` when the original valid child is retained.

- [ ] **Step 6: Add duplicate edge tests and run GREEN**

```python
def test_zero_duplicate_attempts_retains_source_equal_cross_family_child_with_diagnostic() -> None:
    child = reproduce_child(
        CROSS_FAMILY_PARENTS[0],
        CROSS_FAMILY_PARENTS[1],
        context=ZERO_RETRY_CONTEXT,
        identifier=CandidateId(1, 0),
        rng=SCRIPTED,
    )
    assert child.genes == CROSS_FAMILY_PARENTS[0].genes
    assert child.duplicate_diagnostics == (DuplicateDiagnostic(0, "exhausted", "source-equal child"),)


def test_invalid_duplicate_attempt_keeps_last_valid_base_and_terminates() -> None:
    child = reproduce_child(
        DUPLICATE_PARENTS[0],
        DUPLICATE_PARENTS[1],
        context=TWO_RETRY_CONTEXT,
        identifier=CandidateId(1, 2),
        rng=SCRIPTED,
    )
    assert child.status == "pending"
    assert child.duplicate_diagnostics == (
        DuplicateDiagnostic(1, "invalid", "repair failed"),
        DuplicateDiagnostic(2, "exhausted", "duplicate attempts exhausted"),
    )
```

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_population.py tests/unit/genetic/test_operators.py -q
uv run --locked ruff format src/trafficlab/genetic/population.py src/trafficlab/genetic/operators.py tests/unit/genetic
uv run --locked ruff check src/trafficlab/genetic/population.py src/trafficlab/genetic/operators.py tests/unit/genetic
uv run --locked pyright src/trafficlab/genetic/population.py src/trafficlab/genetic/operators.py tests/unit/genetic
```

- [ ] **Step 7: Review and commit the operator boundary**

Inspect the scripted call lists for endpoint probabilities, all Gaussian signs, tie clone, no elite/champion
draws, and bounded retry behavior.

```bash
git diff --check
git add src/trafficlab/genetic/population.py src/trafficlab/genetic/operators.py \
  tests/unit/genetic/test_population.py tests/unit/genetic/test_operators.py
git commit -m "feat: implement genetic population operators"
```

### Task 4: Evaluate candidates and fresh final validation

**Files:**

- Create: `src/trafficlab/genetic/evaluation.py`
- Create: `tests/unit/genetic/test_evaluation.py`

**Interfaces:**

- Consumes: Task 2 candidate types, registry family/bounds, `GenerationLimits`, `SimilarityConfig`,
  `compare_traces`, reference events, exact `W`, and selection or final seed tuples.
- Produces: `EvaluationContext`, `ValidatedEvaluationContext`, `validate_evaluation_context`,
  `evaluate_candidate`, `evaluate_final`, and exact four-method `TrialResult` values using Task 2's
  `CandidateEvaluationError`.

```python
@dataclass(frozen=True, slots=True)
class EvaluationContext:
    reference: tuple[TraceEvent, ...]
    window: float
    families: Mapping[FamilyName, ModelFamily]
    bounds: Mapping[FamilyName, FamilyBounds]
    trial_seeds: tuple[int, ...]
    trial_limits: GenerationLimits
    similarity: SimilarityConfig


@dataclass(frozen=True, slots=True)
class ValidatedEvaluationContext(EvaluationContext):
    pass
```

Both mapping fields are copied into `MappingProxyType` in `__post_init__`; the validated subclass is constructed
only by `validate_evaluation_context` after every common check and self-comparison succeeds.

- [ ] **Step 1: Write failing evaluation tests using real family and comparison paths**

```python
def test_evaluation_fits_once_and_gives_each_trial_the_same_window_and_limits() -> None:
    validated = validate_evaluation_context(CONTEXT)
    evaluated = evaluate_candidate(PENDING_POISSON, validated)
    assert evaluated.status == "valid"
    assert [trial.seed for trial in evaluated.trials] == [7, 9]
    assert FAMILY.fit_calls == 1
    assert FAMILY.generate_calls == [(7, W, TRIAL_LIMITS), (9, W, TRIAL_LIMITS)]
    assert all(tuple(method.name for method in trial.methods) == METHOD_ORDER for trial in evaluated.trials)
    assert evaluated.fitness == math.fsum((0.75, 0.75)) / 2.0


def test_common_metric_precondition_failure_aborts_before_candidate_loop() -> None:
    with pytest.raises(TrafficlabError, match="autocorrelation"):
        validate_evaluation_context(CONTEXT_WITH_REFERENCE_TOO_SHORT_FOR_CONFIGURED_LAG)
    assert FAMILY.fit_calls == 0


def test_incomplete_generation_is_invalid_candidate_not_infrastructure_abort() -> None:
    evaluated = evaluate_candidate(PENDING_POISSON, validate_evaluation_context(INCOMPLETE_CONTEXT))
    assert evaluated.status == "invalid"
    assert evaluated.fitness == 0.0
    assert evaluated.invalid == CandidateFailure("incomplete_generation", 7, "max_packets")


def test_final_validation_uses_only_the_fresh_final_seed_and_is_stage_fatal_when_incomplete() -> None:
    with pytest.raises(TrafficlabError, match="final validation"):
        evaluate_final(VALID_CANDIDATE, validate_evaluation_context(FINAL_INCOMPLETE_CONTEXT), final_seed=101)


def test_final_validation_refits_once_and_returns_no_fitted_python_state() -> None:
    trials = evaluate_final(VALID_CANDIDATE, VALIDATED_CONTEXT, final_seed=101)
    assert FAMILY.fit_calls == 1
    assert FAMILY.generate_calls == [(101, W, TRIAL_LIMITS)]
    assert tuple(trial.seed for trial in trials) == (101,)
```

- [ ] **Step 2: Run the evaluation RED test through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_evaluation.py -q
```

Expected: collection fails because the evaluation module is absent.

- [ ] **Step 3: Validate common context once and implement narrow candidate translation**

```python
def validate_evaluation_context(context: EvaluationContext) -> ValidatedEvaluationContext:
    validate_fit_inputs(context.reference, W=context.window)
    validate_registered_families_bounds_limits_and_seeds(context)
    compare_traces(context.reference, context.reference, context.window, context.similarity)
    return ValidatedEvaluationContext.from_context(context)


def evaluate_candidate(candidate: Candidate, context: ValidatedEvaluationContext) -> Candidate:
    try:
        model = _fit_candidate(candidate, context)
        trials = tuple(_evaluate_trial(model, seed, context) for seed in context.trial_seeds)
    except CandidateEvaluationError as error:
        return invalid_candidate(candidate, CandidateFailure(error.kind, error.seed, error.detail))
    return replace(
        candidate,
        status="valid",
        fitness=math.fsum(trial.aggregate_score for trial in trials) / len(trials),
        trials=trials,
    )
```

`validate_evaluation_context` runs exactly once in `run_strategy`, before initialization/evaluation, and its real
self-comparison error is stage-fatal. `_repair_candidate`, `_fit_candidate`, and `_generate_candidate` each wrap
only their single call to an exact object from `REGISTRY`; only a `TrafficlabError` raised by that direct call is
translated to `CandidateEvaluationError` with kind `repair`, `fit`, or `generation`. An incomplete
`GenerationResult` is explicitly translated as `incomplete_generation`. Before comparison,
`validate_candidate_similarity_preconditions` checks the generated trace against the four configured methods'
published minimum samples/lags and raises `CandidateEvaluationError("similarity_precondition", seed, detail)`.
Then call real `compare_traces` without a catch. Because the shared reference/config self-comparison already passed,
any later `TrafficlabError` from `compare_traces` is unclassified evaluator failure and aborts the stage.

Build methods by iterating `METHOD_ORDER`, taking `ComparisonResult.methods[name]`, and constructing
`MethodTrialResult(name, score, diagnostics)`. The constructor reuses
`SimilarityResult(score, diagnostics).diagnostics` to validate and recursively freeze the existing JSON contract.
Require all method and aggregate scores to be exact finite floats in `[0, 1]`; explicit failure is
`CandidateEvaluationError("nonfinite_score", seed, detail)`. No outer block catches `TrafficlabError`,
`ValueError`, `TypeError`, or `Exception`, so an injected unclassified `TrafficlabError` and every unexpected
exception abort immediately. `evaluate_final` deterministically calls the same registered family `fit` once, then
one `_evaluate_trial` with `(final_seed,)` and trial limits; it translates only `CandidateEvaluationError` into a
stage-fatal `TrafficlabError` prefixed `final validation`, while an unclassified failure propagates unchanged.

- [ ] **Step 4: Add a fsum and nonfinite-score RED regression**

```python
def test_fitness_uses_math_fsum_and_rejects_nonfinite_component_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evaluation, "compare_traces", lambda *_args: comparison_with_scores((1.0, math.nan, 1.0, 1.0)))
    evaluated = evaluate_candidate(PENDING_POISSON, VALIDATED_CONTEXT)
    assert evaluated.status == "invalid"
    assert evaluated.fitness == 0.0


def test_unclassified_trafficlab_error_and_unexpected_exception_abort() -> None:
    for error in (TrafficlabError("injected", corrective_action="stop"), RuntimeError("injected")):
        with pytest.raises(type(error), match="injected"):
            evaluate_candidate(PENDING_POISSON, context_with_unclassified_evaluator_error(error))
```

- [ ] **Step 5: Run GREEN, static checks, and branch coverage**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_evaluation.py \
  --cov=trafficlab.genetic.evaluation --cov-branch --cov-report=term-missing -q
uv run --locked ruff format src/trafficlab/genetic/evaluation.py tests/unit/genetic/test_evaluation.py
uv run --locked ruff check src/trafficlab/genetic/evaluation.py tests/unit/genetic/test_evaluation.py
uv run --locked pyright src/trafficlab/genetic/evaluation.py tests/unit/genetic/test_evaluation.py
```

- [ ] **Step 6: Review and commit evaluation**

Confirm context validation happens once before the candidate loop, only the six named candidate categories become
zero, final validation refits the selected winner once and is stage-fatal, and no broad exception handler exists.

```bash
git diff --check
git add src/trafficlab/genetic/evaluation.py tests/unit/genetic/test_evaluation.py
git commit -m "feat: evaluate genetic candidates"
```

### Task 5: Persist strict checkpoint state and derived history atomically

**Files:**

- Create: `src/trafficlab/genetic/checkpoint.py`
- Create: `tests/unit/genetic/test_checkpoint.py`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `tests/unit/test_artifacts.py`

**Interfaces:**

- Consumes: Task 2 candidate/history types, configuration, exact `experiment.toml` bytes, registry metadata,
  `Random.getstate`, and run directory.
- Produces: frozen `FamilyCheckpointSpec`, `GeneticCheckpointSettings`, `RngState`,
  `CheckpointCompatibility`, and `CheckpointState`; `encode_rng_state`, `decode_rng_state`, `render_checkpoint`,
  `parse_checkpoint`, `publish_checkpoint`, `load_checkpoint`, `publish_generation`, and `publish_history_csv`.
  `render_history_csv` is the pure byte projection used by both publication and stale-file repair.

- [ ] **Step 1: Write failing strict JSON, RNG, and atomic-order tests**

```python
def test_rng_state_round_trip_reproduces_all_next_primitives() -> None:
    rng = Random(73)
    _ = (rng.random(), rng.randrange(9), rng.normalvariate(0.0, 0.1))
    restored = decode_rng_state(encode_rng_state(rng.getstate()))
    clone = Random()
    clone.setstate(restored)
    assert (clone.random(), clone.randrange(9), clone.normalvariate(0.0, 0.1)) == (
        rng.random(),
        rng.randrange(9),
        rng.normalvariate(0.0, 0.1),
    )


def test_checkpoint_rejects_non_null_gaussian_cache_and_duplicate_candidate_ids() -> None:
    with pytest.raises(TrafficlabError, match="gauss_next"):
        parse_checkpoint(replace_json_field(VALID_BYTES, ("rng", "gauss_next"), 0.5), COMPATIBILITY)
    with pytest.raises(TrafficlabError, match="duplicate candidate"):
        parse_checkpoint(DUPLICATE_ID_BYTES, COMPATIBILITY)


@pytest.mark.parametrize(
    "content",
    [NESTED_UNKNOWN_KEY_BYTES, NESTED_WRONG_SHAPE_BYTES, NESTED_NONFINITE_BYTES, BOOL_AS_SCORE_BYTES],
)
def test_checkpoint_rejects_nested_shape_type_and_number_errors(content: bytes) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(content, COMPATIBILITY)


def test_checkpoint_round_trip_preserves_frozen_nested_method_diagnostics() -> None:
    loaded = parse_checkpoint(render_checkpoint(VALID_STATE), COMPATIBILITY)
    assert loaded == VALID_STATE
    assert tuple(method.name for method in loaded.population[0].trials[0].methods) == METHOD_ORDER


def test_checkpoint_publishes_before_derived_history_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(checkpoint, "atomic_replace", lambda path, content: calls.append(path.name))
    publish_generation(tmp_path, VALID_STATE)
    assert calls == ["checkpoint.json", "ga_history.csv"]
```

- [ ] **Step 2: Run the checkpoint RED test through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_checkpoint.py tests/unit/test_artifacts.py -q
```

Expected: missing checkpoint symbols and no generic reusable atomic bytes primitive.

- [ ] **Step 3: Implement one validated exclusive/replace publication primitive**

```python
def atomic_replace(path: Path, content: bytes, *, validator: Callable[[bytes], None]) -> None:
    temporary = _write_fsync_temp_sibling(path, content)
    try:
        validator(temporary.read_bytes())
        os.replace(temporary, path)
    finally:
        _unlink_owned_temp(temporary)
```

Use replace only for checkpoint/history. Keep `best_model.json` exclusive in Task 7. Encode checkpoint JSON with
sorted compact keys and newline; reject unknown/missing keys, bool-as-int, nonfinite floats, duplicate JSON keys
via `object_pairs_hook`, duplicate candidate IDs, duplicate family names, duplicate seeds, out-of-order lexical
family metadata, and malformed diagnostics. JSON parsing recursively accepts only object string keys, arrays,
strings, exact integers, exact finite floats, booleans where the named field permits them, and null where the named
field permits it. A boolean never satisfies an integer or float field. Every object at every depth uses exact-key
validation; do not silently discard an unknown nested field.

`publish_checkpoint` may replace an existing canonical path only while `run_strategy` is advancing a fresh active
fit or a checkpoint loaded under `resume=true`. `resume=false` rejects an existing checkpoint before this helper is
called; there is no public checkpoint-replacement or reset command.

- [ ] **Step 4: Implement checkpoint data and compatibility validation**

```python
@dataclass(frozen=True, slots=True)
class FamilyCheckpointSpec:
    name: FamilyName
    gene_order: tuple[str, ...]
    coordinates: tuple[GeneCoordinate, ...]
    crossover_probability: float
    mutation_probability: float
    mutation_scale: float


@dataclass(frozen=True, slots=True)
class GeneticCheckpointSettings:
    master_seed: int
    final_seed: int
    population_size: int
    generation_count: int
    tournament_size: int
    elite_count: int
    duplicate_mutation_attempts: int
    early_stopping_generations: int
    early_stopping_tolerance: float
    resume: bool


@dataclass(frozen=True, slots=True)
class RngState:
    state_version: int
    mt_state: tuple[int, ...]
    index: int
    gauss_next: None


@dataclass(frozen=True, slots=True)
class CheckpointCompatibility:
    experiment_sha256: str
    reference_sha256: str
    capture_sha256: str
    observation_window_seconds: float
    trial_seeds: tuple[int, ...]
    families: tuple[FamilyCheckpointSpec, ...]
    genetic: GeneticCheckpointSettings
    similarity: SimilarityConfig
    python_version: str
    rng_engine: Literal["python.random.Random/MT19937"]


@dataclass(frozen=True, slots=True)
class CheckpointState:
    compatibility: CheckpointCompatibility
    generation: int
    population: tuple[Candidate, ...]
    history: tuple[HistoryRow, ...]
    rng_state: RngState
    best_identifier: CandidateId
    best_fitness: float
    consecutive_stagnation: int
    terminal_reason: TerminalReason


def load_checkpoint(path: Path, compatibility: CheckpointCompatibility) -> CheckpointState:
    state = parse_checkpoint(path.read_bytes(), compatibility)
    validate_compatibility(state.compatibility, compatibility)
    return state
```

The root checkpoint object has exactly these keys and shapes; it has no schema/version field:

```text
experiment_sha256: 64-character lowercase hex string
reference_sha256: 64-character lowercase hex string
capture_sha256: 64-character lowercase hex string
observation_window_seconds: finite positive float
trial_seeds: nonempty unique array of exact nonnegative integers
families: lexically ordered array of family objects
genetic: exact genetic-settings object
similarity: exact SimilarityConfig object
rng: {engine, python_version, state: {state_version, mt_state, index, gauss_next}}
generation: exact nonnegative integer
population: fixed-size array of evaluated candidate objects
history: ordered array of history-row objects through generation
best: {identifier, fitness}
consecutive_stagnation: exact nonnegative integer
terminal_reason: "running" | "hard_limit" | "early_stop"
```

A family object has exactly `name,gene_order,coordinates,operators`; each coordinate has exactly
`name,kind,lower,upper`, preserving an exact integer bound only for integer coordinates and finite float bounds for
continuous coordinates. `operators` has exactly `crossover_probability,mutation_probability,mutation_scale`.
`genetic` has exactly the ten `GeneticCheckpointSettings` field names above. `similarity` has exactly
`iat_diagnostic_quantile,acf_lags,acf_lag_weights,acf_iat_weight,acf_size_weight,`
`multiscale_widths_seconds,multiscale_scale_weights,multiscale_packet_weight,multiscale_byte_weight,`
`max_direction_bin_cells,method_weights`; `method_weights` has exactly the four current method names. Parse it with
strict `SimilarityConfig.model_validate` and require equality to the effective configuration.

An evaluated candidate object has exactly
`identifier,family,genes,status,fitness,trials,invalid,duplicate_diagnostics`. Identifier is exactly a two-integer
array. Status is only `valid` or `invalid` in a checkpoint; pending candidates are never persisted. Genes are a
canonical numeric array for valid/repaired candidates and null only when repair produced no canonical tuple.
Fitness is a finite float in `[0,1]`, exactly `0.0` for invalid status. A trial has exactly
`seed,aggregate_score,methods`; methods are exactly four objects in `METHOD_ORDER`, each with exactly
`name,score,diagnostics`. Diagnostics recursively obey `FrozenJsonValue`. Invalid is null for valid status or exactly
`{kind,seed,detail}` for invalid status. A duplicate diagnostic has exactly `attempt,outcome,detail`.
Every method score and aggregate is an exact finite float in `[0,1]`; recompute each aggregate with `math.fsum`
and the stored full similarity method weights. Candidate fitness equals `math.fsum(trial aggregates) / trial count`.

A history row has exactly `generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,`
`best_identifier`. Family is an exact enabled name for `scope="family"` and null only for `scope="overall"`.
Rows are generations ascending, each with lexical family rows followed by one overall row. `valid_count` counts only
status `valid`; means include every candidate, so invalid fitness contributes zero; best uses descending fitness and
lexicographically smallest ID even when every candidate is invalid.

`render_history_csv` writes the locked header, decimal integers, and Python `repr` of validated finite floats with
`csv.writer(..., lineterminator="\n")`. Family rows use `scope=family` and the exact family name; the overall row
uses `scope=overall` and an empty family cell. It reparses every scalar and reconstructs the exact `HistoryRow`
sequence before publication.

The RNG state is a lossless decomposition of `Random.getstate()`: state version exact integer, exactly 624 MT words
as exact integers, index exact integer in `0..624`, and `gauss_next` null. Restore the tuple exactly with
`setstate()`. Store `platform.python_version()` as the exact Python version. Compare the exact experiment snapshot
SHA-256 first. Only after it matches, compare input hashes/W,
trial seeds, lexical family names, each family gene order/kinds/bounds/operators, genetic values, full similarity
settings/weights, exact Python version, and engine with field-specific errors. In particular, report
`operator values for family NAME` before any reproduction when only redundant operator state was tampered.

Strict state invariants require generation in `0..G`, exactly `population_size` evaluated candidates with unique
IDs and all configured trial seeds for valid status, one history block for every generation `0..generation`, and a
last block equal to a fresh summary of the current population. `best.identifier` must occur in the current population
and its candidate fitness must equal `best.fitness`. `hard_limit` requires `generation == G`; `early_stop` requires
`generation < G`, a positive early-stop limit, and a counter at least that limit; `running` requires neither terminal
condition. Reject a terminal/counter/best/history inconsistency before CSV repair or RNG restoration.

- [ ] **Step 5: Add CSV exactness and resume rejection tests**

```python
def test_history_rows_have_exact_header_lexical_family_rows_then_overall(tmp_path: Path) -> None:
    publish_history_csv(tmp_path / "ga_history.csv", VALID_STATE)
    assert (tmp_path / "ga_history.csv").read_text() == (
        "generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,"
        "best_birth_generation,best_birth_index\n" + EXPECTED_ROWS
    )


def test_operator_mismatch_rejects_before_rng_reproduction(tmp_path: Path) -> None:
    (tmp_path / "checkpoint.json").write_bytes(VALID_BYTES_WITH_CHANGED_MMMP_OPERATOR)
    with pytest.raises(TrafficlabError, match="operator values for family mmpp"):
        load_checkpoint(tmp_path / "checkpoint.json", COMPATIBILITY)


def test_experiment_hash_mismatch_precedes_redundant_operator_mismatch() -> None:
    content = change_experiment_hash_and_mmpp_operator(VALID_BYTES)
    with pytest.raises(TrafficlabError, match="experiment snapshot SHA-256"):
        parse_checkpoint(content, COMPATIBILITY)


def test_resume_repairs_missing_or_stale_history_only_from_authoritative_checkpoint(tmp_path: Path) -> None:
    for existing in (None, b"stale\n"):
        restore_checkpoint_and_optional_history(tmp_path, checkpoint=VALID_BYTES, history=existing)
        before = (tmp_path / "checkpoint.json").read_bytes()
        state = load_generation(tmp_path, COMPATIBILITY)
        assert (tmp_path / "checkpoint.json").read_bytes() == before
        assert (tmp_path / "ga_history.csv").read_bytes() == render_history_csv(state)
```

- [ ] **Step 6: Run GREEN and targeted coverage**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_checkpoint.py tests/unit/test_artifacts.py \
  --cov=trafficlab.genetic.checkpoint --cov=trafficlab.artifacts --cov-branch --cov-report=term-missing -q
uv run --locked ruff format src/trafficlab/genetic/checkpoint.py src/trafficlab/artifacts.py \
  tests/unit/genetic/test_checkpoint.py tests/unit/test_artifacts.py
uv run --locked ruff check src/trafficlab/genetic/checkpoint.py src/trafficlab/artifacts.py \
  tests/unit/genetic/test_checkpoint.py tests/unit/test_artifacts.py
uv run --locked pyright src/trafficlab/genetic/checkpoint.py src/trafficlab/artifacts.py \
  tests/unit/genetic/test_checkpoint.py tests/unit/test_artifacts.py
```

- [ ] **Step 7: Review and commit checkpoint persistence**

Verify that history is reconstructible from checkpoint and its repair failure cannot erase a valid checkpoint.

```bash
git diff --check
git add src/trafficlab/genetic/checkpoint.py src/trafficlab/artifacts.py \
  tests/unit/genetic/test_checkpoint.py tests/unit/test_artifacts.py
git commit -m "feat: checkpoint genetic search state"
```

### Task 6: Orchestrate generations, termination, resume, and final outcome

**Files:**

- Create: `src/trafficlab/genetic/strategy.py`
- Create: `tests/unit/genetic/test_strategy.py`

**Interfaces:**

- Consumes: Tasks 3–5 constructors/reproduction/evaluation/checkpoint, `ExperimentConfig`, prepared reference,
  W, bounds, and run directory.
- Produces: `StrategyContext`, `FitOutcome`, `run_strategy`, `initialize_or_resume`, `should_stop_early`, and
  `advance_termination_state`; `FitOutcome` contains no fitted model.

```python
@dataclass(frozen=True, slots=True)
class StrategyContext:
    config: ExperimentConfig
    evaluation: EvaluationContext
    compatibility: CheckpointCompatibility
    run_directory: Path
```

Task 6 defines `make_strategy_context(config: ExperimentConfig, reference: tuple[TraceEvent, ...], window: float,
run_directory: Path, *, experiment_sha256: str, reference_sha256: str,
capture_sha256: str) -> StrategyContext`. It lexically resolves enabled families, exact bounds/operators,
coordinates, full genetic/similarity settings, Python version, and engine once.

- [ ] **Step 1: Write failing lifecycle and tolerance tests**

```python
def test_generation_zero_is_evaluated_and_checkpointed_before_first_reproduction() -> None:
    outcome = run_strategy(CONTEXT_WITH_G=1)
    assert EVENT_LOG[:3] == ["initialize", "evaluate:0", "checkpoint:0"]
    assert EVENT_LOG[3:] == ["reproduce:1", "evaluate:1", "checkpoint:1", "final:1"]
    assert outcome.generation == 1


def test_generation_count_zero_checkpoints_hard_terminal_generation_zero() -> None:
    outcome = run_strategy(CONTEXT_WITH_G=0)
    state = load_checkpoint(CHECKPOINT_PATH, COMPATIBILITY)
    assert (outcome.generation, state.generation, state.terminal_reason) == (0, 0, "hard_limit")
    assert not any(event.startswith("reproduce:") for event in EVENT_LOG)


def test_early_stop_counts_consecutive_improvements_not_greater_than_tolerance() -> None:
    assert stop_generation([0.1, 0.11, 0.12], tolerance=0.01, limit=2) == 2
    assert stop_generation([0.1, 0.11, 0.1200001], tolerance=0.01, limit=2) is None


def test_small_improvement_updates_winner_but_increments_stagnation() -> None:
    state = finish_scores(previous_best=0.50, current_best=0.505, tolerance=0.01)
    assert state.best_fitness == 0.505
    assert state.consecutive_stagnation == 1


def test_resume_flag_absent_checkpoint_starts_fresh_and_false_rejects_present_checkpoint(tmp_path: Path) -> None:
    assert initialize_or_resume(FRESH_CONTEXT(tmp_path, resume=True)).generation == 0
    write_checkpoint(tmp_path / "checkpoint.json")
    with pytest.raises(TrafficlabError, match="resume"):
        initialize_or_resume(FRESH_CONTEXT(tmp_path, resume=False))


def test_resume_immediately_before_and_at_early_stop_preserves_counter_and_boundary(tmp_path: Path) -> None:
    before = resume_from_checkpoint(tmp_path, generation=1, consecutive_stagnation=1, terminal_reason="running")
    assert before.terminal_reason == "early_stop"
    at_stop = resume_from_checkpoint(tmp_path, generation=2, consecutive_stagnation=2, terminal_reason="early_stop")
    assert at_stop.terminal_reason == "early_stop"
    assert reproduction_calls(at_stop) == []
```

- [ ] **Step 2: Run the strategy RED test through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_strategy.py -q
```

Expected: import failure for the absent strategy module.

- [ ] **Step 3: Implement the generation state machine**

```python
def run_strategy(context: StrategyContext) -> FitOutcome:
    evaluation = validate_evaluation_context(context.evaluation)
    state = initialize_or_resume(context)
    if state is None:
        population, rng = initialize_population(context)
        state = finish_evaluated_generation(
            population, rng, generation=0, previous=None, context=context, evaluation=evaluation
        )
        publish_generation(context.run_directory, state)
    while state.terminal_reason == "running":
        state = reproduce_then_evaluate(state, context, evaluation=evaluation, generation=state.generation + 1)
        publish_generation(context.run_directory, state)
    winner = candidate_by_id(state.population, state.best_identifier)
    final_trials = evaluate_final(winner, evaluation, context.final_seed)
    return FitOutcome(winner, final_trials, state.generation, state.terminal_reason)
```

`finish_evaluated_generation` first evaluates the complete current population, then appends that generation's
lexical-family/overall history rows, then updates retained best ID/fitness and consecutive stagnation. Generation
zero establishes retained best and counter `0`. At later generations, update retained best whenever stable ranking
finds a better candidate, including an improvement no larger than tolerance. Reset the counter only when the current
generation best fitness exceeds the prior retained-best fitness by more than `early_stopping_tolerance`; otherwise
increment it. Assign `hard_limit` when `generation == G`; otherwise assign `early_stop` when the positive configured
stagnation limit is reached; otherwise assign `running`. Thus a simultaneous hard/early boundary records
`hard_limit`, and early stop never extends `G`.

`publish_generation` atomically replaces authoritative `checkpoint.json` with this fully updated state, then derives
and atomically replaces `ga_history.csv`. Loading an existing checkpoint validates it and repairs a missing/stale CSV
before any RNG restoration or reproduction. Do not re-evaluate its selection population. Restore decoded RNG state
before the first tournament. `generation_count=G` represents generations `0..G` and never creates `G+1`.

A terminal checkpoint re-entry validates compatibility and repairs history, but performs no initialization,
reproduction, selection, history append, RNG draw, or checkpoint rewrite. It retrieves the winner by stored
`best_identifier`, deterministically repeats final validation, and returns a fresh `FitOutcome`. Final scores never
replace that winner. Final validation failure remains stage-fatal and leaves checkpoint/history scientifically
unchanged.

- [ ] **Step 4: Add exact uninterrupted-versus-resumed identity test**

```python
def test_resume_matches_uninterrupted_population_history_winner_and_rng_state(tmp_path: Path) -> None:
    uninterrupted = run_strategy(CONTEXT(tmp_path / "full", generation_count=2))
    interrupted = run_until_checkpoint(CONTEXT(tmp_path / "resume", generation_count=2), stop_after_generation=1)
    resumed = run_strategy(CONTEXT(tmp_path / "resume", generation_count=2, resume=True))
    assert resumed == uninterrupted
    assert read_checkpoint(tmp_path / "resume") == read_checkpoint(tmp_path / "full")
    assert read_history(tmp_path / "resume") == read_history(tmp_path / "full")


def test_terminal_reentry_only_final_validates_stored_winner() -> None:
    first = run_strategy(TERMINAL_RESUME_CONTEXT)
    reset_spies()
    second = run_strategy(TERMINAL_RESUME_CONTEXT)
    assert second == first
    assert EVENTS == ["load", "repair_history", "final_fit", "final_generate", "final_compare"]
    assert CHECKPOINT_BYTES_AFTER == CHECKPOINT_BYTES_BEFORE
```

- [ ] **Step 5: Run GREEN, static checks, and branch coverage**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic/test_strategy.py \
  --cov=trafficlab.genetic.strategy --cov-branch --cov-report=term-missing -q
uv run --locked ruff format src/trafficlab/genetic/strategy.py tests/unit/genetic/test_strategy.py
uv run --locked ruff check src/trafficlab/genetic/strategy.py tests/unit/genetic/test_strategy.py
uv run --locked pyright src/trafficlab/genetic/strategy.py tests/unit/genetic/test_strategy.py
```

- [ ] **Step 6: Review and commit strategy**

Check zero-generation hard termination, update-before-checkpoint ordering, fresh final-seed-only evaluation,
resume on both sides of early stop, terminal re-entry without reselection/reproduction, and RNG restoration before
reproduction.

```bash
git diff --check
git add src/trafficlab/genetic/strategy.py tests/unit/genetic/test_strategy.py
git commit -m "feat: run resumable genetic strategy"
```

### Task 7: Wire the prepared fit stage, best-model artifact, run log, and CLI

**Files:**

- Create: `src/trafficlab/fitting.py`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `src/trafficlab/cli.py`
- Create: `tests/unit/test_fitting.py`
- Create: `tests/unit/test_cli.py`

**Interfaces:**

- Consumes: `open_or_prepare_experiment`, reference/capture PCAPNG parsers, `normalize_reference`, SHA helpers,
  registry `make_best_model`/`render_best_model`/`load_best_model`, Tasks 4–6, and `append_run_log`.
- Produces: `FitStageResult`, `fit_experiment(experiment_path: Path) -> FitStageResult`, strict best-model
  exclusive/reuse publication, `read_fit_input(path: Path) -> bytes`, and CLI command `fit`.

```python
@dataclass(frozen=True, slots=True)
class FitStageResult:
    experiment_path: Path
    run_directory: Path
    best_model_path: Path
    best_model: BestModel
    outcome: FitOutcome
    observation_window_seconds: float
    reused_best_model: bool


@dataclass(frozen=True, slots=True)
class BestModelPublication:
    path: Path
    content: bytes
    created_by_call: bool


@dataclass(frozen=True, slots=True)
class FitDependencies:
    open_or_prepare: Callable[[Path], PreparedExperiment]
    read_bytes: Callable[[Path], bytes]
    strategy: Callable[[StrategyContext], FitOutcome]

    @classmethod
    def production(cls) -> Self:
        return cls(open_or_prepare_experiment, read_fit_input, run_strategy)
```

Task 7 defines
`publish_best_model(path: Path, content: bytes) -> BestModelPublication`; it validates `content` with
`load_best_model` before exclusive publication and validates an existing artifact before byte-identical reuse.

- [ ] **Step 1: Write failing prepared-input and artifact tests**

```python
def test_fit_reads_each_input_once_hashes_exact_bytes_and_passes_one_normalized_window(tmp_path: Path) -> None:
    result = fit_experiment(EXPERIMENT, dependencies=DEPENDENCIES)
    assert result.observation_window_seconds == 2.0
    assert READS == ["experiment.toml", "capture.json", "reference.pcapng"]
    assert STRATEGY_CONTEXT.window == 2.0
    assert result.best_model_path == tmp_path / "run" / "best_model.json"


def test_best_model_is_exclusive_except_validated_identical_reuse(tmp_path: Path) -> None:
    publish_best_model(tmp_path, VALID_BEST_BYTES)
    assert publish_best_model(tmp_path, VALID_BEST_BYTES).created_by_call is False
    with pytest.raises(TrafficlabError, match="best_model already exists"):
        publish_best_model(tmp_path, OTHER_VALID_BEST_BYTES)


def test_final_validation_fit_precedes_one_publication_refit_and_uses_same_genes() -> None:
    result = fit_experiment(EXPERIMENT, dependencies=COUNTED_DEPENDENCIES)
    assert FIT_EVENTS == [
        ("final_validation", result.outcome.winner.genes),
        ("make_best_model", result.outcome.winner.genes),
    ]
    assert result.best_model.genes == result.outcome.winner.genes
    assert result.best_model.fitted == EXPECTED_FITTED_FOR_WINNER


def test_final_validation_failure_publishes_no_best_model(tmp_path: Path) -> None:
    with pytest.raises(TrafficlabError, match="final validation"):
        fit_experiment(EXPERIMENT, dependencies=FINAL_FAILURE_DEPENDENCIES)
    assert not (tmp_path / "run" / "best_model.json").exists()


def test_cli_fit_uses_injected_boundary_and_reports_result(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["fit", "experiment.toml"], fit=lambda _path: FIT_RESULT) == 0
    assert "fit: family=mmpp" in capsys.readouterr().out
```

- [ ] **Step 2: Run the stage and CLI RED tests through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/test_fitting.py tests/unit/test_cli.py -q
```

Expected: missing `trafficlab.fitting` and absent `fit` CLI parser command.

- [ ] **Step 3: Implement the one-read fit pipeline and exclusive artifact publication**

```python
def fit_experiment(experiment_path: Path, *, dependencies: FitDependencies | None = None) -> FitStageResult:
    active = dependencies or FitDependencies.production()
    prepared = active.open_or_prepare(experiment_path)
    snapshot_bytes = active.read_bytes(prepared.run_directory / "experiment.toml")
    capture_bytes = active.read_bytes(prepared.run_directory / "capture.json")
    reference_bytes = active.read_bytes(prepared.run_directory / "reference.pcapng")
    metadata = parse_capture_metadata(capture_bytes, source=prepared.run_directory / "capture.json")
    reference_events = parse_pcapng_bytes(reference_bytes, metadata, source=prepared.run_directory / "reference.pcapng")
    reference, window = normalize_reference(reference_events)
    context = make_strategy_context(
        prepared.config,
        reference,
        window,
        prepared.run_directory,
        experiment_sha256=sha256_bytes(snapshot_bytes),
        reference_sha256=sha256_bytes(reference_bytes),
        capture_sha256=sha256_bytes(capture_bytes),
    )
    outcome = active.strategy(context)
    winner_family = get_family(outcome.winner.family)
    winner_bounds = getattr(prepared.config.models, outcome.winner.family)
    if winner_bounds is None or outcome.winner.genes is None:
        raise AssertionError("validated winner must have configured bounds and canonical genes")
    best = make_best_model(
        family=winner_family,
        reference=reference,
        genes=outcome.winner.genes,
        reference_sha256=context.compatibility.reference_sha256,
        capture_sha256=context.compatibility.capture_sha256,
        W=window,
        bounds=winner_bounds,
    )
    publication = publish_best_model(prepared.run_directory / "best_model.json", render_best_model(best))
    return FitStageResult(
        experiment_path,
        prepared.run_directory,
        publication.path,
        best,
        outcome,
        window,
        not publication.created_by_call,
    )
```

The strategy's final validation deterministically fits the stored winner once and returns only method evidence.
After it succeeds, the existing `make_best_model` API deliberately repairs and fits the same winner once more to
construct self-contained artifact state. There is no fitted cache, fitted checkpoint payload, or fitted member on
`FitOutcome`. This makes exactly two post-selection fits: validation, then artifact construction. Never read
reference/capture twice, never recompute W per trial, and append deterministic fit start/checkpoint/final/published
or failure log records only after their real events.

- [ ] **Step 4: Add failure-policy and terminal-checkpoint reuse tests**

```python
def test_parser_and_checkpoint_errors_abort_fit_instead_of_becoming_invalid_candidates() -> None:
    with pytest.raises(TrafficlabError, match="reference"):
        fit_experiment(EXPERIMENT, dependencies=BAD_REFERENCE_DEPENDENCIES)
    with pytest.raises(TrafficlabError, match="checkpoint"):
        fit_experiment(EXPERIMENT, dependencies=BAD_CHECKPOINT_DEPENDENCIES)


def test_terminal_checkpoint_is_validated_before_identical_best_model_reuse(tmp_path: Path) -> None:
    write_terminal_checkpoint_and_matching_best_model(tmp_path)
    result = fit_experiment(EXPERIMENT, dependencies=TERMINAL_DEPENDENCIES)
    assert result.reused_best_model is True
    assert EVENTS == ["load_checkpoint", "repair_history", "final_validation", "make_best_model", "reuse"]


@pytest.mark.parametrize(
    "change",
    ["experiment_settings", "reference_hash", "capture_hash", "window", "bounds", "checkpoint", "final_seed"],
)
def test_existing_best_model_never_bypasses_terminal_checkpoint_compatibility(change: str) -> None:
    with pytest.raises(TrafficlabError, match=EXPECTED_COMPATIBILITY_ERROR[change]):
        fit_experiment(changed_terminal_run(change))
```

Always enter `run_strategy`, even when `best_model.json` exists. Only a compatible terminal checkpoint may reach
final validation and prospective artifact construction. Render and validate the prospective `BestModel`, then
exclusively publish it or reuse an existing artifact only when `load_best_model(existing_bytes)` succeeds and its
canonical bytes are exactly the prospective bytes. A different existing artifact is preserved and rejected. The
experiment hash comparison occurs first; redundant input hashes/W, family bounds/operators/settings, checkpoint
state, and final seed are then validated at their owning boundaries. A terminal invocation reruns deterministic
final validation and the intentional `make_best_model` refit before byte comparison.

- [ ] **Step 5: Run GREEN and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/test_fitting.py tests/unit/test_cli.py -q
uv run --locked ruff format src/trafficlab/fitting.py src/trafficlab/artifacts.py src/trafficlab/cli.py \
  tests/unit/test_fitting.py tests/unit/test_cli.py
uv run --locked ruff check src/trafficlab/fitting.py src/trafficlab/artifacts.py src/trafficlab/cli.py \
  tests/unit/test_fitting.py tests/unit/test_cli.py
uv run --locked pyright src/trafficlab/fitting.py src/trafficlab/artifacts.py src/trafficlab/cli.py \
  tests/unit/test_fitting.py tests/unit/test_cli.py
```

- [ ] **Step 6: Review and commit the fit boundary**

Confirm CLI returns stage-formatted `TrafficlabError`, no pre-strategy model reuse exists, terminal reuse validates
checkpoint/final evidence and canonical bytes, final and publication fit counts are each one, and final validation
failure publishes no best model.

```bash
git diff --check
git add src/trafficlab/fitting.py src/trafficlab/artifacts.py src/trafficlab/cli.py \
  tests/unit/test_fitting.py tests/unit/test_cli.py
git commit -m "feat: add genetic fit stage"
```

### Task 8: Check in offline fitting fixtures and full Phase 5 integration evidence

**Files:**

- Create: `examples/data/fit/experiment.toml`
- Create: `examples/data/fit/capture.json`
- Create: `examples/data/fit/reference.pcapng`
- Create: `examples/data/fit/checkpoint.json`
- Create: `examples/data/fit/ga_history.csv`
- Create: `examples/data/fit/best_model.json`
- Create: `examples/data/fit/README.md`
- Create: `scripts/generate_fit_fixtures.py`
- Create: `tests/unit/test_fit_fixture_generator.py`
- Create: `tests/integration/test_genetic_fitting.py`

**Interfaces:**

- Consumes: completed config, fit stage, checkpoint codec, artifact codecs, generation stage, comparison stage,
  and existing fixture-PCAPNG helpers.
- Produces: deterministic tiny all-three-family nondefault fixture; script command
  `uv run --locked python scripts/generate_fit_fixtures.py --check`; offline integration evidence.

- [ ] **Step 1: Write failing fixture generator and competition tests**

```python
def test_fit_fixture_generator_check_mode_accepts_checked_in_bytes() -> None:
    assert main(["--check"]) == 0


def test_small_nondefault_three_family_population_keeps_each_family_and_uses_own_operators(tmp_path: Path) -> None:
    result = fit_experiment(FIXTURE_EXPERIMENT.with_run_directory(tmp_path / "run"))
    checkpoint = load_checkpoint(tmp_path / "run" / "checkpoint.json", compatibility_for_fixture())
    assert {candidate.family for candidate in checkpoint.population} == {"markov_renewal", "mmpp", "poisson_empirical"}
    assert result.best_model.family in {candidate.family for candidate in checkpoint.population}


def test_resume_and_tampered_operator_checkpoint_are_observable_offline(tmp_path: Path) -> None:
    assert resumed_fixture_result(tmp_path) == uninterrupted_fixture_result(tmp_path)
    with pytest.raises(TrafficlabError, match="operator values for family"):
        load_tampered_fixture_checkpoint(tmp_path)
```

- [ ] **Step 2: Run fixture/integration RED through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/test_fit_fixture_generator.py tests/integration/test_genetic_fitting.py -q
```

Expected: missing generator, data directory, and genetic integration modules.

- [ ] **Step 3: Create tiny deterministic all-family fixture inputs**

Use the existing deterministic PCAPNG writer and capture metadata codec, never manually encode packet binary.
Configure all three families, `population_size` satisfying elites plus champions, one or two selection seeds, a
distinct `final_seed`, nondefault crossover/mutation/scale values for each family, `generation_count=1`, and
sufficiently large trial/final guards. Put the exact values and expected family order in
`examples/data/fit/README.md`.

```toml
[genetic]
population_size = 6
generation_count = 1
tournament_size = 2
elite_count = 1
trial_seeds = [17]
duplicate_mutation_attempts = 1
early_stopping_generations = 0
early_stopping_tolerance = 0.0
resume = true
```

- [ ] **Step 4: Implement deterministic generator and `--check` behavior**

```python
def main(argv: Sequence[str] | None = None) -> int:
    check = argparse.ArgumentParser().parse_args(argv).check
    expected = generate_fixture_tree()
    if check:
        return compare_fixture_tree(expected)
    write_fixture_tree(expected)
    return 0
```

`--check` must compare every expected byte file and return nonzero with the exact mismatched relative path;
regular mode writes only under `examples/data/fit/`. Generate initial/resumed/tamper support deterministically
without network or Docker.

- [ ] **Step 5: Add offline `fit -> generate -> compare` integration test**

```python
def test_offline_fit_generate_compare_preserves_one_window_everywhere(tmp_path: Path) -> None:
    fit = fit_experiment(copy_fixture_experiment(tmp_path))
    generated = generate_experiment(fit.experiment_path)
    comparison = compare_experiment(fit.experiment_path)
    assert (
        fit.observation_window_seconds
        == generated.observation_window_seconds
        == (comparison.observation_window_seconds)
    )
    assert all(
        method.diagnostics["observation_window_seconds"] == fit.observation_window_seconds
        for method in comparison.methods.values()
    )
```

Also assert same-family-only crossover from injected trace data, cross-family forced mutation, guard-truncated
trial invalidation, final validation uses the one distinct seed, and no Docker command boundary is invoked.

- [ ] **Step 6: Run GREEN and generator check through guards**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/test_fit_fixture_generator.py tests/integration/test_genetic_fitting.py -q
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_fit_fixtures.py --check
uv run --locked ruff format scripts/generate_fit_fixtures.py tests/unit/test_fit_fixture_generator.py \
  tests/integration/test_genetic_fitting.py
uv run --locked ruff check scripts/generate_fit_fixtures.py tests/unit/test_fit_fixture_generator.py \
  tests/integration/test_genetic_fitting.py
uv run --locked pyright scripts/generate_fit_fixtures.py tests/unit/test_fit_fixture_generator.py \
  tests/integration/test_genetic_fitting.py
```

- [ ] **Step 7: Review and commit fixtures/integration**

Read every checked-in JSON/CSV/TOML byte artifact through its real strict loader before committing.

```bash
git diff --check
git add examples/data/fit scripts/generate_fit_fixtures.py tests/unit/test_fit_fixture_generator.py \
  tests/integration/test_genetic_fitting.py
git commit -m "test: add genetic fitting fixtures"
```

### Task 9: Run final gates, independent review, and truthful roadmap update

**Files:**

- Modify: `architecture/ROADMAP.md`
- Modify only when review finds a verified Phase 5 defect: the owning Phase 5 source/test/document file from Tasks 1–8.

**Interfaces:**

- Consumes: every completed Task 1–8 artifact, all strict loaders, fixture generator, and Phase 5 checklist.
- Produces: verified Phase 5 completion evidence, a Critical/Important-free independent review, and roadmap
  checkboxes that match evidence.

- [ ] **Step 1: Create a review checklist from every Phase 5 roadmap box**

```text
[ ] fit command uses all enabled families
[ ] quotas/common W/trial seeds/ties/elites/champions are deterministic
[ ] coordinate crossover/mutation/reflection/RNG order are exact
[ ] candidate invalid versus infrastructure abort is explicit
[ ] checkpoint/RNG/history resume is strict and atomic
[ ] fresh final seed validation publishes a winner without reselection
[ ] best_model.json and ga_history.csv satisfy exact artifact contracts
[ ] three-family nondefault/resume/tamper/offline integration evidence exists
```

For every item, record the exact test node(s), artifact parser, and source module that proves it. Do not mark any
roadmap checkbox yet.

- [ ] **Step 2: Run all focused Phase 5 suites serially through the guard**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -n 0 tests/unit/genetic tests/unit/test_fitting.py tests/unit/test_cli.py \
  tests/unit/test_fit_fixture_generator.py tests/integration/test_genetic_fitting.py -q
```

Expected: all Phase 5 focused tests pass without Docker or Internet selection.

- [ ] **Step 3: Run locked format, lint, and strict typing gates**

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
git diff --check
```

Expected: formatter reports no pending rewrite, lint/type commands report zero errors, and the diff has no
whitespace errors.

- [ ] **Step 4: Run the fast four-worker deterministic suite through the guard**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

Expected: all fast unit tests pass. Do not use `-n auto`, omit `--dist`, or run another pytest command concurrently.

- [ ] **Step 5: Run non-Docker/non-Internet branch coverage through the guard**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Expected: exit zero and at least 90% branch-aware coverage. If a Phase 5 failed unit test revealed a defective
function, inspect its targeted missing-line report and add behavior tests until that function has 100% executable
lines and branches.

- [ ] **Step 6: Prove fixture and guard controls remain available without external work**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/generate_fit_fixtures.py --check
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

Expected: fixtures match committed bytes and the process guard succeeds. These commands deliberately do not select
Docker or Internet tests.

- [ ] **Step 7: Obtain and address an independent Phase 5 review**

Request a reviewer who did not implement the last task to inspect the final diff against `architecture/ROADMAP.md`,
`architecture/genetic_models/basic_generational.md`, `architecture/SYSTEM.md`, and `architecture/TESTING.md`.
Give the reviewer the checklist, exact verification output, checkpoint compatibility examples, and fixture paths.
Fix every Critical or Important finding with a new failing behavioral test, guarded RED run, minimal implementation,
guarded GREEN run, and rerun the affected final gate.

- [ ] **Step 8: Update roadmap only after all evidence and review are green**

Mark exactly the Phase 5 deliverable and test checkboxes complete only when their named verification exists. Do not
mark Phase 6, Docker, Internet, or future work. Keep the Phase 5 `Done when` statement truthful; if any required
evidence is missing, leave the corresponding checkbox unchecked and continue implementation.

- [ ] **Step 9: Final review, clean tree check, and completion commit**

```bash
git diff --check
git status --short
git add architecture/ROADMAP.md
git commit -m "docs: record phase 5 completion"
git status --short
```

Before committing, verify only the intended roadmap change is staged. After committing, require an empty
`git status --short`, retain all Task 1–8 commits locally, and report the exact commands/results rather than a
generalized success claim.

## Plan Self-Review

- Phase 5 fit, quotas, common seeds/window/limits, stable ranking, selection, elites, champions,
  same/different-family operators, repair, duplicate policy, and exact RNG order are assigned to Tasks 2–4 and
  integration-tested in Task 8.
- Checkpoint contents, strict JSON/RNG compatibility, atomic checkpoint-first/history-second ordering, resume,
  and tamper rejection are assigned to Tasks 5–6 and independently exercised in Task 8.
- Fresh final validation, no reselection, `best_model.json`, prepared one-read input, run log, and CLI are
  assigned to Task 7.
- The separate all-three-family nondefault offline fixtures, resume, tamper, installed CLI, and generator check
  are assigned to Task 8.
- Task 9 contains all required locked/static/fast/coverage/guard commands, independent review, truthful roadmap
  handling, and no external resource selection.
- Interfaces named by later tasks are defined in Locked Interfaces or produced by the earlier task named in each
  Interfaces block.
- No task uses raw pytest, unbounded systemd, `-n auto`, overlapping test runs, Docker, Internet, Phase 3 edits,
  or Phase 6 edits.
