# Phase 4 Traffic Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the three documented traffic-model families behind one strict interface and make
`trafficlab generate` publish a deterministic, parseable, complete-window PCAPNG from a fitted-model artifact.

**Architecture:** A small `trafficlab.models` package owns common validation, deterministic sampling, three typed
immutable fitted-model implementations, strict versioned model JSON, and one closed three-family registry. The
in-process `trafficlab.generation` stage loads a winning model and capture metadata, performs final generation,
round-trips the rendered trace, and publishes `generated.pcapng` without replacing unrelated bytes.

**Tech Stack:** Python 3.12, standard-library `dataclasses`, `json`, `math`, `random`, `time`, and `tempfile`;
existing Pydantic v2 configuration models and canonical trace/PCAPNG boundaries; pytest, pytest-xdist, Ruff, and
strict Pyright through uv.

## Global Constraints

- Treat `architecture/`, especially the four traffic-model documents and Phase 4 of `architecture/ROADMAP.md`, as
  authoritative; keep the approved one-process, classical-model research-prototype scope.
- Follow red-green-refactor for every behavior: add one focused failing test, run it serially through the committed
  bounded-test guard, add the minimum implementation, rerun the focused test, then run the task file or task slice.
- Use `apply_patch` for hand-authored edits and uv for Python commands. Do not add a dependency, plugin framework,
  dynamic model discovery, security subsystem, Node.js dependency, or speculative service.
- Import and use `trafficlab.config.GenerationLimits`; do not introduce a second limits type. The output-byte guard
  counts `sum(event.frame_length)` in canonical Ethernet bytes, independent of PCAPNG block overhead.
- All references passed to model fitting are already normalized and must contain at least two finite nondecreasing
  events, start exactly at `0.0`, end exactly at the supplied finite positive `W`, and contain renderer-compatible
  Ethernet frame lengths in inclusive range `14..2**32 - 1`.
- `ModelFamily` has the exact public methods
  `repair(genes, bounds, reference) -> tuple[float | int, ...]`,
  `fit(reference, genes, *, W, bounds) -> FittedModel`, and
  `generate(model, seed, W, limits, *, clock=time.monotonic) -> GenerationResult`. `bounds` is typed as
  `FamilyBounds = PoissonConfig | MarkovRenewalConfig | MmppConfig`, and each family rejects the wrong configured
  type. Hashes and other artifact lineage belong only to `BestModel`, not fitted family dataclasses.
- Every public family `fit` defensively applies its deterministic `repair` before estimating/storing values, so the
  returned fitted model always corresponds to canonical repaired genes even when a direct caller did not repair first.
- The runtime registry contains exactly `poisson_empirical`, `markov_renewal`, and `mmpp` in that order and rejects
  every other family. There is no entry-point or import-string extension mechanism.
- Every public generation call rejects a seed unless it is an exact nonnegative `int`, constructs
  `random.Random(seed)`, and never uses global RNG state. Every `random()` result must be finite in `[0,1)`, and every
  sampled delay must be finite and nonnegative. Stable empirical sampling uses `randrange`.
- `W` is a finite positive `float`. Every generator forces its first packet at `t=0.0`, emits a prospective event
  when `next_time <= W`, and returns `complete=True` only after observing a stochastic next-event timestamp strictly
  greater than `W`.
- Before the initial event and every loop decision, check wall time and whether packet/output budgets are exhausted;
  exhaustion is incomplete even when a later draw might prove natural completion. Check wall time immediately after
  every stochastic draw. After timing draws, compare `next_time` with `W` before prospective packet/output checks.
  Before emitting at or below `W`, require `len(events) + 1 <= max_packets` and
  `output_bytes + frame_length <= max_output_bytes`.
- `max_wall_seconds` is measured from the first injected `clock()` value. A nonfinite/backward clock or
  `clock() >= start + max_wall_seconds` is an incomplete `max_wall_seconds` result, never a reusable shortened trace.
- `GenerationResult` is a frozen typed value containing `complete`, partial diagnostic `events`, and `reason`.
  Complete results require a nonempty trace and `reason is None`; incomplete results require one of `max_packets`,
  `max_output_bytes`, or `max_wall_seconds`. `require_complete()` is the sole conversion to a reusable trace and
  raises `TrafficlabError` for incomplete results.
- JSON numbers and clocks must be finite; booleans never satisfy integer/float fields. Hashes are exactly 64 lowercase
  hexadecimal characters. Frame directions use the exact canonical strings `outbound` and `inbound`. Reject duplicate
  JSON object keys at every nesting level with `object_pairs_hook`, before exact-key/type validation.
- Serialized genes have family-specific arity and exact canonical types: Poisson has one `float`; MMPP has four
  `float` values; Markov renewal has `float, float, float, int, float`. Each named bound has the corresponding exact
  `FloatBounds` or `IntegerBounds` representation. Integers do not satisfy float fields, and booleans satisfy neither.
- Every generator treats a nonfinite/overflowed sampled delay or `next_time` as a structural `TrafficlabError`, not a
  reliability limit or natural completion.
- `best_model.json` is UTF-8, newline-terminated canonical JSON rendered by
  `json.dumps(..., sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"`. It has exactly `version`,
  `family`, `genes`, `fitted`, `reference_sha256`, `capture_sha256`, `observation_window_seconds`, `gene_bounds`,
  `estimator_choices`, and `seed_policy`; `version` is integer `1`. Loading rejects unknown/missing keys, wrong exact
  types, nonfinite values, inconsistent family/genes/fitted data, malformed bounds, and invalid family invariants.
- Use Type 7 linear quantiles for Markov renewal: for sorted lengths `x`, `h=(n-1)q`, `i=floor(h)`, and
  `x[i] + (h-i) * (x[min(i+1,n-1)]-x[i])`. Bins are `length <= threshold1`, else `length <= threshold2`, else bin 2.
  Document this Trafficlab estimator choice in the owning model document.
- Stable order means first appearance in the normalized reference for empirical marks and active Markov states;
  stored order is authoritative after loading. Transition sampling walks the stored active-state order. Do not sort
  by enum text, frame length, dictionary hash order, or set iteration.
- Each task ends with focused verification, self-review for placeholders and type/signature drift, independent review,
  resolution of all Critical and Important findings, and one coherent local commit. Do not push.
