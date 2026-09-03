# Required Models and Similarity Methods Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four classical traffic models, four weighted similarity methods, and three final-only diagnostics under deterministic schema-5 artifacts with tiered tests and Moutai-based development experiments.

**Architecture:** Keep the closed one-process registries and existing `ModelFamily`/comparison boundaries. Fit each new family deterministically behind its own focused module, expand genetic fitness from four to eight weighted methods, and publish Fano/Allan, transition fidelity, and C2ST only during final comparison. Use schema 5 to reject stale checkpoints and preserve one authoritative artifact path.

**Tech Stack:** Python 3.12, NumPy 2, SciPy 1.16, Pydantic 2, Scapy 2.7, PySide6/Matplotlib for the optional dashboard, pytest/Hypothesis, Ruff, strict Pyright, uv, Wireshark `editcap`/`reordercap`, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-02-required-models-similarity-design.md`

<!-- label_creation_ns: 1788341491920135208 -->

## Global Constraints

- Work only on branch `feature/required-models-similarity-v5` in `.worktrees/required-models-similarity-v5` created from pushed commit `56cd1bc` or its exact pushed descendant.
- Push the empty feature branch to `origin` before the first implementation edit.
- Use `apply_patch` for hand-authored edits and `uv` for every Python dependency or command.
- Add no runtime dependency; use locked NumPy and SciPy for EM, optimization, random features, and logistic regression.
- Preserve one process, closed registries, classical methods, fixed PCG64 seeds, complete `[0, W]` generation, reliability guards, atomic publication, and no payload/L3/L4 semantics.
- Set `SCIENTIFIC_ARTIFACT_SCHEMA_VERSION = 5`; reject schema 4 without migration and never rewrite historical evidence.
- Weighted fitness contains exactly eight methods; post-fit diagnostics contain exactly Fano/Allan, transition fidelity, and classical C2ST and are never called by genetic trials.
- Update each owning architecture document in the same task as its implementation; architecture contains no progress state or dated experiment conclusions.
- Follow red/green TDD for every behavior. A failed function receives 100% executable line and branch coverage before its task closes.
- Run only the Small tier inside ordinary tasks. Run the Medium tier only at the explicit milestone steps in this plan. Run the Big tier only in the final task.
- Treat every displayed `uv run ... pytest` line as the inner command of the
  canonical bounded wrapper. Small/Focused uses `--memory-high 2G
  --memory-max 3G --swap-max 512M --wall-time 5m --kill-after 10s` and serial
  `-n 0`; Medium uses the same memory bounds with `--wall-time 10m`. The Big
  task copies the authoritative Ordinary, Coverage, and External commands from
  `architecture/DEVELOPMENT.md` exactly.
- Request independent specification-compliance and code-quality review after every task; fix all Critical and Important findings before the next task.
- Commit every reviewed task coherently with a terse Conventional Commit message. Do not amend prior task commits.

## File and responsibility map

New production owners:

- `src/trafficlab/comparison/similarity/ecdf.py` — bounded CvM and AD ECDF distances.
- `src/trafficlab/comparison/similarity/jensen_shannon.py` — exact aligned PMFs and base-2 JSD.
- `src/trafficlab/comparison/similarity/mmd.py` — reference-scaled direction-delta random-feature MMD.
- `src/trafficlab/comparison/postfit/dispersion.py` — Fano/Allan curves.
- `src/trafficlab/comparison/postfit/transitions.py` — frozen-state transition fidelity.
- `src/trafficlab/comparison/postfit/c2st.py` — blocked deterministic logistic C2ST.
- `src/trafficlab/generation/models/nhpp.py` — piecewise-constant NHPP family.
- `src/trafficlab/generation/models/acd.py` — exponential ACD family.
- `src/trafficlab/generation/models/markov_packet_train/` — segmentation, fitted state, family, generation.
- `src/trafficlab/generation/models/packet_hmm/` — categorical inference, fitted state, family, generation.
- `scripts/derive_required_candidates_reference.py` — bounded packet-range derivation and manifest.
- `examples/required_candidates/` — small, medium, and big imported-reference configs plus instructions.

Existing cross-cutting owners:

- `src/trafficlab/common/config.py` — seven family tables, eight weights, post-fit settings.
- `src/trafficlab/common/scientific_schema.py` — schema-5 compatibility marker.
- `src/trafficlab/generation/models/{common,registry,fitted_schema,fitted_model}.py` — closed model universe and wire union.
- `src/trafficlab/fitting/genetic/` — coordinate metadata, eight-method trials, checkpoint compatibility.
- `src/trafficlab/comparison/{metrics,diagnostics,schema,codec,publication,stage}.py` — fitness/post-fit split and strict artifact.
- `src/trafficlab_dashboard/aspects/run_level.py` and `run_loader.py` — stored schema-5 diagnostics only.
- `scripts/generate_{artifact_schemas,fit_fixtures,model_fixtures,similarity_fixtures}.py` — deterministic schema-5 assets.
- `architecture/traffic_models/`, `architecture/similarity_methods/`, `architecture/{SYSTEM,TESTING,VISUALIZATION}.md` — normative behavior.

---

### Task 1 [TASK-1-75662814]: Generalize coordinate metadata and define family config value types

**Files:**
- Modify: `src/trafficlab/common/config.py`
- Modify: `src/trafficlab/generation/models/common.py`
- Modify: `src/trafficlab/generation/models/registry.py`
- Modify: `src/trafficlab/fitting/genetic/coordinates.py`
- Modify: `tests/unit/common/test_config_schema.py`
- Modify: `tests/unit/common/test_config_validation.py`
- Modify: `tests/unit/generation/models/test_registry.py`
- Modify: `tests/unit/fitting/genetic/test_coordinates.py`
- Modify: `architecture/genetic_models/basic_generational.md`

**Interfaces:**
- Produces: `GeneCoordinateKind = Literal["linear", "log", "integer"]`; four strict but not yet registered family config value types; generic coordinate construction with unchanged three-family runtime behavior.
- Consumes: existing `FamilyOperators`, `FloatBounds`, `IntegerBounds`, `ModelFamily`, and normalized coordinate functions.

- [ ] **[STEP-1-111c47f1] Write failing structural-config and coordinate-contract tests**

```python
def test_existing_families_declare_coordinate_kinds() -> None:
    assert POISSON_FAMILY.gene_coordinate_kinds == ("log",)
    assert MARKOV_RENEWAL_FAMILY.gene_coordinate_kinds == (
        "linear",
        "linear",
        "linear",
        "integer",
        "log",
    )
    assert MMPP_FAMILY.gene_coordinate_kinds == ("log", "log", "log", "log")


def test_required_structural_bound_types_are_strict() -> None:
    assert PacketHmmConfig(state_count=IntegerBounds(lower=2, upper=4)).state_count.upper == 4
    with pytest.raises(ValidationError):
        AcdConfig(order={"lower": 0, "upper": 3})
```

Add strict direct config-value cases for ACD order bounds outside `1..3`, HMM
state bounds outside `2..4`, train cap outside `3..8`, and NHPP bins outside
`2..16`. `ModelsConfig` does not expose these tables until the owning family is
registered.

- [ ] **[STEP-2-8815164b] Run the focused tests and preserve the expected red evidence**

Run:

```bash
uv run --locked pytest -q \
  tests/unit/common/test_config_schema.py \
  tests/unit/common/test_config_validation.py \
  tests/unit/generation/models/test_registry.py \
  tests/unit/fitting/genetic/test_coordinates.py
```

Expected: failures naming absent family config classes, invalid structural
bounds, and coordinate metadata. The runtime registry remains the
three fully implemented families until Tasks 8–11 add one working family at a
time.

- [ ] **[STEP-3-ff1226dd] Add family coordinate declarations**

Define the common metadata directly on `ModelFamily`:

```python
type GeneCoordinateKind = Literal["linear", "log", "integer"]


class ModelFamily(Protocol):
    @property
    def gene_names(self) -> tuple[str, ...]:
        """Return canonical coordinate names."""
        raise NotImplementedError

    @property
    def gene_coordinate_kinds(self) -> tuple[GeneCoordinateKind, ...]:
        """Return one coordinate kind per canonical name."""
        raise NotImplementedError