- Every pytest invocation uses the exact committed guard interface with all five named limit flags and `--`, followed
  by `uv run --locked pytest`. Focused tests use `--memory-high 2G --memory-max 3G --swap-max 512M`, `-n 0`, and
  `--wall-time 5m --kill-after 10s`, except a justified `10m` focused run. Broad tests use
  `--memory-high 6G --memory-max 8G --swap-max 1G`, `-n 4 --dist worksteal`, `--kill-after 10s`, and the stated
  `10m` or `20m` wall time. Do not invoke `systemd-run` directly or use `-n auto`.

---

## File Map and Locked Interfaces

- `src/trafficlab/models/common.py`: common types, reference/bounds validation, guard accounting, stable empirical
  marks, weighted choice, and `GenerationResult`; artifact hashes/JSON stay in Task 5 registry code.
- `src/trafficlab/models/registry.py`: Task 5 exact three-family registry plus strict outer `BestModel`, load, and
  render dispatch, created only after all three family codecs exist.
- `src/trafficlab/models/poisson.py`: Poisson genes, fitted parameters, repair, fit, serialization, and generation.
- `src/trafficlab/models/markov_renewal.py`: Type 7 state construction, complete transition estimator, timing
  samples/fallback, repair, fit, serialization, and generation.
- `src/trafficlab/models/mmpp.py`: direct-rate repair/fit, stationary initialization, CTMC/arrival race,
  serialization, and generation.
- `src/trafficlab/models/__init__.py`: common exports in Task 1, then Task 5 exports for `BestModel`,
  `load_best_model`, `render_best_model`, and `get_family` after registry construction.
- `src/trafficlab/generation.py`: final-generation stage and publication orchestration; it never opens
  `reference.pcapng`.
- `src/trafficlab/artifacts.py`: validated, exclusive, byte-identical reusable `generated.pcapng` publication.
- `src/trafficlab/cli.py`: lazy `generate` command adapter and concise success/error output.
- `scripts/generate_model_fixtures.py`: deterministic checked-fixture producer with `--check` byte comparison.
- `fixtures/examples/pipeline/models/best_model.json`: Phase 4 Poisson artifact whose hashes identify the parent checked capture
  metadata/reference bytes without modifying Phase 2-owned fixtures.
- `fixtures/examples/pipeline/models/generated.pcapng`: Phase 4 deterministic final-seed rendering from that model and the parent
  `fixtures/examples/pipeline/capture.json`.

The common concrete signatures are:

```text
type Gene = float | int
type Genes = tuple[Gene, ...]
type IncompleteReason = Literal["max_packets", "max_output_bytes", "max_wall_seconds"]
type FamilyBounds = PoissonConfig | MarkovRenewalConfig | MmppConfig

GenerationResult fields:
  complete: bool
  events: tuple[TraceEvent, ...]
  reason: IncompleteReason | None = None
GenerationResult.require_complete(self) -> tuple[TraceEvent, ...]

FittedModel.family -> FamilyName

ModelFamily.name: FamilyName
ModelFamily.gene_names: tuple[str, ...]
ModelFamily.bounds_type: type[FamilyBounds]
ModelFamily.estimator_choices: Mapping[str, str | int | float]
ModelFamily.repair(self, genes: Sequence[float | int], bounds: FamilyBounds,
                   reference: Sequence[TraceEvent]) -> Genes
ModelFamily.fit(self, reference: Sequence[TraceEvent], genes: Sequence[float | int], *,
                W: float, bounds: FamilyBounds) -> FittedModel
ModelFamily.generate(self, model: FittedModel, seed: int, W: float,
                     limits: GenerationLimits, *,
                     clock: Callable[[], float] = monotonic) -> GenerationResult
ModelFamily.load_fitted(self, data: object, *, genes: Genes,
                        bounds: FamilyBounds) -> FittedModel
ModelFamily.dump_fitted(self, model: FittedModel) -> dict[str, object]

BestModel fields:
  version: Literal[1]
  family: FamilyName
  genes: Genes
  fitted: FittedModel
  reference_sha256: str
  capture_sha256: str
  observation_window_seconds: float
  gene_bounds: dict[str, FloatBounds | IntegerBounds]
  estimator_choices: dict[str, str | int | float]
  seed_policy: dict[str, str]

load_best_model(content: bytes, *, source: Path) -> BestModel
render_best_model(model: BestModel) -> bytes
get_family(name: str) -> ModelFamily
make_best_model(family: ModelFamily, reference: Sequence[TraceEvent], genes: Sequence[Gene], *,
                reference_sha256: str, capture_sha256: str, W: float,
                bounds: FamilyBounds) -> BestModel
```

`seed_policy` is exactly
`{"empirical":"randrange","exponential":"expovariate","generator":"random.Random","weighted":"random_cumulative"}`.
Family-specific `estimator_choices` are exact-key dictionaries validated on load. Poisson uses
`rate="interval_count_over_window"`, `marks="joint_empirical_first_appearance"`, and `first_event="zero"`. Markov
renewal uses `quantile="type7_linear"`, `state_order="first_appearance"`,
`transition="additive_uniform_empty_row"`, `timing="conditional_source_global"`, and `first_event="zero"`. MMPP uses
`rates="direct_genes"`, `initial_regime="stationary"`, `marks="joint_empirical_first_appearance"`,
`tie="regime_change"`, and `first_event="zero"`.

---

### Task 1: Shared Model Contract, Guards, and Empirical Marks

**Files:**
- Create: `src/trafficlab/models/common.py`
- Create: `src/trafficlab/models/__init__.py`
- Create: `tests/unit/models/test_common.py`
- Modify: `architecture/traffic_models/README.md`

**Interfaces:**
- Consumes: `GenerationLimits`, `PoissonConfig`, `MarkovRenewalConfig`, `MmppConfig`, `FamilyName`, `Direction`,
  `TraceEvent`, and `TrafficlabError`.
- Produces: `Gene`, `Genes`, `FamilyBounds`, `FittedModel`, `ModelFamily`, `GenerationResult`,
  `MarkCount(direction, frame_length, count)`, `MarkDistribution.from_reference()`,
  `MarkDistribution.sample(rng)`, `validate_fit_inputs()`, `weighted_index()`, and `GenerationGuard` for Tasks 2–6.
  Task 1 contains no registry, `BestModel`, family-codec dispatch, or substitute registry scaffolding.