```

Make `family_coordinates()` zip `gene_names`, `gene_coordinate_kinds`, and exact configured bounds. Existing families declare their current kinds without changing chromosome semantics.

- [ ] **[STEP-4-6176b37e] Add strict required-family configuration models**

Implement exact integer structural bounds:

```python
class PacketHmmConfig(FamilyOperators):
    state_count: IntegerBounds


class MarkovPacketTrainConfig(FamilyOperators):
    length_cap: IntegerBounds


class AcdConfig(FamilyOperators):
    order: IntegerBounds


class NhppConfig(FamilyOperators):
    bin_count: IntegerBounds
```

Define the four config value types, but do not expose their tables through
`ModelsConfig` until the owning family task registers a complete implementation.
Do not change similarity weights or scientific schema in this task; Task 4
changes those contracts together with their runtime implementations and
fixtures.

- [ ] **[STEP-5-167183e8] Generalize bound and coordinate plumbing without registering incomplete families**

Make bounds/coordinate validation consume family-declared metadata and retain
the exact three-family registry. Add tests proving unregistered required-family
names remain rejected until their full implementation task and proving existing
family chromosomes and fixed-seed behavior are unchanged.

- [ ] **[STEP-6-8f78d252] Update the genetic coordinate documentation**

Document family-declared coordinate kinds in `basic_generational.md`. Keep the
normative registry at three families and scientific artifact schema at 4 until
Task 4 performs the coherent schema-5 transition.

- [ ] **[STEP-7-f87fe2c4] Run the Small tier for the shared contract**

```bash
uv run --locked pytest -q \
  tests/unit/common/test_config_schema.py \
  tests/unit/common/test_config_validation.py \
  tests/unit/generation/models/test_registry.py \
  tests/unit/fitting/genetic/test_coordinates.py
uv run --locked ruff check src/trafficlab/common/config.py src/trafficlab/generation/models src/trafficlab/fitting/genetic/coordinates.py
uv run --locked pyright src/trafficlab/common/config.py src/trafficlab/generation/models/common.py src/trafficlab/generation/models/registry.py src/trafficlab/fitting/genetic/coordinates.py
```

Expected: all focused tests pass; Ruff and Pyright exit zero.

- [ ] **[STEP-8-68c0ff55] Run the first Medium contract gate**

```bash
uv run --locked pytest -q tests/unit/common tests/unit/generation/models/test_registry.py tests/unit/fitting/genetic
uv run --locked pytest -q tests/integration/generation/test_model_pipeline.py -k 'poisson or markov or mmpp'
```

Do not run Docker, dashboard tests, fixture regeneration, or an experiment;
runtime behavior still contains only complete existing algorithms.

- [ ] **[STEP-9-1fc7cfc3] Request independent review and fix all blocking findings**

Review the exact task diff for compatibility, strict typing, accidental existing-family semantic changes, and architecture/spec compliance. Re-run Steps 7–8 after fixes.

- [ ] **[STEP-10-3e7001ec] Commit coordinate metadata and config value types**

```bash
git add src/trafficlab/common/config.py src/trafficlab/generation/models src/trafficlab/fitting/genetic \
  tests/unit/common tests/unit/generation/models/test_registry.py tests/unit/fitting/genetic \
  tests/integration/generation/test_model_pipeline.py architecture/genetic_models/basic_generational.md
git commit -m "refactor(genetic): declare coordinate kinds"
```

### Task 2 [TASK-2-ec6f79ae]: Add bounded CvM and Anderson–Darling fitness methods

**Files:**
- Create: `src/trafficlab/comparison/similarity/ecdf.py`
- Create: `tests/unit/comparison/similarity/test_ecdf.py`
- Modify: `src/trafficlab/comparison/similarity/__init__.py`
- Create: `architecture/similarity_methods/cramer_von_mises.md`
- Create: `architecture/similarity_methods/anderson_darling.md`
- Modify: `architecture/similarity_methods/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `cramer_von_mises_similarity(reference: TrafficTrace, generated: TrafficTrace, W: float, iat_weight: float, size_weight: float) -> SimilarityResult`; `anderson_darling_similarity(reference: TrafficTrace, generated: TrafficTrace, W: float, iat_weight: float, size_weight: float) -> SimilarityResult`.
- Consumes: immutable `TrafficTrace`, `iat_sample`, finite-number validation, and `SimilarityResult`.

- [ ] **[STEP-11-4977317a] Write hand-calculated failing ECDF tests**

Use direct arrays with ties and zero IATs. Include identical traces scoring `1.0`, disjoint singleton size samples scoring maximum discrepancy, pooled-support CvM arithmetic for `[1, 2]` versus `[1, 3]`, tail-amplified AD ordering, nonfinite rejection, and one trace missing a direction stratum.

```python
def test_cvm_hand_case_uses_pooled_empirical_mass() -> None:
    result = bounded_cvm_sample((1.0, 2.0), (1.0, 3.0))
    assert result.discrepancy == pytest.approx(0.0625)
```

- [ ] **[STEP-12-d07e60c0] Run ECDF tests red**

```bash
uv run --locked pytest -q tests/unit/comparison/similarity/test_ecdf.py
```

Expected: import failure for `trafficlab.comparison.similarity.ecdf`.

- [ ] **[STEP-13-a7795056] Implement the shared tie-aware pooled ECDF scan**

At each pooled unique support value, update both ECDFs once. CvM uses pooled empirical mass times squared ECDF difference. AD uses the same squared difference with tail weight `1 / (H * (1 - H))`, excludes `H == 1`, and divides by the sum of accepted tail weights so the discrepancy remains in `[0, 1]`.

- [ ] **[STEP-14-391dc5f5] Implement trace-level feature aggregation and typed diagnostics**

Combine IAT and frame-size component discrepancies under exact normalized weights. Retain sample counts, tie counts, per-feature raw sums, normalized discrepancies, direction-stratum availability, `W`, and final discrepancy. Report no p-value.

- [ ] **[STEP-15-4e1af563] Add independent scientific formulas to tests**

The test oracle performs its own sorted support/count scan and never imports production ECDF helpers. Cover random small integer samples against the production result with a deterministic seed.

- [ ] **[STEP-16-d713a4d9] Write normative algorithm documents**

Document exact formulas, pooled-mass convention, AD endpoint normalization, ties, empty strata, score mapping, cost, diagnostics, and direct hand examples. Update the similarity catalog and testing matrix.

- [ ] **[STEP-17-a014944f] Run the ECDF Small tier**

```bash
uv run --locked pytest -q tests/unit/comparison/similarity/test_ecdf.py
uv run --locked ruff check src/trafficlab/comparison/similarity/ecdf.py tests/unit/comparison/similarity/test_ecdf.py
uv run --locked pyright src/trafficlab/comparison/similarity/ecdf.py tests/unit/comparison/similarity/test_ecdf.py
```

- [ ] **[STEP-18-30ab89de] Review, fix, and commit ECDF methods**

After independent review reports no Critical or Important finding:

```bash
git add src/trafficlab/comparison/similarity tests/unit/comparison/similarity/test_ecdf.py \
  architecture/similarity_methods architecture/TESTING.md
git commit -m "feat(similarity): add bounded ECDF distances"
```

### Task 3 [TASK-3-9b684938]: Add Jensen–Shannon and approximate joint MMD

**Files:**
- Create: `src/trafficlab/comparison/similarity/jensen_shannon.py`
- Create: `src/trafficlab/comparison/similarity/mmd.py`
- Create: `tests/unit/comparison/similarity/test_jensen_shannon.py`
- Create: `tests/unit/comparison/similarity/test_mmd.py`
- Create: `architecture/similarity_methods/jensen_shannon.md`
- Create: `architecture/similarity_methods/approximate_mmd.md`
- Modify: `architecture/similarity_methods/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `jensen_shannon_similarity(reference: TrafficTrace, generated: TrafficTrace, W: float, iat_bin_count: int, iat_weight: float, mark_weight: float) -> SimilarityResult`; `approximate_mmd_similarity(reference: TrafficTrace, generated: TrafficTrace, W: float, feature_count: int, seed: int, scale_floor: float) -> SimilarityResult`; streaming `RandomFeatureMean` test surface.
- Consumes: schema-5 JS/MMD settings from `SimilarityConfig` and exact canonical trace columns.

- [ ] **[STEP-19-47acb948] Write failing exact-JSD tests**

Cover equal PMFs, disjoint PMFs giving base-2 JSD `1`, zero-mass handling without pseudocounts, exact `(direction, frame_length)` categories, `log1p(IAT)` bins fixed by reference `W`, and generated IATs at bin endpoints.

- [ ] **[STEP-20-155d522f] Write failing deterministic-MMD tests**

```python
def test_mmd_direction_has_no_numeric_order() -> None:
    first = feature_mean(trace_with_direction_codes((0, 1)), settings)
    swapped = feature_mean(trace_with_direction_codes((1, 0)), settings)
    assert first.shape == swapped.shape
    assert np.all(np.isfinite(first))
```

Also cover exact repeatability, identical traces score `1`, reference-only scale use, unit feature norm, mean-embedding distance at most two, and changed MMD seed changing features but not validation shape.

- [ ] **[STEP-21-625a62b9] Run JSD/MMD tests red**

```bash
uv run --locked pytest -q \
  tests/unit/comparison/similarity/test_jensen_shannon.py \
  tests/unit/comparison/similarity/test_mmd.py
```

- [ ] **[STEP-22-fb533303] Implement exact aligned PMFs and base-2 JSD**

Use integer count maps, union-aligned support, and terms only where `p > 0` or `q > 0`. Combine exact joint marks and direction-conditioned IAT bins under configured weights; retain counts and component JSD values.

- [ ] **[STEP-23-8c540395] Implement streaming unit-norm random Fourier features**

Use paired cosine/sine coordinates scaled by `1/sqrt(K)`, reference-only continuous mean/scale, and separate outbound/inbound feature blocks. Accumulate means without an `N x D` matrix. Define `discrepancy = norm(mean_r - mean_g) / 2` and reject only roundoff outside `[0, 1]`.

- [ ] **[STEP-24-12309636] Add independent JSD and Fourier-feature oracles**

Tests calculate entropy/JSD from `fractions.Fraction` for small PMFs and construct a tiny explicit cosine/sine map from frozen frequencies. Production helpers are not imported by the oracle.

- [ ] **[STEP-25-6537a6a0] Document both algorithms and configuration choices**

Document PMF support, bin edges, zero policy, direction delta kernel, reference-only scaling, feature norm, seed, dimension, cost, score bounds, and no IID p-value.

- [ ] **[STEP-26-fc3ef71a] Run the JSD/MMD Small tier**

```bash
uv run --locked pytest -q tests/unit/comparison/similarity/test_jensen_shannon.py tests/unit/comparison/similarity/test_mmd.py
uv run --locked ruff check src/trafficlab/comparison/similarity tests/unit/comparison/similarity
uv run --locked pyright src/trafficlab/comparison/similarity/jensen_shannon.py src/trafficlab/comparison/similarity/mmd.py
```

- [ ] **[STEP-27-8b39c78c] Review, fix, and commit JSD/MMD**

```bash
git add src/trafficlab/comparison/similarity tests/unit/comparison/similarity \
  architecture/similarity_methods architecture/TESTING.md
git commit -m "feat(similarity): add JS and approximate MMD"
```

### Task 4 [TASK-4-050cdc9b]: Expand genetic fitness and comparison artifacts to eight methods

**Files:**
- Modify: `src/trafficlab/common/scientific_schema.py`
- Modify: `src/trafficlab/common/config.py`
- Modify: `src/trafficlab/comparison/metrics.py`
- Modify: `src/trafficlab/comparison/diagnostics.py`
- Modify: `src/trafficlab/comparison/schema.py`
- Modify: `src/trafficlab/comparison/{codec,publication,stage}.py`
- Modify: `src/trafficlab/fitting/genetic/{types,evaluation}.py`
- Modify: `src/trafficlab/fitting/genetic/checkpoint/{schema,codec,state,compatibility}.py`
- Modify: `src/trafficlab/artifact_schemas.py`
- Modify: `scripts/generate_{artifact_schemas,fit_fixtures,model_fixtures,similarity_fixtures}.py`
- Replace: `examples/schemas/scientific-artifact-v4/` with generated `examples/schemas/scientific-artifact-v5/` for the current three fitted families and eight-method comparison
- Modify: `examples/configs/{default,minimal}.toml`
- Modify: `examples/data/` and nonhistorical `tests/fixtures/` schema-5 artifacts
- Modify: `tests/unit/comparison/`
- Modify: `tests/unit/fitting/genetic/`
- Modify: `tests/integration/comparison/`
- Modify: `tests/unit/tooling/test_artifact_schema_generator.py`
- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/similarity_methods/README.md`

**Interfaces:**
- Produces: schema version 5; canonical `FITNESS_METHOD_NAMES` tuple of eight names; complete strict similarity settings; typed diagnostics union; eight-entry `TrialResult`; schema-5 `ComparisonMethods` and current three-family fixtures.
- Consumes: four existing and four new pure similarity functions.

- [ ] **[STEP-28-1b86da1d] Write failing eight-method aggregate and checkpoint tests**

Assert exact method order, one-hot and mixed arithmetic, zero-weight execution/failure, canonical JSON order, `SCIENTIFIC_ARTIFACT_SCHEMA_VERSION == 5`, schema-4 checkpoint and best-model rejection, eight-method trial tuples, and resume incompatibility when any new setting changes.

- [ ] **[STEP-29-6c81ea8b] Run the integration tests red**

```bash
uv run --locked pytest -q tests/unit/comparison tests/unit/fitting/genetic tests/integration/comparison
```

Expected: failures at fixed four-method literals, unions, tuple lengths, and schemas.

- [ ] **[STEP-30-0c46636e] Add typed diagnostics and method registry constants**

Create one canonical tuple:

```python
FITNESS_METHOD_NAMES = (
    "autocorrelation",
    "frame_size_ks",
    "iat_ks",
    "multiscale_rate",
    "cramer_von_mises",
    "anderson_darling",
    "jensen_shannon",
    "approximate_mmd",
)
```

Use it in runtime schema iteration, checkpoint tuple construction, publication arithmetic, and tests. Keep typed Pydantic fields rather than an unvalidated arbitrary mapping.

Add the four new `MethodWeights` fields and every CvM/AD/JS/MMD setting from
the specification to `SimilarityConfig`; update the existing three-family
default and minimal configs with valid values.

- [ ] **[STEP-31-7b4153f8] Wire all eight methods into fitness evaluation**

`evaluate_fitness()` eagerly runs every method, validates every score/diagnostic, applies configured weights with `math.fsum`, and returns the typed aggregate. Genetic evaluation calls this function and no post-fit module.

- [ ] **[STEP-32-e2480242] Widen checkpoint and resume compatibility**

Bind the full schema-5 similarity settings identity, serialize eight trial component scores in canonical order, reject schema 4, and retain existing failure classification for candidate metric preconditions.

- [ ] **[STEP-33-954f32d4] Update comparison codec/publication arithmetic**

Validate exact eight keys, diagnostics discriminators, each `score = 1 - discrepancy` contract, weight normalization, and aggregate equality. Reject extra, missing, reordered wire fields under canonical rendering rules.

Bump the global schema marker to 5, regenerate current checkpoint/comparison
fixtures and JSON Schemas, and prove schema 4 fails with the documented refit
message. Do not add new model payloads before their family tasks.

- [ ] **[STEP-34-0b2ba777] Update normative system and similarity contracts**

Replace “four mandatory methods” and “fixed four-method shape” with the exact schema-5 eight-method definition. Explain that final-only diagnostics are separate and not represented by a zero weight.

- [ ] **[STEP-35-7c2c97e1] Run the weighted-fitness Medium gate**

```bash
uv run --locked pytest -q tests/unit/comparison tests/unit/fitting/genetic tests/integration/comparison
uv run --locked pytest -q tests/integration/generation/test_model_pipeline.py -k 'poisson or markov or mmpp'
uv run --locked pytest -q tests/unit/tooling/test_artifact_schema_generator.py
uv run --locked python scripts/generate_artifact_schemas.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_similarity_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
uv run --locked ruff check src/trafficlab/comparison src/trafficlab/fitting/genetic tests/unit/comparison tests/unit/fitting/genetic
uv run --locked pyright src/trafficlab/comparison src/trafficlab/fitting/genetic
```