- [ ] **Step 1: Add failing common-contract tests**

  Add exact tests for strict `GenerationResult` state combinations, `require_complete`, finite-positive `W`,
  normalized endpoints, nondecreasing timestamps, accepted lengths `14`, `70_000`, and `2**32 - 1`, rejection of
  `13` and `2**32`,
  exact integer seed/limit types, stable first-appearance mark counting, one-draw integer cumulative mark sampling,
  weighted boundary selection, and all three guards. Use ordinary typed scripted clock/RNG values so draw count and
  order are asserted directly.

  ```python
  def test_mark_distribution_preserves_first_appearance_and_joint_counts() -> None:
      marks = MarkDistribution.from_reference(
          (
              TraceEvent(0.0, Direction.INBOUND, 60),
              TraceEvent(0.5, Direction.OUTBOUND, 80),
              TraceEvent(1.0, Direction.INBOUND, 60),
          )
      )
      assert marks.entries == (
          MarkCount(Direction.INBOUND, 60, 2),
          MarkCount(Direction.OUTBOUND, 80, 1),
      )
      rng = ScriptedRandrange([1, 2])
      assert marks.sample(rng) == (Direction.INBOUND, 60)
      assert marks.sample(rng) == (Direction.OUTBOUND, 80)
      assert rng.stops == [3, 3]


  @pytest.mark.parametrize(
      ("count", "byte_count", "now", "reason"),
      [(2, 10, 0.0, "max_packets"), (1, 100, 0.0, "max_output_bytes"), (1, 10, 1.0, "max_wall_seconds")],
  )
  def test_guard_reports_exhaustion_before_another_draw(
      count: int, byte_count: int, now: float, reason: IncompleteReason
  ) -> None:
      guard = GenerationGuard.start(
          GenerationLimits(max_packets=2, max_output_bytes=100, max_wall_seconds=1.0),
          clock=ScriptedClock([0.0, now]),
      )
      assert guard.pre_draw_reason(count, byte_count) == reason
  ```

- [ ] **Step 2: Run the common tests and confirm red**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_common.py -q
  ```

  Expected: collection fails because `trafficlab.models.common` does not exist.

- [ ] **Step 3: Implement the minimum common module**

  Implement frozen/slots dataclasses with explicit validation. `GenerationGuard.start()` calls the injected clock
  once and records an initial wall failure for a nonfinite start/deadline; otherwise it stores the deadline and last
  clock value. `pre_draw_reason()`, `post_draw_reason()`, and `prospective_reason()` return `max_wall_seconds` for a
  nonfinite, backward, or deadline-reaching clock. `weighted_index()` validates finite nonnegative weights with a
  positive total, consumes exactly one `random()`, walks tuple order, and lets the last index absorb numerical tail.
  `MarkDistribution` stores positive integer counts, rejects duplicate marks/non-renderable lengths, and consumes
  exactly one `randrange(total_count)` per sample.

  Document in `architecture/traffic_models/README.md` that the byte limit counts canonical Ethernet frame lengths,
  guards run before decisions and after draws, and incomplete events are diagnostics only.

- [ ] **Step 4: Run, type-check, and review Task 1**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_common.py -q
  uv run --locked ruff check src/trafficlab/models/common.py src/trafficlab/models/__init__.py \
    tests/unit/models/test_common.py
  uv run --locked pyright src/trafficlab/models tests/unit/models/test_common.py
  ```

  Expected: focused tests pass and static checks are clean. Scan for placeholders, implicit `Any`, unordered sampling,
  duplicate limits types, and signature drift. Request independent review of the common contract and guards; fix all
  Critical or Important findings and rerun the same commands.

- [ ] **Step 5: Commit Task 1**

  ```bash
  git add architecture/traffic_models/README.md src/trafficlab/models/common.py \
    src/trafficlab/models/__init__.py tests/unit/models/test_common.py
  git commit -m "feat: add shared traffic model contract"
  ```

---

### Task 2: Poisson Empirical Family

**Files:**
- Create: `src/trafficlab/models/poisson.py`
- Create: `tests/unit/models/test_poisson.py`

**Interfaces:**
- Consumes: Task 1 validation, marks, guard, JSON primitives, and `PoissonConfig` bounds.
- Produces: `PoissonModel(base_rate, rate, marks)` with constant family property, and one complete `PoissonFamily`
  object owning `gene_names`, `bounds_type`, `estimator_choices`, repair/fit/generate, and strict fitted load/dump.

- [ ] **Step 1: Add failing estimator and repair tests**

  Cover the document literally: timestamps `0,1,2` give base rate `1`; gene `2` gives fitted rate `2`; exact positive
  bounds remain unchanged; finite out-of-range genes clamp; wrong arity, bool, nonfinite, nonpositive post-repair genes,
  invalid bounds, fewer than two packets, zero window, mismatched `W`, decreasing timestamps, and invalid marks fail.

  ```python
  def test_fit_uses_interval_count_over_full_window(reference: tuple[TraceEvent, ...]) -> None:
      fitted = FAMILY.fit(
          reference,
          (2.0,),
          W=2.0,
          bounds=PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0)),
      )
      assert fitted.base_rate == 1.0
      assert fitted.rate == 2.0
  ```

- [ ] **Step 2: Confirm estimator/repair tests fail**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_poisson.py -k 'repair or fit' -q
  ```

  Expected: failures identify the missing Poisson family.

- [ ] **Step 3: Implement repair, fit, and fitted JSON**

  Public `fit` calls `repair` itself, then computes `(len(reference)-1)/W`, multiplies by repaired `c_lambda`, and
  rejects nonfinite/nonpositive results. Store stable counted joint marks. The fitted object has exactly
  `base_rate: float`, `rate: float`, and `marks: list[MarkCount object]`; every mark object has exactly `count: int`,
  `direction: str`, and `frame_length: int`. Loading repeats invariants and checks `rate` against
  `base_rate * outer_genes[0]` using canonical repaired outer genes.

- [ ] **Step 4: Add failing deterministic generation tests**

  Assert exact draw order: initial `randrange` mark; then each loop `expovariate(rate)`; only an in-window time draws
  another mark. Test equal-seed event equality, no global RNG mutation, zero delays, endpoint emission, natural
  completion, packet/output/wall guards at pre-draw and prospective-emission boundaries, wall checks after exponential
  and mark draws, and a nonfinite/backward clock.

  ```python
  def test_generation_draw_order_and_closed_endpoint(model: PoissonModel) -> None:
      rng = ScriptedPoissonRng(marks=[0, 1], delays=[2.0, 0.1])
      result = generate_with_rng(model, rng, W=2.0, limits=LARGE_LIMITS, clock=steady_clock)
      assert result.require_complete() == (
          TraceEvent(0.0, Direction.OUTBOUND, 60),
          TraceEvent(2.0, Direction.INBOUND, 80),
      )
      assert rng.calls == [
          ("randrange", model.marks.total_count),
          ("expovariate", model.rate),
          ("randrange", model.marks.total_count),
          ("expovariate", model.rate),
      ]
  ```

- [ ] **Step 5: Confirm generation tests fail, then implement generation**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_poisson.py -k generate -q
  ```

  Implement the initial guard/mark/emission and loop in the exact global draw/guard order. Keep a private RNG-injected
  helper solely so tests can prove ordering; public `generate` always constructs `Random(seed)`.