Do not run post-fit, dashboard, Docker, or experiments yet.

- [ ] **[STEP-36-e525c65b] Measure changed-package branch coverage**

```bash
uv run --locked pytest -q --cov=trafficlab.comparison --cov=trafficlab.fitting.genetic \
  --cov-branch --cov-report=term-missing tests/unit/comparison tests/unit/fitting/genetic tests/integration/comparison
```

Add focused cases until changed functions satisfy the project coverage rules.

- [ ] **[STEP-37-9222f6cd] Review, fix, and commit eight-method fitness**

```bash
git add src/trafficlab/common src/trafficlab/comparison src/trafficlab/fitting/genetic src/trafficlab/artifact_schemas.py \
  scripts/generate_artifact_schemas.py scripts/generate_model_fixtures.py \
  scripts/generate_similarity_fixtures.py scripts/generate_fit_fixtures.py \
  examples/configs examples/schemas examples/data tests/fixtures \
  tests/unit/comparison tests/unit/fitting/genetic tests/unit/tooling/test_artifact_schema_generator.py \
  tests/integration/comparison \
  architecture/SYSTEM.md architecture/similarity_methods
git commit -m "feat(schema): publish eight-method schema 5"
```

### Task 5 [TASK-5-720bec0d]: Add Fano/Allan and transition post-fit diagnostics

**Files:**
- Create: `src/trafficlab/comparison/postfit/__init__.py`
- Create: `src/trafficlab/comparison/postfit/dispersion.py`
- Create: `src/trafficlab/comparison/postfit/transitions.py`
- Create: `tests/unit/comparison/postfit/test_dispersion.py`
- Create: `tests/unit/comparison/postfit/test_transitions.py`
- Create: `architecture/similarity_methods/fano_allan.md`
- Create: `architecture/similarity_methods/transition_matrix.md`
- Modify: `architecture/similarity_methods/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `fano_allan_diagnostic(reference: TrafficTrace, generated: TrafficTrace, W: float, widths: tuple[float, ...], scale_weights: tuple[float, ...], fano_weight: float, allan_weight: float) -> SimilarityResult`; `transition_matrix_diagnostic(reference: TrafficTrace, generated: TrafficTrace, W: float, size_bin_count: int, iat_bin_count: int, pseudocount: float, component_weights: tuple[float, float, float]) -> SimilarityResult`.
- Consumes: final normalized traces and post-fit configuration only.

- [ ] **[STEP-38-029deb5c] Write failing Fano/Allan hand cases**

Cover constant one-packet windows (`Fano=0`, `Allan=0`), alternating counts, all-zero direction channel, endpoint `t=W`, insufficient windows, scale weights, and bounded `log1p` discrepancy arithmetic.

- [ ] **[STEP-39-759adbca] Write failing transition hand cases**

Cover Type-7 reference thresholds, outbound/inbound categorical separation, generated values outside reference bins, hand-counted occupancy/transition rows, additive smoothing, empty source rows, run-length PMFs, and identical traces scoring one.

- [ ] **[STEP-40-4307f5a9] Run post-fit tests red**

```bash
uv run --locked pytest -q tests/unit/comparison/postfit/test_dispersion.py tests/unit/comparison/postfit/test_transitions.py
```

- [ ] **[STEP-41-3400c53f] Implement dispersion curves**

Use existing four-ULP bin-boundary semantics. Define `Fano = variance / mean` and `Allan = mean(diff(counts)**2) / (2 * mean)` with an explicit zero-mean result of zero. Retain raw counts, window counts, both curves, component differences, weights, and score.

- [ ] **[STEP-42-ad099485] Implement reference-frozen transition fidelity**

Build the vocabulary once from reference log-size/log-IAT thresholds plus direction, add explicit edge categories, count occupancy/transitions/runs, smooth only the declared active vocabulary, and combine occupancy/row/run JSD under configured weights.

- [ ] **[STEP-43-da0ea413] Add independent count and transition oracles**

Tests use small Python integer lists and `Fraction`-based PMFs, not production binning/JSD helpers. Verify every diagnostic field against hand counts.

- [ ] **[STEP-44-446a31b0] Document post-fit semantics**

State explicitly that these two diagnostics are final-only, unweighted, deterministic, blocked from GA evaluation, and subject to exact minimum-window/state caps.

- [ ] **[STEP-45-336d0126] Run the post-fit Small tier**

```bash
uv run --locked pytest -q tests/unit/comparison/postfit
uv run --locked ruff check src/trafficlab/comparison/postfit tests/unit/comparison/postfit
uv run --locked pyright src/trafficlab/comparison/postfit tests/unit/comparison/postfit
```

- [ ] **[STEP-46-aa64509c] Review, fix, and commit dispersion/transition diagnostics**

```bash
git add src/trafficlab/comparison/postfit tests/unit/comparison/postfit \
  architecture/similarity_methods architecture/TESTING.md
git commit -m "feat(diagnostics): add traffic structure checks"
```

### Task 6 [TASK-6-eb02bdb7]: Add deterministic C2ST and final-only artifact publication

**Files:**
- Create: `src/trafficlab/comparison/postfit/c2st.py`
- Create: `tests/unit/comparison/postfit/test_c2st.py`
- Modify: `src/trafficlab/comparison/schema.py`
- Modify: `src/trafficlab/comparison/{metrics,stage,codec,publication}.py`
- Modify: `tests/unit/comparison/{test_metrics,test_schema,test_stage,test_codec,test_publication}.py`
- Modify: `tests/integration/comparison/test_comparison_pipeline.py`
- Create: `architecture/similarity_methods/classical_c2st.md`
- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/similarity_methods/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `evaluate_postfit(reference: TrafficTrace, generated: TrafficTrace, W: float, settings: SimilarityConfig) -> PostfitDiagnostics`; schema-5 `postfit_diagnostics` field; `classical_c2st_diagnostic(reference: TrafficTrace, generated: TrafficTrace, W: float, settings: C2stSettings) -> SimilarityResult`.
- Consumes: Fano/Allan and transition functions plus fixed window features.

- [ ] **[STEP-47-dd63600a] Write failing blocked-window and logistic tests**

Cover exact window features, reference-only standardization, fold and guard indexes, no adjacent leakage, balanced labels, deterministic coefficients, separable AUC `1`, indistinguishable AUC `0.5`, coefficient validation, solver nonconvergence, and insufficient windows.

- [ ] **[STEP-48-4ddc7d9b] Run C2ST and artifact tests red**

```bash
uv run --locked pytest -q tests/unit/comparison/postfit/test_c2st.py tests/unit/comparison/test_schema.py tests/unit/comparison/test_stage.py
```

- [ ] **[STEP-49-3adb4d0d] Implement fixed window features and guarded folds**

Use outbound/inbound packets and bytes, frame-size mean/quantiles, positive-IAT mean/quantiles, zero-IAT count, and activity count. Split contiguous folds with configured guard windows removed from both train and evaluation sets.

- [ ] **[STEP-50-5a1cc4e8] Implement deterministic L2 logistic regression and AUC**

Use SciPy `minimize` with analytic loss/gradient, zero initialization, fixed tolerance/iterations, and no RNG. Implement rank/tie-aware AUC locally and expose an independent test oracle. Define `score = 1 - 2 * abs(AUC - 0.5)` with only roundoff clamping.

- [ ] **[STEP-51-2d1bf47e] Add the final-only comparison boundary**

`evaluate_fitness()` remains the only GA entry. `evaluate_postfit()` calls exactly three diagnostics. The final comparison stage joins both typed results; genetic evaluation tests monkeypatch post-fit functions to raise if called and prove they remain untouched.

- [ ] **[STEP-52-b412a5c4] Extend strict comparison publication**

Add typed diagnostic unions and exact keys under `postfit_diagnostics`, validate shared `W`, score arithmetic, solver/convergence fields, canonical serialization, and rejection of post-fit fields in trial/checkpoint payloads.

- [ ] **[STEP-53-c3159552] Document C2ST and artifact separation**

Document feature version, folds, guard blocks, reference-only scaling, solver, AUC, score mapping, preconditions, and the reason C2ST is never GA fitness.

- [ ] **[STEP-54-5d7100f1] Run the post-fit Medium gate**

```bash
uv run --locked pytest -q tests/unit/comparison tests/integration/comparison
uv run --locked ruff check src/trafficlab/comparison tests/unit/comparison tests/integration/comparison
uv run --locked pyright src/trafficlab/comparison tests/unit/comparison
uv run --locked pytest -q --cov=trafficlab.comparison --cov-branch --cov-report=term-missing \
  tests/unit/comparison tests/integration/comparison