- [ ] **Step 6: Run and review Task 2**

  Run:

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_poisson.py -q
  uv run --locked ruff check src/trafficlab/models/poisson.py tests/unit/models/test_poisson.py
  uv run --locked pyright src/trafficlab/models/poisson.py tests/unit/models/test_poisson.py
  ```

  Expected: focused tests pass and static checks are clean. Independently review formula, serialized invariants, draw
  order, endpoint behavior, and every guard; fix Critical/Important findings and rerun.

- [ ] **Step 7: Commit Task 2**

  ```bash
  git add src/trafficlab/models/poisson.py tests/unit/models/test_poisson.py
  git commit -m "feat: implement Poisson empirical model"
  ```

---

### Task 3: Markov Renewal Family

**Files:**
- Create: `src/trafficlab/models/markov_renewal.py`
- Create: `tests/unit/models/test_markov_renewal.py`
- Modify: `architecture/traffic_models/markov_renewal.md`

**Interfaces:**
- Consumes: Task 1 common contracts and `MarkovRenewalConfig`.
- Produces: `MarkovState(direction, size_bin, frame_lengths, source_iats)`, `MarkovRenewalModel` parameters and
  samples, `type7_quantile()`, transition/timing samplers, and one complete `MarkovRenewalFamily` object owning its
  gene metadata, repair/fit/generate, and strict fitted load/dump.

- [ ] **Step 1: Document and test Type 7 state construction and repair**

  Add the exact Type 7 formula and inclusive bin comparisons to the architecture document. Test `x=[10,20,30,40]`
  at `q=.25,.75` produces thresholds `17.5,32.5`; thresholds need not be integers. Test first-appearance state order,
  exactly three possible bins per observed direction, reversed quantile sorting before named clamping, named bounds
  remaining attached to `q1`/`q2`, half-up integer repair `floor(x+0.5)` for positive `r`, inclusive integer bounds,
  and rejection of equal repaired quantiles, clamping-destroyed order, duplicate thresholds, invalid genes, arity, and
  bounds.

  ```python
  def test_type7_thresholds_and_inclusive_bins() -> None:
      lengths = (10, 20, 30, 40)
      assert type7_quantile(lengths, 0.25) == 17.5
      assert type7_quantile(lengths, 0.75) == 32.5
      assert tuple(size_bin(length, 17.5, 32.5) for length in (10, 17, 18, 32, 33, 40)) == (0, 0, 1, 1, 2, 2)


  def test_r_half_cases_round_up() -> None:
      assert FAMILY.repair((0.2, 0.8, 0.0, 2.5, 1.0), BOUNDS, DISTINCT_REFERENCE)[3] == 3
  ```

- [ ] **Step 2: Confirm repair/state tests fail**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_markov_renewal.py \
      -k 'quantile or bin or repair or state' -q
  ```

  Expected: failures identify absent quantile/state/repair implementation.

- [ ] **Step 3: Implement repair and state construction**

  Materialize and validate the reference once. Sort only the two quantile values, then clamp each to its named bound;
  clamp `alpha`, half-up-rounded `r`, and `c_t` to their own bounds. Public `fit` invokes this repair before deriving
  thresholds. Build states as `(direction,bin)` in first-reference-appearance order and retain actual frame lengths in
  destination-state reference order.

- [ ] **Step 4: Add failing complete-estimator and fallback tests**

  Hand-count rows and assert

  ```python
  expected = (transition_count + alpha) / (outgoing_count + alpha * state_count)
  ```

  for all ordinary rows. Assert `[A,B]`, `alpha=0` gives `A=[0,1]` and final-only `B=[.5,.5]`; a positive-smoothing
  empty row reaches the same uniform row through the ordinary formula; a nonempty zero-smoothed row equals empirical
  frequencies. Assert conditional IATs are indexed by source/destination, source samples contain every IAT leaving
  that source, and global samples contain every adjacent IAT including zero. Test fallback precedence: conditional
  when `len >= r`, otherwise nonempty source, otherwise global. Missing, empty, nonfinite, or negative global IAT data
  fails fitting/loading; zero is valid. Test every row finite/nonnegative/sum-to-one within absolute tolerance `1e-12`,
  matrix dimensions `K x K`, and `K >= 1`.

  ```python
  def test_final_only_zero_smoothed_row_is_uniform(two_event_reference: tuple[TraceEvent, ...]) -> None:
      model = fit_two_state(two_event_reference, alpha=0.0)
      assert model.transition_rows == ((0.0, 1.0), (0.5, 0.5))


  @pytest.mark.parametrize(
      ("conditional", "source", "expected"),
      [((0.1, 0.2), (0.1, 0.2, 0.3), (0.1, 0.2)), ((0.1,), (0.1, 0.3), (0.1, 0.3)), ((), (), (0.1, 0.2, 0.3))],
  )
  def test_timing_fallback_precedence(conditional, source, expected) -> None:
      assert choose_holding_sample(conditional, source, (0.1, 0.2, 0.3), minimum_support=2) == expected
  ```