```

Do not run dashboard, models, Docker, or Moutai experiments.

- [ ] **[STEP-55-2a763067] Review and fix the complete post-fit subsystem**

Require independent checks for temporal leakage, generated-data influence on reference transforms, GA isolation, strict artifact arithmetic, and resource caps. Repeat Step 54 after fixes.

- [ ] **[STEP-56-b37f61dd] Commit C2ST and post-fit publication**

```bash
git add src/trafficlab/comparison tests/unit/comparison tests/integration/comparison \
  architecture/SYSTEM.md architecture/similarity_methods architecture/TESTING.md
git commit -m "feat(diagnostics): add final-only C2ST"
```

### Task 7 [TASK-7-9c058d21]: Render eight fitness methods and three post-fit diagnostics

**Files:**
- Modify: `src/trafficlab_dashboard/run_loader.py`
- Modify: `src/trafficlab_dashboard/run_data.py`
- Modify: `src/trafficlab_dashboard/aspects/run_level.py`
- Modify: `tests/trafficlab_dashboard/unit/test_run_level_aspects.py`
- Modify: `tests/trafficlab_dashboard/integration/test_run_loader.py`
- Modify: `architecture/VISUALIZATION.md`

**Interfaces:**
- Produces: score-bar ordering for eight methods; `FanoAllanAspect`, `TransitionFidelityAspect`, `C2stAspect` using stored diagnostics.
- Consumes: strict schema-5 `ComparisonResult`; performs no scientific recomputation.

- [ ] **[STEP-57-c478514b] Write failing dashboard loader/aspect tests**

Assert all eight labels and aggregate ordering, disabled aspects without a valid schema-5 artifact, exact Fano/Allan scale series, transition occupancy/row series, C2ST AUC/coefficient data, and no calls into comparison calculators.

- [ ] **[STEP-58-54c74cda] Run dashboard tests red**

```bash
uv run --locked pytest -q tests/trafficlab_dashboard/unit/test_run_level_aspects.py tests/trafficlab_dashboard/integration/test_run_loader.py
```

- [ ] **[STEP-59-23c07c4c] Extend immutable dashboard run data**

Expose stored post-fit records through typed read-only fields and preserve exact unavailable-reason handling for absent or invalid artifacts.

- [ ] **[STEP-60-7131972b] Add three run-level aspect calculators/renderers**

Fano/Allan renders reference/generated curves; transition renders component discrepancies and occupancy; C2ST renders AUC/balanced accuracy plus coefficient magnitude. All use the established single-canvas interaction and export path.

- [ ] **[STEP-61-0636a7bd] Update visualization architecture**

Define the new aspects, labels, axes, availability, stored-data-only boundary, and schema-4 incompatibility. Preserve display-only uplink/downlink translation while artifacts remain outbound/inbound.

- [ ] **[STEP-62-9f3ff607] Run the dashboard Small tier**

```bash
uv run --locked pytest -q tests/trafficlab_dashboard/unit/test_run_level_aspects.py tests/trafficlab_dashboard/integration/test_run_loader.py
uv run --locked ruff check src/trafficlab_dashboard tests/trafficlab_dashboard
uv run --locked pyright src/trafficlab_dashboard tests/trafficlab_dashboard
```

- [ ] **[STEP-63-e838c691] Run one headless schema-5 aspect smoke**

```bash
QT_QPA_PLATFORM=offscreen uv run --locked --extra dashboard pytest -q \
  tests/trafficlab_dashboard/integration/test_run_loader.py
```

- [ ] **[STEP-64-765c5332] Review and fix dashboard ownership**

Independent review must confirm no metric recomputation, no GUI-thread blocking regression, correct terminology, and immutable result handling.

- [ ] **[STEP-65-015b63a4] Commit dashboard support**

```bash
git add src/trafficlab_dashboard tests/trafficlab_dashboard architecture/VISUALIZATION.md
git commit -m "feat(dashboard): show required diagnostics"
```

### Task 8 [TASK-8-0cba90b0]: Implement the piecewise-constant NHPP family

**Files:**
- Modify: `src/trafficlab/common/config.py`
- Create: `src/trafficlab/generation/models/nhpp.py`
- Modify: `src/trafficlab/generation/models/{registry,fitted_schema,fitted_model,__init__}.py`
- Create: `tests/unit/generation/models/test_nhpp.py`
- Modify: `tests/unit/common/test_config_validation.py`
- Modify: `tests/support/config.py`
- Modify: `tests/scientific/generation/oracles.py`
- Modify: `tests/scientific/generation/test_model_validation.py`
- Create: `architecture/traffic_models/nhpp.md`
- Modify: `architecture/traffic_models/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `NhppFamily`, `NhppModel`, `NhppPayload`; `FamilyName`/`ModelsConfig`/registry support for `nhpp` with integer `bin_count`.
- Consumes: generation guard, mark distribution, fit validation, PCG64, schema-5 payload union.

- [ ] **[STEP-66-9852321e] Write failing NHPP fit/generation/codec tests**

Cover hand bin counts excluding `t=0`, exact rates, zero-rate bins, active-bin/global marks, integer repair endpoints, `t=W`, crossing empty bins, every guard, draw order, payload corruption, and same-seed equality.

- [ ] **[STEP-67-ef550288] Add independent NHPP scientific oracles**

For declared rates and widths, test generated per-bin means against analytic `lambda_b * width_b`, integrated total count, zero-bin absence, and bin-conditional mark frequencies under predeclared seeds/tolerances.

- [ ] **[STEP-68-7fea1f19] Run NHPP tests red**

```bash
uv run --locked pytest -q tests/unit/generation/models/test_nhpp.py tests/scientific/generation/test_model_validation.py -k nhpp
```

- [ ] **[STEP-69-08ed2f8f] Implement deterministic NHPP fit and payload validation**

Fit equal-width bin rates, integrated intensity, exact edges, bin mark tables, global fallback, estimator choices, and strict finite validation. Exclude only the conditioned first packet from the rate counts.

- [ ] **[STEP-70-682aa66a] Implement exact complete-window NHPP generation**

Consume one initial mark, then exponential clocks within nonzero bins; cross zero/exhausted bins without mark draws. Check guards at the common boundaries and retain exact draw order.

- [ ] **[STEP-71-1f2d96fc] Integrate registry and fitted-model codecs**

Add `nhpp` atomically to `FamilyName`, `ModelsConfig`, registry, exact bounds
reconstruction, strict payload discriminator, load/dump, estimator choices, and
best-model round trips. No scaffold exists from Task 1.

- [ ] **[STEP-72-8624c746] Write the NHPP architecture document**

Include intensity estimator, conditioning at zero, bin endpoints, zero rates, marks/fallbacks, RNG order, cost, limits, examples, and direct validation matrix.

- [ ] **[STEP-73-f123f7d0] Run the NHPP Small tier**

```bash
uv run --locked pytest -q tests/unit/generation/models/test_nhpp.py tests/unit/generation/models/test_registry.py \
  tests/scientific/generation/test_model_validation.py -k 'nhpp or registry'
uv run --locked ruff check src/trafficlab/generation/models/nhpp.py tests/unit/generation/models/test_nhpp.py
uv run --locked pyright src/trafficlab/generation/models/nhpp.py tests/unit/generation/models/test_nhpp.py
```

- [ ] **[STEP-74-6f476273] Review, fix, and commit NHPP**