- [ ] **Step 5: Confirm estimator tests fail, then implement fit and strict fitted JSON**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_markov_renewal.py \
      -k 'transition or row or fallback or iat or json' -q
  ```

  Store exact fitted JSON keys `alpha`, `conditional_iats`, `global_iats`, `minimum_support`, `states`, `thresholds`,
  `time_scale`, and `transition_rows`. Each state object has exactly `direction`, `frame_lengths`, `size_bin`, and
  `source_iats`; its initial weight is `len(frame_lengths)`, so no redundant count is stored. `conditional_iats` is a
  `K x K` array aligned to state order. Loading accepts outer `genes` and `bounds`, reconstructs states, validates all
  dimensions/samples, recomputes row invariants, and checks fitted parameters against repaired outer genes.

- [ ] **Step 6: Add failing generation and exact draw-order tests**

  Initial state selection consumes `random()` against empirical state counts, then frame length consumes `randrange`.
  Each loop consumes transition `random()`, holding-time `randrange` from the selected fallback, and scales by `c_t`.
  Only an in-window transition consumes destination-frame `randrange`. A scripted final-only-state test proves the
  uniform row is sampled and global fallback reached. Cover endpoints, natural completion, zero IAT, fixed-seed
  equality, no global RNG change, malformed loaded rows, and all guards before/after draws and before emission.

  ```python
  def test_final_only_state_uses_uniform_row_and_global_iat(model_with_final_only_state) -> None:
      rng = ScriptedMarkovRng(random_values=[0.9, 0.1], indices=[0, 0, 0])
      result = generate_with_rng(model_with_final_only_state, rng, W=1.0, limits=LARGE_LIMITS, clock=steady_clock)
      assert rng.sample_sources[1] == model_with_final_only_state.global_iats
      assert result.complete is True
  ```

- [ ] **Step 7: Implement Markov renewal generation**

  Use only stored order and samples. Exact floating ties in cumulative state choice belong to the first interval whose
  cumulative value is greater than the draw; the final index absorbs the numerical tail. Multiply the sampled
  nonnegative IAT by the finite positive time scale before addition and reject overflow/nonfinite `next_time` as a
  structural `TrafficlabError`, not a reliability guard.

- [ ] **Step 8: Run and review Task 3**

  Run:

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 10m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_markov_renewal.py -q
  uv run --locked ruff check src/trafficlab/models/markov_renewal.py \
    tests/unit/models/test_markov_renewal.py
  uv run --locked pyright src/trafficlab/models/markov_renewal.py \
    tests/unit/models/test_markov_renewal.py
  ```

  Expected: every documented formula, empty-row case, fallback, loader invariant, and deterministic generation example
  passes. Independently review all mathematical branches and the persisted layout; resolve Critical/Important findings
  and rerun.

- [ ] **Step 9: Commit Task 3**

  ```bash
  git add architecture/traffic_models/markov_renewal.md src/trafficlab/models/markov_renewal.py \
    tests/unit/models/test_markov_renewal.py
  git commit -m "feat: implement Markov renewal model"
  ```

---

### Task 4: Two-State MMPP Family

**Files:**
- Create: `src/trafficlab/models/mmpp.py`
- Create: `tests/unit/models/test_mmpp.py`

**Interfaces:**
- Consumes: Task 1 common contracts and `MmppConfig`.
- Produces: `MmppModel(q01, q10, lambda0, lambda1, marks)` with constant family property and derived stationary
  probability properties, plus one complete `MmppFamily` object owning gene metadata, repair/fit/generate, strict
  fitted load/dump, and `_generate_with_rng()`.

- [ ] **Step 1: Add failing repair, stationary, fit, and loader tests**

  Assert `q01=1,q10=3` yields `pi0=.75,pi1=.25`; near-maximum finite q rates derive finite normalized probabilities
  without overflowing their sum. Repair never swaps transition rates; only lambda genes sort before named clamping;
  each value uses its named inclusive bound. Wrong arity/type, bool/nonfinite/nonpositive rates, invalid bounds,
  equal/post-clamp-disordered lambdas, missing marks, or invalid derived probabilities fail. Fitting does no likelihood
  optimization and stores the four repaired genes plus joint empirical marks.

  ```python
  def test_stationary_probabilities_keep_named_transition_rates() -> None:
      model = fit_mmpp((1.0, 3.0, 2.0, 8.0))
      assert (model.q01, model.q10) == (1.0, 3.0)
      assert (model.pi0, model.pi1) == (0.75, 0.25)


  def test_repair_sorts_only_arrival_rates() -> None:
      assert FAMILY.repair((1.0, 3.0, 8.0, 2.0), BOUNDS, REFERENCE) == (1.0, 3.0, 2.0, 8.0)
  ```

- [ ] **Step 2: Confirm repair/fit tests fail, then implement them**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_mmpp.py \
      -k 'repair or stationary or fit or json' -q
  ```

  Public `fit` first applies deterministic repair. Fitted JSON has exactly `lambda0`, `lambda1`, `marks`, `q01`, and
  `q10`. Compute `pi0` and `pi1` from q rates as
  properties or immediately before generation; do not serialize them. Loading validates q rates and derived finite
  probabilities in `[0,1]` summing to one within `1e-12`, using max-rate scaling or equivalent overflow-safe
  arithmetic, and validates all rates against outer genes/bounds.

- [ ] **Step 3: Add failing event-race and generation tests**

  Exact draw order is initial-regime `random()`, initial-mark `randrange`, then per loop arrival
  `expovariate(lambda_z)` followed by transition `expovariate(q_z_other)`. Compare both sampled absolute next times;
  arrival wins only for strict `<`, so a tie switches regime without a packet. Compare the selected time to `W` after
  both clocks and before prospective mark/budget checks. An arrival consumes one joint-mark `randrange`; a regime
  change consumes none. Cover both starting regimes, arrivals/transitions, ties, endpoints, fixed seed, no global RNG
  change, and all guard positions including non-emitting regime changes hitting wall time.

  ```python
  def test_exact_race_tie_changes_regime_without_emission(model: MmppModel) -> None:
      rng = ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[0.5, 0.5, 2.0, 2.0])
      result = generate_with_rng(model, rng, W=1.0, limits=LARGE_LIMITS, clock=steady_clock)
      assert result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
      assert rng.calls[:4] == [
          ("random",),
          ("randrange", model.marks.total_count),
          ("expovariate", model.lambda0),
          ("expovariate", model.q01),
      ]
  ```

- [ ] **Step 4: Confirm generation tests fail, then implement the CTMC race**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_mmpp.py -k generate -q
  ```

  Resample both exponential clocks after either event, per the memoryless construction. Check wall time after each
  exponential draw, even if the first sampled time already lies after `W`; regime changes consume no output budget.

- [ ] **Step 5: Run and review Task 4**

  Run:

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_mmpp.py -q
  uv run --locked ruff check src/trafficlab/models/mmpp.py tests/unit/models/test_mmpp.py
  uv run --locked pyright src/trafficlab/models/mmpp.py tests/unit/models/test_mmpp.py
  ```

  Expected: all documented repair, stationary, race, tie, endpoint, mark, seed, and guard cases pass. Independently
  review CTMC semantics and draw order; resolve Critical/Important findings and rerun.

- [ ] **Step 6: Commit Task 4**

  ```bash
  git add src/trafficlab/models/mmpp.py tests/unit/models/test_mmpp.py
  git commit -m "feat: implement two-state MMPP model"
  ```

---

### Task 5: Closed Registry, Strict Model JSON, Cross-Family Pipeline, and Model Fixture

**Files:**
- Create: `src/trafficlab/models/registry.py`
- Create: `tests/unit/models/test_registry.py`
- Create: `tests/unit/models/test_contract.py`
- Create: `tests/integration/test_model_pipeline.py`
- Create: `scripts/generate_model_fixtures.py`
- Create: `fixtures/examples/pipeline/models/best_model.json`
- Modify: `src/trafficlab/models/__init__.py`

**Interfaces:**
- Consumes: the three complete family objects/codecs from Tasks 2–4, existing parent
  `fixtures/examples/pipeline/capture.json` and `fixtures/examples/pipeline/reference.pcapng`, `parse_pcapng`, `normalize_reference`, and exact
  SHA-256 identities.
- Produces: the locked `BestModel`, `make_best_model`, `load_best_model`, `render_best_model`, `get_family`, a direct
  closed registry, one common family contract, the in-process pipeline, and the checked Phase 4 model for Task 6.

- [ ] **Step 1: Add failing closed-registry and strict JSON tests**

  Assert the registry is one immutable `MappingProxyType` mapping exactly the ordered names `poisson_empirical`,
  `markov_renewal`, and `mmpp` directly to their real family objects. Assert unknown-family rejection, canonical compact
  sorted newline rendering, and a valid outer round-trip for every real fitted family. Reject every missing/extra key,
  duplicate key at every JSON nesting level, `version=True`, versions other than integer `1`, uppercase/short hashes,
  bool genes, NaN/Infinity, invalid `W`, mismatched gene/bounds names or types, estimator choices, seed policy, family,
  and fitted parameters inconsistent with outer genes/bounds. Do not replace private mappings or create fake codecs.

  ```python
  def test_registry_is_closed_and_stably_ordered() -> None:
      assert tuple(REGISTRY) == ("poisson_empirical", "markov_renewal", "mmpp")
      assert REGISTRY["poisson_empirical"] is POISSON_FAMILY
      with pytest.raises(TrafficlabError, match="unknown model family"):
          get_family("plugin.family")


  def test_best_model_render_is_canonical(valid_best_model: BestModel) -> None:
      rendered = render_best_model(valid_best_model)
      assert rendered.endswith(b"\n")
      assert b" " not in rendered
      loaded = load_best_model(rendered, source=Path("best_model.json"))
      assert render_best_model(loaded) == rendered
  ```

- [ ] **Step 2: Confirm registry tests fail, then implement the real registry and JSON boundary**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_registry.py -q
  ```

  Expected: RED because `trafficlab.models.registry` and its public boundary do not exist. Build the direct immutable
  mapping; do not add `FamilySpec`, lazy providers, dynamic discovery, or runtime replacement hooks. Decode UTF-8 and
  JSON with a duplicate-detecting `object_pairs_hook`, validate the exact outer object, select the real family, and
  call `family.load_fitted(data, genes=genes, bounds=bounds)`. `BestModel` alone owns family, genes, hashes, `W`,
  bounds, estimator choices, and seed policy. `make_best_model()` validates hashes/window, derives the exact named
  bound mapping and family constants, repairs genes, calls family fit, and returns the canonical artifact so callers
  never hand-assemble repeated metadata. Rendering rebuilds the exact object and calls `family.dump_fitted()`.

- [ ] **Step 3: Add the common family contract and run it RED-to-green**

  Parameterize exact valid genes/config tables for all real families. For each, repair and fit the same normalized
  reference and `W`, construct/render/load a `BestModel`, then regenerate twice at one seed. Assert first timestamp
  `0.0`, nondecreasing timestamps, last timestamp `<= W`, renderer-compatible frame bounds, natural completion,
  endpoint behavior, serialized `W`, and each incomplete reason.

  ```python
  @pytest.mark.parametrize("case", FAMILY_CASES, ids=lambda case: case.name)
  def test_every_family_round_trips_and_reproduces(case: FamilyCase) -> None:
      artifact = make_best_model(
          case.family,
          REFERENCE,
          case.genes,
          reference_sha256="a" * 64,
          capture_sha256="b" * 64,
          W=WINDOW,
          bounds=case.bounds,
      )
      loaded = load_best_model(render_best_model(artifact), source=Path("best_model.json"))
      first = case.family.generate(loaded.fitted, 2468, WINDOW, COMPLETE_LIMITS).require_complete()
      second = case.family.generate(loaded.fitted, 2468, WINDOW, COMPLETE_LIMITS).require_complete()
      assert first == second
      assert first[0].timestamp == 0.0
      assert first[-1].timestamp <= WINDOW
  ```

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 10m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_contract.py -q
  ```

  Expected: the new contract is initially RED only if its boundary or an invariant is absent; otherwise it may pass
  immediately. Any exposed drift is fixed in the owning family without weakening the contract.

- [ ] **Step 4: Add and verify the in-process model pipeline**

  Read the checked capture metadata/reference bytes once, parse those bytes, normalize once, and hash those same bytes.
  For each family, fit/serialize/load/generate, encode PCAPNG, parse its bytes, and compare directions/lengths plus
  nanosecond-rounded timestamps. Reload and reproduce byte-identical PCAPNG using the same `W`, limits, hashes, and
  endpoint policy.

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 10m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/integration/test_model_pipeline.py -q
  ```

  Expected: RED if a pipeline boundary is absent; after the minimum owning fix, parsed events equal generated events
  after `round(timestamp * 1_000_000_000) / 1_000_000_000`, and reproduced bytes are identical.