```bash
git add src/trafficlab/common/config.py src/trafficlab/generation/models tests/support/config.py tests/unit/common/test_config_validation.py \
  tests/unit/generation/models tests/scientific/generation \
  architecture/traffic_models architecture/TESTING.md
git commit -m "feat(models): add piecewise NHPP"
```

### Task 9 [TASK-9-7bbda026]: Implement exponential ACD

**Files:**
- Modify: `src/trafficlab/common/config.py`
- Create: `src/trafficlab/generation/models/acd.py`
- Modify: `src/trafficlab/generation/models/{registry,fitted_schema,fitted_model,__init__}.py`
- Create: `tests/unit/generation/models/test_acd.py`
- Modify: `tests/unit/common/test_config_validation.py`
- Modify: `tests/support/config.py`
- Modify: `tests/scientific/generation/{oracles.py,test_model_validation.py}`
- Create: `architecture/traffic_models/acd.md`
- Modify: `architecture/traffic_models/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `AcdFamily`, `AcdModel`, `AcdPayload`; `FamilyName`/`ModelsConfig`/registry support for exponential ACD(`p`,`p`) with `p in 1..3`.
- Consumes: SciPy optimizer, joint empirical marks, common generation guards.

- [ ] **[STEP-75-184052e5] Write failing ACD recursion, likelihood, and repair tests**

Hand-check `psi_i`, exponential negative log likelihood, coefficient transform, `sum(alpha)+sum(beta)<1`, order repair, zeros, optimizer nonconvergence, payload corruption, marks, endpoint and guard behavior.

- [ ] **[STEP-76-8149035f] Add independent ACD scientific oracles**

Test stationary mean `omega / (1 - sum(alpha) - sum(beta))`, unit-mean innovations `Delta/psi`, recursion values, and generated mark frequencies with fixed predeclared tolerances.

- [ ] **[STEP-77-f64e33c9] Run ACD tests red**

```bash
uv run --locked pytest -q tests/unit/generation/models/test_acd.py tests/scientific/generation/test_model_validation.py -k acd
```

- [ ] **[STEP-78-66f12e58] Implement constrained deterministic ACD fitting**

Use one documented unconstrained-to-stationary parameter transform, zero initialization derived from reference mean, analytic likelihood/gradient where practical, fixed solver/tolerance/iterations, and explicit nonconvergence failure.

- [ ] **[STEP-79-11f61ece] Implement ACD generation and strict payload**

Initialize prior durations/conditional means exactly, draw scalar unit
exponentials, recurse, emit joint marks, and enforce common
complete-window/guard semantics. Add the complete family atomically to
`FamilyName`, `ModelsConfig`, registry, bounds reconstruction, and payload union.

- [ ] **[STEP-80-efb2baa5] Write the ACD architecture document**

Document equations, estimator transform, initialization, zeros, stationarity, RNG order, payload, errors, cost, hand cases, and direct scientific validation.

- [ ] **[STEP-81-98b58a33] Run the two-family Medium gate**

```bash
uv run --locked pytest -q tests/unit/generation/models tests/scientific/generation \
  tests/integration/generation/test_model_pipeline.py -k 'nhpp or acd or registry or contract'
uv run --locked ruff check src/trafficlab/generation/models tests/unit/generation/models tests/scientific/generation
uv run --locked pyright src/trafficlab/generation/models/nhpp.py src/trafficlab/generation/models/acd.py \
  tests/unit/generation/models/test_nhpp.py tests/unit/generation/models/test_acd.py
```

No Moutai experiment runs until Task 13 provides the reproducible derivation/profile tooling.

- [ ] **[STEP-82-01bcdb89] Review and fix NHPP/ACD integration**

Review optimizer determinism, stationary constraints, schema-5 payloads, existing-family non-regression, and medium coverage. Repeat Step 81.

- [ ] **[STEP-83-76bbeb1f] Commit ACD**

```bash
git add src/trafficlab/common/config.py src/trafficlab/generation/models tests/support/config.py tests/unit/common/test_config_validation.py \
  tests/unit/generation/models tests/scientific/generation \
  tests/integration/generation/test_model_pipeline.py architecture/traffic_models architecture/TESTING.md
git commit -m "feat(models): add exponential ACD"
```

### Task 10 [TASK-10-ee746d74]: Implement the Markov packet-train family

**Files:**
- Modify: `src/trafficlab/common/config.py`
- Create: `src/trafficlab/generation/models/markov_packet_train/__init__.py`
- Create: `src/trafficlab/generation/models/markov_packet_train/{model,segmentation,family,generation}.py`
- Modify: `src/trafficlab/generation/models/{registry,fitted_schema,fitted_model}.py`
- Create: `tests/unit/generation/models/markov_packet_train/`
- Modify: `tests/unit/common/test_config_validation.py`
- Modify: `tests/support/config.py`
- Modify: `tests/scientific/generation/{oracles.py,test_model_validation.py}`
- Create: `architecture/traffic_models/markov_packet_train.md`
- Modify: `architecture/traffic_models/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `segment_trains()`, `MarkovPacketTrainFamily`, fitted/payload types, `FamilyName`/`ModelsConfig`/registry support, and no whole-train template storage.
- Consumes: Type-7 quantile helper, weighted sampling, mark and timing fallbacks, generation guards.

- [ ] **[STEP-84-e8df7ebc] Write failing segmentation and state tests**

Cover equality at the q90 gap threshold, first/interior/last position classes, capped train states, single-packet trains, final train, transition counts, actual-length reservoirs, and no persisted whole-trace/train template field.

- [ ] **[STEP-85-9fd84e2a] Write failing generation/fallback/codec tests**

Hand-script initial state, actual length, state transition, within gaps, marks, transition/source/global inter-train gap fallback, `t=W`, mid-train guard exhaustion, payload corruption, and fixed draw order.

- [ ] **[STEP-86-06d11f90] Add independent packet-train oracles**

Use a hand trace with three known trains to verify segmentation, transition matrix, train-state occupancy, actual-length distribution, within-gap bounds, inter-train gaps, and mark-position frequencies.

- [ ] **[STEP-87-71d62f06] Run train tests red**

```bash
uv run --locked pytest -q tests/unit/generation/models/markov_packet_train \
  tests/scientific/generation/test_model_validation.py -k packet_train
```

- [ ] **[STEP-88-e1693297] Implement segmentation, fitted state, and payload**

Freeze Type-7 q90, build capped length states, individual position mark/IAT pools, smoothed transitions, and the three-tier gap fallback. Validate every row, pool, state, and diagnostic counter.

- [ ] **[STEP-89-966e0201] Implement train-by-train generation**

Sample state and actual length, then individual position-conditioned marks and within gaps. Check the clock and all guards between packets; never draw or replay an entire stored train.

- [ ] **[STEP-90-f6a98be4] Integrate codecs and write architecture**

Add the complete family atomically to `FamilyName`, `ModelsConfig`, registry,
bounds/load/dump/discriminator, and document threshold, states, fallbacks, RNG
order, guards, non-replay rule, cost, examples, and scientific checks.

- [ ] **[STEP-91-81aebb72] Run the packet-train Small tier and review**

```bash
uv run --locked pytest -q tests/unit/generation/models/markov_packet_train tests/unit/generation/models/test_registry.py \
  tests/scientific/generation/test_model_validation.py -k 'packet_train or registry'
uv run --locked ruff check src/trafficlab/generation/models/markov_packet_train tests/unit/generation/models/markov_packet_train
uv run --locked pyright src/trafficlab/generation/models/markov_packet_train tests/unit/generation/models/markov_packet_train
```

Fix every review finding and repeat the command.

- [ ] **[STEP-92-18a7eac8] Commit packet trains**

```bash
git add src/trafficlab/common/config.py src/trafficlab/generation/models tests/support/config.py tests/unit/common/test_config_validation.py \
  tests/unit/generation/models/markov_packet_train \
  tests/scientific/generation architecture/traffic_models architecture/TESTING.md
git commit -m "feat(models): add Markov packet trains"
```

### Task 11 [TASK-11-99735de2]: Implement the categorical packet HMM

**Files:**
- Modify: `src/trafficlab/common/config.py`
- Create: `src/trafficlab/generation/models/packet_hmm/__init__.py`
- Create: `src/trafficlab/generation/models/packet_hmm/{inference,model,family,generation}.py`
- Modify: `src/trafficlab/generation/models/{registry,fitted_schema,fitted_model}.py`
- Create: `tests/unit/generation/models/packet_hmm/`
- Modify: `tests/unit/common/test_config_validation.py`
- Modify: `tests/support/config.py`
- Modify: `tests/scientific/generation/{oracles.py,test_model_validation.py}`
- Create: `architecture/traffic_models/packet_hmm.md`
- Modify: `architecture/traffic_models/README.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: scaled/log-space forward-backward, deterministic Baum–Welch, canonical state ordering, `PacketHmmFamily`/payload, and final seven-name `FamilyName`/`ModelsConfig`/registry.
- Consumes: categorical vocabulary of IAT bin × direction × size bin and raw individual-category reservoirs.

- [ ] **[STEP-93-6fa0da5e] Write failing vocabulary and tiny-likelihood tests**

Cover explicit zero-IAT category, Type-7 thresholds, only observed vocabulary entries, forward likelihood against brute-force enumeration for two states/two observations, posterior row sums, and state canonicalization.

- [ ] **[STEP-94-f1c44ffd] Write failing EM/generation/codec tests**

Cover fixed initialization, monotone likelihood within tolerance, additive smoothing, iteration cap, nonconvergence policy, category reservoirs, hidden transition/emission draws, initial mark, `t=W`, guards, payload corruption, and repeatability.

- [ ] **[STEP-95-86c37f6b] Add independent HMM scientific oracles**

Enumerate every hidden path for a tiny sequence, calculate stationary hidden distribution independently, and test long-run state/emission/category frequencies with predeclared seeds and tolerances.

- [ ] **[STEP-96-1fca2ed1] Run HMM tests red**

```bash
uv run --locked pytest -q tests/unit/generation/models/packet_hmm \
  tests/scientific/generation/test_model_validation.py -k packet_hmm
```

- [ ] **[STEP-97-68cd1e47] Implement stable deterministic HMM inference**

Use scaled or log-space forward/backward recursions, bounded Baum–Welch, declared initialization/smoothing, finite checks, convergence diagnostics, and canonical state ordering by expected IAT category then emission/transition vectors.

- [ ] **[STEP-98-f87203d6] Implement fitted payload and generation**

Persist vocabulary, thresholds, probability tables, individual category reservoirs, estimator settings, and convergence record. Generate hidden state → category → raw member, with no stored subsequence templates.

- [ ] **[STEP-99-8f753340] Integrate codecs and write HMM architecture**

Add the complete family atomically to the closed registry and strict
union/bounds/load/dump, then document observations, EM, label order, smoothing,
category sampling, RNG order, guards, cost, hand cases, and scientific evidence.

- [ ] **[STEP-100-5d590173] Run the seven-family Medium gate**

```bash
uv run --locked pytest -q tests/unit/generation/models tests/scientific/generation tests/integration/generation
uv run --locked pytest -q tests/unit/fitting/genetic -k 'population or coordinates or evaluation or checkpoint'
uv run --locked ruff check src/trafficlab/generation/models tests/unit/generation/models tests/scientific/generation
uv run --locked pyright src/trafficlab/generation/models tests/unit/generation/models tests/scientific/generation
uv run --locked pytest -q --cov=trafficlab.generation.models --cov-branch --cov-report=term-missing \
  tests/unit/generation/models tests/scientific/generation tests/integration/generation
```

- [ ] **[STEP-101-2a5e31e5] Review, fix, and commit packet HMM**

Require independent likelihood/oracle, convergence, label, RNG, payload, and no-replay review. Repeat Step 100, then:

```bash
git add src/trafficlab/common/config.py src/trafficlab/generation/models tests/support/config.py tests/unit/common/test_config_validation.py \
  tests/unit/generation/models tests/scientific/generation \
  tests/integration/generation architecture/traffic_models architecture/TESTING.md
git commit -m "feat(models): add categorical packet HMM"
```

### Task 12 [TASK-12-007a0c58]: Publish schema-5 defaults, fixtures, schemas, and candidate-doc cleanup

**Files:**
- Modify: `examples/configs/default.toml`
- Modify: `examples/configs/minimal.toml`
- Modify: `examples/configs/README.md`
- Modify: `scripts/generate_{artifact_schemas,fit_fixtures,model_fixtures,similarity_fixtures}.py`
- Modify: generated `examples/schemas/scientific-artifact-v5/` for all seven fitted payloads and final post-fit fields
- Modify: `examples/data/` generated schema-5 fixtures/manifests
- Modify: `tests/fixtures/` nonhistorical schema-5 fixtures/manifests
- Modify: `tests/support/config.py`
- Modify: `tests/unit/tooling/test_artifact_schema_generator.py`
- Modify: `tests/unit/tooling/test_test_config_support.py`
- Modify: `architecture/CANDIDATES.md`
- Modify: `architecture/README.md`

**Interfaces:**
- Produces: release defaults enabling seven families/eight weights; deterministic schema-5 fixtures and JSON Schemas.
- Consumes: all implemented algorithms and generator scripts; preserves immutable historical evidence directories.

- [ ] **[STEP-102-b357605f] Write failing default/schema inventory tests**

Assert exact seven enabled family tables, weight `0.125` for all eight methods, complete post-fit settings, schema-5 directory/root IDs, 13-or-updated exact schema inventory, and absence of implemented algorithms from `CANDIDATES.md`.

- [ ] **[STEP-103-c45050c9] Run inventory tests red**

```bash
uv run --locked pytest -q tests/unit/tooling/test_artifact_schema_generator.py \
  tests/unit/tooling/test_test_config_support.py tests/unit/common/test_config_io.py
```

- [ ] **[STEP-104-5a34c56a] Update defaults and deterministic generators**

Add all family bounds/settings, equal eight-method weights, bounded MMD/C2ST/state allocations, and generator support for every strict payload. Keep imported-reference target and capture provenance values deliberately non-runnable.

- [ ] **[STEP-105-2d33f5fc] Generate schema-5 assets twice and compare bytes**

```bash
uv run --locked python scripts/generate_artifact_schemas.py
uv run --locked python scripts/generate_model_fixtures.py
uv run --locked python scripts/generate_similarity_fixtures.py
uv run --locked python scripts/generate_fit_fixtures.py
git diff --check
uv run --locked python scripts/generate_artifact_schemas.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_similarity_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
```

The first run intentionally changes checked assets; every `--check` run must then exit zero without mutation.

- [ ] **[STEP-106-b43aa446] Remove implemented entries from the candidate catalog**

Delete the four model and seven similarity entries now owned by normative algorithm documents. Update the catalog status language without recording milestone completion or experiment results.

- [ ] **[STEP-107-c33d94d0] Run the schema/default Medium gate**

```bash
uv run --locked pytest -q tests/unit/tooling tests/unit/common tests/unit/pipeline/test_artifact_schemas.py
uv run --locked pytest -q tests/integration/generation tests/integration/comparison
uv run --locked ruff check scripts tests/support src/trafficlab
uv run --locked pyright scripts/generate_artifact_schemas.py scripts/generate_fit_fixtures.py \
  scripts/generate_model_fixtures.py scripts/generate_similarity_fixtures.py tests/support src/trafficlab
```

- [ ] **[STEP-108-53828941] Audit immutable historical evidence**

Run repository fixture-layout and validation-study schema tests read-only. Confirm no file beneath immutable historical evidence prefixes changed.

- [ ] **[STEP-109-2485b9d7] Review and fix schema-5 assets/documentation**

Independent review checks defaults, strict schemas, deterministic generation, historical immutability, candidate removal, and architecture coverage. Repeat Steps 105, 107, and 108.

- [ ] **[STEP-110-44423032] Commit schema-5 release assets**

```bash
git add examples scripts tests/fixtures tests/support tests/unit/tooling tests/unit/common \
  architecture/CANDIDATES.md architecture/README.md