- [ ] **Step 5: Add and verify the deterministic fitted-model fixture generator**

  `scripts/generate_model_fixtures.py` accepts only optional `--check`. It reads the parent Phase 2 capture metadata
  and reference once, hashes/parses those same bytes, fits Poisson gene `(1.0,)` with bounds from `minimal.toml`, and
  renders `fixtures/examples/pipeline/models/best_model.json`. Normal mode writes only Phase 4 model fixtures; `--check` compares
  expected bytes and exits nonzero with the differing path. It never mutates parent Phase 2 fixtures.

  ```bash
  uv run --locked python scripts/generate_model_fixtures.py
  uv run --locked python scripts/generate_model_fixtures.py --check
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 10m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models/test_registry.py \
      tests/unit/models/test_contract.py tests/integration/test_model_pipeline.py -q
  uv run --locked ruff check src/trafficlab/models tests/unit/models/test_registry.py \
    tests/unit/models/test_contract.py tests/integration/test_model_pipeline.py \
    scripts/generate_model_fixtures.py
  uv run --locked pyright src/trafficlab/models tests/unit/models/test_registry.py \
    tests/unit/models/test_contract.py tests/integration/test_model_pipeline.py \
    scripts/generate_model_fixtures.py
  ```

  Expected: the checked model is stable, strictly loads, and contains exact parent capture/reference hashes.

- [ ] **Step 6: Review and commit Task 5**

  Independently review direct registry closure, duplicate-key detection, outer-only lineage, family codec validation,
  contract parity, PCAPNG normalization, and fixture ownership. Fix every Critical/Important finding and rerun Step 5.

  ```bash
  git add src/trafficlab/models tests/unit/models/test_registry.py tests/unit/models/test_contract.py \
    tests/integration/test_model_pipeline.py scripts/generate_model_fixtures.py \
    fixtures/examples/pipeline/models/best_model.json
  git commit -m "feat: add strict traffic model registry"
  ```

---

### Task 6: Final Generation Stage, Exclusive Artifact Publication, CLI, and Example PCAPNG

**Files:**
- Create: `src/trafficlab/generation.py`
- Create: `tests/integration/test_generate_cli.py`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `src/trafficlab/cli.py`
- Modify: `scripts/generate_model_fixtures.py`
- Create: `fixtures/examples/pipeline/models/generated.pcapng`

**Interfaces:**
- Consumes: `open_or_prepare_experiment`, strict snapshot config, `load_best_model`, registered family generation,
  `parse_capture_metadata`, `encode_pcapng`, `parse_pcapng_bytes`, `append_run_log`, `run.final_seed`, and
  `generation.final`.
- Produces:

  ```text
  GeneratedPublication fields:
    path: Path
    created_by_call: bool
    content: bytes

  publish_generated_pcapng(run_directory: Path, content: bytes, *,
                           metadata: CaptureMetadata,
                           expected_events: Sequence[TraceEvent]) -> GeneratedPublication

  GenerationStageResult fields:
    run_directory: Path
    generated_path: Path
    events: tuple[TraceEvent, ...]
    seed: int
    observation_window_seconds: float
    reused: bool

  generate_experiment(path: Path, *,
                      clock: Callable[[], float] = monotonic) -> GenerationStageResult
  ```

- [ ] **Step 1: Add failing publication tests at the integration boundary**

  Test temp-file write/flush/fsync, parse validation before publication, exclusive hard-link publication, and cleanup.
  If `generated.pcapng` exists, read it exactly once; reuse only when those bytes equal expected content, parse from
  those
  same bytes, and equal expected events. Preserve and reject differing/malformed content. Simulate link/write/parse
  failures and assert no canonical replacement and cleanup of only owned temporary files.

  ```python
  def test_existing_identical_generated_capture_is_reused(run_directory, encoded, metadata, expected) -> None:
      destination = run_directory / "generated.pcapng"
      destination.write_bytes(encoded)
      publication = publish_generated_pcapng(run_directory, encoded, metadata=metadata, expected_events=expected)
      assert publication.created_by_call is False
      assert destination.read_bytes() == encoded


  def test_existing_different_generated_capture_is_preserved(run_directory, encoded, metadata, expected) -> None:
      destination = run_directory / "generated.pcapng"
      destination.write_bytes(b"unrelated")
      with pytest.raises(TrafficlabError, match="already exists"):
          publish_generated_pcapng(run_directory, encoded, metadata=metadata, expected_events=expected)
      assert destination.read_bytes() == b"unrelated"
  ```

- [ ] **Step 2: Confirm publication tests fail, then implement exclusive publication**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/integration/test_generate_cli.py -k publication -q
  ```

  Validate expected events against parsed bytes using exact directions/lengths and `round(timestamp*1e9)/1e9`.
  Create a sibling temporary file, flush/fsync, validate its bytes, then `os.link(temp, generated.pcapng)` so an
  existing path is never replaced. Reuse only byte-identical, successfully parsed expected output. There is no new
  manifest or sidecar.

- [ ] **Step 3: Add failing stage tests**

  Test that the stage reopens/prepares the authoritative run and loads `run/experiment.toml` through that path. It
  reads `best_model.json` once and parses those exact bytes; reads `capture.json` once, hashes those bytes, and passes
  the same bytes to `parse_capture_metadata`; never opens `reference.pcapng`; and uses only stored `W`, configured
  final seed/limits. It requires the stored family to be enabled and its exact named bounds to equal that family's
  authoritative snapshot bounds; tests cover disabled families and mismatched bounds. It requires completion,
  republishes a round-tripped canonical trace, and returns reuse state.
  Missing/invalid model, hash mismatch, malformed metadata, incomplete generation, round-trip mismatch, and occupied
  different output are direct generate-stage errors.

  Success appends exactly one sorted JSON-line record:

  ```python
  {
      "event": "generated_pcapng_reused" if reused else "generated_pcapng_published",
      "observation_window_seconds": W,
      "packet_count": len(parsed_events),
      "path": str(run_directory / "generated.pcapng"),
      "seed": final_seed,
      "stage": "generate",
  }
  ```

  After preparation, failure attempts to append exactly
  `{"corrective_action":error.corrective_action,"detail":str(error),"event":"stage_failed","stage":"generate"}`.
  If that append fails, raise a wrapper retaining the original detail, corrective action, and exit code while adding
  the log failure as a diagnostic; do not claim it was appended. A success-log failure may fail the command while the
  already validated `generated.pcapng` remains safely reusable.

- [ ] **Step 4: Confirm stage tests fail, then implement `generate_experiment`**

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/integration/test_generate_cli.py \
      -k 'stage and not cli' -q
  ```

  Materialize and parse `best_model.json` once, dispatch through `get_family(best.family)`, pass the stored `W`, call
  `require_complete()`, render once, publish, parse publication bytes, and expose parsed events. Do not inspect or hash
  `reference.pcapng`; its recorded hash is fitting lineage.