git commit -m "feat(release): enable required algorithms"
```

### Task 13 [TASK-13-777d3cce]: Add reproducible small, medium, and big development profiles

**Files:**
- Create: `scripts/derive_required_candidates_reference.py`
- Create: `tests/unit/tooling/test_derive_required_candidates_reference.py`
- Create: `examples/required_candidates/README.md`
- Create: `examples/required_candidates/{small,medium,big}.toml`
- Modify: `scripts/README.md`
- Modify: `.gitignore` only if the derived-work directory is not already ignored

**Interfaces:**
- Produces: CLI deriving packet ranges through `editcap`/`reordercap`, validating with Trafficlab, and writing canonical provenance manifest; three strict imported-reference profiles.
- Consumes: existing Moutai capture and capture metadata without modifying either.

- [ ] **[STEP-111-334500a4] Write failing derivation and profile tests**

Use fake command runners to assert packet range `1-256`/`1-512`, command order, no overwrite, source/output SHA-256, packet count, `W`, tool versions, capture metadata identity, atomic manifest publication, and exact small/medium/big genetic settings.

- [ ] **[STEP-112-aa4c511e] Run tooling tests red**

```bash
uv run --locked pytest -q tests/unit/tooling/test_derive_required_candidates_reference.py
```

- [ ] **[STEP-113-dd4b6188] Implement bounded derivation and manifests**

The script accepts source PCAPNG, capture JSON, inclusive packet limit, output directory, and refuses existing outputs. It runs `editcap -r source temp 1-N`, `reordercap`, strict pair validation, then atomically publishes PCAPNG, unchanged canonical metadata, and JSON manifest.

- [ ] **[STEP-114-432ff42b] Add three exact experiment profiles and instructions**

Small uses `256/population 8/generation 1/seeds [17]`; medium uses `512/12/3/[17,29]`; big uses full capture `21/10/[17,29,43]`. Include guards, early stopping, standalone preflight/fit/generate/compare commands, expected purpose, and prohibition on best-model claims from development profiles.

- [ ] **[STEP-115-3428c4bd] Run the tooling Small tier and derive local inputs**

```bash
uv run --locked pytest -q tests/unit/tooling/test_derive_required_candidates_reference.py
uv run --locked ruff check scripts/derive_required_candidates_reference.py tests/unit/tooling/test_derive_required_candidates_reference.py
uv run --locked pyright scripts/derive_required_candidates_reference.py tests/unit/tooling/test_derive_required_candidates_reference.py
uv run --locked python scripts/derive_required_candidates_reference.py \
  --source dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng \
  --capture-json dumps/moutai-stock-price-response-success/capture.json \
  --packet-limit 256 --output .work/required-candidates/small
uv run --locked python scripts/derive_required_candidates_reference.py \
  --source dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng \
  --capture-json dumps/moutai-stock-price-response-success/capture.json \
  --packet-limit 512 --output .work/required-candidates/medium
```

- [ ] **[STEP-116-ad219507] Review, fix, and commit experiment tooling**

```bash
git add scripts/derive_required_candidates_reference.py scripts/README.md \
  tests/unit/tooling/test_derive_required_candidates_reference.py examples/required_candidates .gitignore
git commit -m "feat(experiments): add tiered development runs"
```

### Task 14 [TASK-14-e919a96d]: Run the integrated Medium gate and limited development experiments

**Files:**
- Create or update ignored results: `runs/required-candidates-small/`, `runs/required-candidates-medium/`
- Create: `docs/evidence/2026-09-02-required-candidates-medium.md`
- Modify only when failures prove ownership: source/tests/docs from Tasks 1–13

**Interfaces:**
- Produces: reproducible small/medium run evidence and one human-readable technical summary.
- Consumes: complete schema-5 algorithms, derived local captures, strict profiles.

- [x] **[STEP-117-da26a591] Run the complete Medium test tier**

```bash
uv sync --locked --all-groups --all-extras
uv run --locked ruff format --check .
uv run --locked ruff check src scripts tests
uv run --locked pyright src scripts tests
uv run --locked pytest -q -m 'not docker and not internet' \
  tests/unit tests/scientific tests/integration/generation tests/integration/comparison tests/trafficlab_dashboard
```

Fix failures at their smallest owning test tier, then repeat this step once.

- [x] **[STEP-118-4a99b800] Run and retain the Small experiment**

Initialize `runs/required-candidates-small` from `examples/required_candidates/small.toml`, copy the derived pair, then run standalone `fit`, `generate`, and `compare` through `scripts/run_bounded.sh`. Require all seven families to receive candidates and all eleven final evaluations to publish.

- [x] **[STEP-119-3d9e8896] Run and retain the Medium experiment**

Repeat with the 512-packet pair and medium profile. Record command lines, identities, elapsed time, peak RSS, family champions, aggregate/components, post-fit diagnostics, and any invalid candidate counts. Do not deepen the search after results are visible.

- [x] **[STEP-120-6f6f63f1] Verify medium artifacts and write the evidence summary**

Use strict final artifact validation, reproduce generation/comparison from saved model/seeds, and explain that these are development checks rather than held-out scientific conclusions.

- [x] **[STEP-121-18685367] Review, fix, commit, and push the integrated medium milestone**

After independent review and one repeated Medium gate:

```bash
git add docs/evidence/2026-09-02-required-candidates-medium.md
git commit -m "test(integration): validate required algorithms"
git push origin feature/required-models-similarity-v5
```

### Task 15 [TASK-15-9e1e99fb]: Run the Big completion gate and full-capture experiment

**Files:**
- Create or update ignored results: `runs/required-candidates-big/`
- Create: `docs/evidence/2026-09-02-required-candidates-big.md`
- Modify: any final source/test/docs only when Big failures identify a concrete owner

**Interfaces:**
- Produces: final bounded real-program evidence, clean reviewed branch, pushed head ready for integration.
- Consumes: every prior reviewed task and medium evidence.

- [x] **[STEP-122-816102dd] Run the full Big test and release gate once**

```bash
uv sync --locked --all-groups --all-extras
uv run --all-extras ruff format --check .
uv run --all-extras ruff check .
uv run --all-extras pyright
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" --durations=50
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" \
  --cov=trafficlab --cov=trafficlab_dashboard --cov-branch --cov-report=term-missing \
  --cov-fail-under=90 --durations=50
uv run --all-extras python scripts/generate_similarity_fixtures.py --check
uv run --all-extras python scripts/generate_model_fixtures.py --check
uv run --all-extras python scripts/generate_fit_fixtures.py --check
uv run --all-extras python scripts/generate_validation_study_fixture.py --check
uv run --all-extras python scripts/generate_artifact_schemas.py --check
uv run --all-extras python scripts/measure_scientific_stack_reduction.py --check
uv run --all-extras python scripts/benchmark_scientific_stack.py --check
uv run --all-extras python scripts/benchmark_scapy_production.py --check
uv run --all-extras python scripts/check_scientific_stack_example.py --check
uv run --all-extras python scripts/run_scientific_stack_probes.py --probe all --check
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --all-extras pytest -vv -n 0 -m "docker or internet" \
  --internet-url https://upload.wikimedia.org/wikipedia/commons/5/5b/SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg
```

Run Big only after every earlier checkbox is complete. Fix any failure at its smallest owning tier before repeating the affected gate.

- [x] **[STEP-123-f666324b] Run the bounded full-capture Big experiment**

Use the unmodified 3,649-packet Moutai pair with `examples/required_candidates/big.toml`. Run standalone stages through a bounded outer scope, retain all canonical artifacts and resource measurements, and do not increase ten generations or three seeds after inspecting output.

- [ ] **[STEP-124-6802a7e8] Audit evidence and obtain final independent review**

Reproduce the saved final generation and comparison, verify schema-5 arithmetic/identities, document family/component outcomes without causal claims, and request final specification plus code-quality review. Fix every Critical/Important finding and rerun only its owning Small/Medium gate plus the final affected Big check.

- [ ] **[STEP-125-1f05977e] Commit, push, and report the clean feature head**

```bash
git add docs/evidence/2026-09-02-required-candidates-big.md
git commit -m "test(evidence): record required algorithm run"
git status --short
git push origin feature/required-models-similarity-v5
```

Require an empty `git status --short`, record the pushed commit, list all verification commands and experiment directories, and hand off the branch for merge/rebase without moving `MVP_3` again.