- [ ] **Step 5: Add failing CLI tests and implement lazy `generate` dispatch**

  Add `trafficlab generate EXPERIMENT` to `build_parser()`, reject `--config-only`, and add an injected
  `GenerateExperiment` callable to `main`. When not injected, lazily import `generate_experiment` only in the generate
  branch. Success prints exactly `generate: packets=<n> output=<run>/generated.pcapng`; `TrafficlabError` follows the
  existing `<command>: <detail>; <corrective_action>` stderr format and exit code. Assert existing dispatch remains
  unchanged.

  ```bash
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 5m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/integration/test_generate_cli.py -k cli -q
  ```

- [ ] **Step 6: Extend the fixture generator and check the deterministic PCAPNG**

  Extend the fixture script to load its just-computed model, generate with `minimal.toml` final seed/limits, require
  completion, encode with checked parent capture metadata, parse/compare it, and write/check only
  `fixtures/examples/pipeline/models/generated.pcapng`. CLI integration copies the Phase 4 `best_model.json` into a temporary
  prepared run and compares its run artifact with this Phase 4 PCAPNG. Never mutate parent Phase 2 fixtures.

  ```bash
  uv run --locked python scripts/generate_model_fixtures.py
  uv run --locked python scripts/generate_model_fixtures.py --check
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 10m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/integration/test_generate_cli.py \
      tests/integration/test_model_pipeline.py -q
  ```

  Expected: the example PCAPNG is byte-stable, parseable, bounded to the stored window, and reproduced by the checked
  model with seed `54321`.

- [ ] **Step 7: Review and commit Task 6**

  Run Ruff and strict Pyright over changed production/tests/scripts. Independently review no-replace behavior,
  validation-before-publication, reuse proof, no reference reopen, final settings, log failure precedence, and CLI
  laziness. Fix all Critical/Important findings and rerun focused verification plus fixture `--check`.

  ```bash
  git add src/trafficlab/generation.py src/trafficlab/artifacts.py src/trafficlab/cli.py \
    tests/integration/test_generate_cli.py scripts/generate_model_fixtures.py \
    fixtures/examples/pipeline/models/generated.pcapng
  git commit -m "feat: generate final synthetic capture"
  ```

---

### Task 7: Phase 4 Gate, Roadmap Accounting, and Independent Phase Review

**Files:**
- Modify: `architecture/ROADMAP.md`
- Modify only if review finds a documented contract ambiguity: the owning file under `architecture/traffic_models/`
- Modify only if verification exposes a defect: the owning Phase 4 source or test file named above

**Interfaces:**
- Consumes: Tasks 1–6 and all existing non-Docker package behavior.
- Produces: truthful Phase 4 checkboxes, retained Phase 3 current marker/external-evidence status, and a review-clean
  verified phase.

- [ ] **Step 1: Run locked dependency and static gates**

  ```bash
  uv sync --locked --all-groups
  uv lock --check
  uv run --locked ruff format --check .
  uv run --locked ruff check .
  uv run --locked pyright
  ```

  Expected: lock state is unchanged, formatting/lint pass, and strict Pyright reports zero errors.

- [ ] **Step 2: Run the fast non-Docker suite with exactly four workers**

  ```bash
  scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
    --wall-time 10m --kill-after 10s -- \
    uv run --locked pytest -n 4 --dist worksteal \
      -m "not integration and not docker and not internet" -q
  ```

  Expected: the fast unit scope passes; integration, Docker, and Internet tests are intentionally excluded. After
  completion, confirm no leaked pytest, fixture-generator, or trafficlab subprocess remains.

- [ ] **Step 3: Run branch-aware coverage with exactly four workers**

  Run:

  ```bash
  scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
    --wall-time 20m --kill-after 10s -- \
    uv run --locked pytest -n 4 --dist worksteal -m "not docker and not internet" \
      --cov=trafficlab --cov-branch --cov-report=term-missing --cov-fail-under=90
  ```

  Expected: at least 90% package branch-aware coverage. Inspect the report directly: every repair formula, estimator
  branch, Markov empty-row and fallback, MMPP tie/race, common guard, strict loader, generation-stage failure, and
  publication reuse/reject branch has behavioral coverage even if aggregate coverage already exceeds 90%. Any function
  exposed by a failed test must reach 100% executable line and branch coverage before continuing.

- [ ] **Step 4: Run deterministic fixture and pinpoint gates**

  ```bash
  uv run --locked python scripts/generate_model_fixtures.py --check
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
    --wall-time 10m --kill-after 10s -- \
    uv run --locked pytest -n 0 tests/unit/models tests/integration/test_model_pipeline.py \
      tests/integration/test_generate_cli.py -q
  ```

  Expected: checked JSON/PCAPNG bytes match regeneration; all Phase 4 focused tests pass serially; no subprocess
  remains.

- [ ] **Step 5: Request independent whole-phase review and resolve findings**

  Ask a fresh reviewer to compare the Phase 4 diff against the Phase 4 roadmap and all four model documents,
  emphasizing formula fidelity, strict types/JSON, draw order, complete-window semantics, guards, frame bounds, byte
  budgets, safe publication/reuse, hashes, and no reference reopen. Record the result in the implementation handoff.
  Fix every Critical or Important finding with a failing regression test first, then rerun Steps 1–4.

- [ ] **Step 6: Perform final self-review**

  Search the Phase 4 plan/implementation for placeholders, unchecked exception swallowing, public-boundary `Any`,
  global `random` calls, unordered sampling, raw pytest/systemd invocations, `-n auto`, and names outside the registry.
  Confirm signatures/types match across families, registry, fixtures, generation, CLI, and tests. Run
  `git diff --check`.

- [ ] **Step 7: Mark only proven Phase 4 roadmap items complete**

  Check every Phase 4 Deliverables and Tests box only after its gate passes. Preserve the Phase 3 `Current` marker and
  external Internet/real-program evidence boxes exactly; local Phase 4 completion does not manufacture evidence or
  advance that marker.

- [ ] **Step 8: Commit the phase gate**

  ```bash
  git add architecture/ROADMAP.md architecture/traffic_models src/trafficlab tests \
    scripts/generate_model_fixtures.py fixtures/examples/pipeline/models/best_model.json \
    fixtures/examples/pipeline/models/generated.pcapng
  git commit -m "docs: complete Phase 4 traffic models"
  git status --short
  ```

  Expected: the commit contains only verified Phase 4 corrections/accounting and `git status --short` is empty. Do
  not push.
