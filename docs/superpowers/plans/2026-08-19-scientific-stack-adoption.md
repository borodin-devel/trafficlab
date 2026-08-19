# [PLAN-1-f79dbd45] Scientific Stack Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt NumPy, SciPy, expanded Pydantic schemas, and deterministic Hypothesis testing while proving or rejecting the MMPP-likelihood, pymoo, and Scapy candidates and simplifying Docker cleanup.

**Architecture:** Migrate in layers around one owned read-only `TrafficTrace`, retain Trafficlab's scientific formulas as thin typed policies over NumPy/SciPy, and validate persisted JSON through strict Pydantic roots before canonical rendering and cross-artifact checks. Optional libraries enter only through development-only probes with explicit adoption gates; production remains one Python process using Docker Compose CLI.

**Tech Stack:** CPython 3.12.3, uv, Pydantic 2, NumPy, SciPy, Hypothesis, pytest, Ruff, Pyright, Docker Compose CLI; development probes use pymoo 0.6.2 and Scapy 2.7.0.

**Spec:** `docs/superpowers/specs/2026-08-19-scientific-stack-adoption-design.md`

## [SECTION-1-c390d4e8] Global Constraints

- `architecture/` is authoritative for scientific definitions; preserve nearest-rank IAT diagnostics, Type 7 Markov-renewal bin boundaries, NIST ACF, exact multiscale snapping/L1, and arrival-epoch MMPP semantics.
- Production stays one Python process with two capture containers, Docker Compose CLI, classical models only, no security subsystem, no Node.js application dependency, and no second orchestration path.
- Runtime gains only NumPy and SciPy; Hypothesis, pymoo 0.6.2, and Scapy 2.7.0 stay in the development group unless a probe's reviewed adoption gate passes.
- New production randomness uses explicit `numpy.random.Generator(numpy.random.PCG64(seed))`; module-global NumPy RNG and `default_rng` are forbidden.
- Hypothesis acceptance uses `derandomize=True`, `database=None`, `deadline=None`, and `max_examples=100`; discovered defects become literal regressions.
- Public persisted models are strict, frozen, forbid extras, and reject nonfinite values. Duplicate-key detection, canonical bytes, hashes, atomicity, arithmetic, and lineage remain Trafficlab policy.
- Every changed behavior follows RED, GREEN, refactor. Broad commands use the bounded commands in `architecture/DEVELOPMENT.md`.
- Non-Docker package coverage remains at least 90% branch-aware. A failed unit test exposing a function defect requires 100% executable-line and branch coverage of that function.
- Deterministic fixtures and examples are checked in; ordinary tests stay offline. Docker resources use unique project names and bounded cleanup.
- Each task is independently reviewed; all Critical and Important findings are fixed before the next task.

## [SECTION-2-88433e42] File Responsibility Map

| Area | Owning files |
| --- | --- |
| Dependency lock and test profile | `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `tests/property/` |
| Columnar trace and adapters | `src/trafficlab/trace.py`, `src/trafficlab/pcapng.py` |
| Vectorized model features | `src/trafficlab/models/common.py`, `poisson.py`, `markov_renewal.py`, `mmpp.py` |
| Statistical kernels and reports | `src/trafficlab/similarity/`, `src/trafficlab/statistics.py`, validation-study scripts |
| Artifact schemas | owning artifact modules plus `src/trafficlab/artifact_schemas.py` |
| RNG/checkpoint compatibility | `src/trafficlab/genetic/`, `src/trafficlab/scientific_schema.py` |
| MMPP/pymoo/Scapy probes | `tests/scientific/probes/`, `scripts/run_scientific_stack_probes.py` |
| Docker cleanup | `src/trafficlab/cleanup.py`, `capture.py`, Docker test support |
| Benchmarks and retained evidence | `scripts/benchmark_scientific_stack.py`, `examples/scientific_stack/`, `docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md` |

---

### Task 1: [TASK-1-15e070e4] Lock dependencies and establish deterministic property safety nets

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/conftest.py`
- Create: `tests/property/__init__.py`
- Create: `tests/property/strategies.py`
- Create: `tests/property/test_trace_properties.py`
- Create: `tests/property/test_parser_and_schema_properties.py`
- Create: `tests/property/test_genetic_properties.py`

**Interfaces:**
- Produces: pytest profile `trafficlab_locked`; reusable `trace_events()`, `pcapng_cases()`, and `json_documents()` Hypothesis strategies.
- Preserves: every existing hand-calculated and scientific-validation test.

- [x] **[STEP-1-3d95c8d5] Step 1: Write dependency/profile tests that fail before installation**

```python
def test_locked_hypothesis_profile() -> None:
    from hypothesis import settings

    profile = settings.get_profile("trafficlab_locked")
    assert profile.derandomize is True
    assert profile.database is None
    assert profile.deadline is None
    assert profile.max_examples == 100
```

Also add import/version-placement assertions proving NumPy and SciPy are runtime dependencies while Hypothesis, pymoo, and Scapy are development-only.

- [x] **[STEP-2-84858edd] Step 2: Run the new tests and observe the expected import/profile failure**

Run the focused bounded command for `tests/property/test_trace_properties.py` and `tests/unit/test_package.py`; expect failure because Hypothesis and the profile do not exist.

- [x] **[STEP-3-89aa37a9] Step 3: Add dependencies with uv and register the locked profile**

```bash
uv add 'numpy>=2,<3' 'scipy>=1.16,<2'
uv add --dev 'hypothesis>=6,<7' 'pymoo==0.6.2' 'scapy==2.7.0'
```

Register and load `trafficlab_locked` in `tests/conftest.py`; reject a mutable examples directory by setting `database=None`.

- [x] **[STEP-4-55b7a059] Step 4: Add real property tests around existing behavior**

Generate finite nondecreasing traces, malformed/valid PCAPNG blocks, strict JSON documents, gene coordinates, and checkpoint render/parse inputs. Assertions use literal invariants and real production functions: round-trip identity, nondecreasing timestamps, deterministic rejection, and coordinate error bounds.

- [x] **[STEP-5-a7013717] Step 5: Prove deterministic repeated outcomes and runtime budget**

Run the property selection twice from separate empty temporary directories with the locked profile; record identical collection and pass counts. Run the Fast gate three times before and after the property selection is included and retain median wall times showing no more than 20% increase.

- [x] **[STEP-6-3e7315ca] Step 6: Verify and commit the safety net**

Run Ruff format/check, strict Pyright, the focused property selection, existing parser/schema/trace/genetic unit suites, and the bounded Fast gate. Commit `test: add deterministic property safety net`.

### Task 2: [TASK-2-682c67c6] Introduce the owned read-only NumPy trace

**Files:**
- Modify: `src/trafficlab/trace.py`
- Modify: `src/trafficlab/pcapng.py`
- Modify: `src/trafficlab/capture_validation.py`
- Modify: `tests/unit/test_trace.py`
- Modify: `tests/unit/test_pcapng.py`
- Modify: `tests/integration/test_model_pipeline.py`
- Create: `tests/property/test_trace_array_properties.py`
- Modify: `architecture/SYSTEM.md`

**Interfaces:**
- Produces: `TrafficTrace(timestamps: NDArray[np.float64], directions: NDArray[np.uint8], frame_lengths: NDArray[np.uint32])`, `TrafficTrace.from_events()`, `TrafficTrace.to_events()`, `TrafficTrace.iats()`, `parse_pcapng_trace()`.
- Changes: `normalize_reference()` and `align_generated()` return `TrafficTrace`; boundary APIs may retain `TraceEvent` conversion during migration.

- [x] **[STEP-7-3baf3a78] Step 1: Write failing ownership, dtype, validation, and conversion tests**

```python
trace = TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 64),))
assert trace.timestamps.dtype == np.dtype(np.float64)
assert trace.directions.dtype == np.dtype(np.uint8)
assert trace.frame_lengths.dtype == np.dtype(np.uint32)
assert not trace.timestamps.flags.writeable
assert trace.to_events() == (TraceEvent(0.0, Direction.OUTBOUND, 64),)
```

Mutate source arrays after construction, attempt writes, and cover unequal lengths, rank, wrong dtype, NaN/Inf, decreasing time, direction `2`, zero length, and `uint32` overflow.

- [x] **[STEP-8-3448d598] Step 2: Run trace tests and confirm `TrafficTrace` is absent**

Run the focused bounded selection for `tests/unit/test_trace.py` and `tests/property/test_trace_array_properties.py`; expect import failure for `TrafficTrace`.

- [x] **[STEP-9-64c9d82a] Step 3: Implement the minimal typed columnar value**

Copy inputs into owned C-contiguous arrays, validate before narrowing integers, set `write=False`, and implement length, slicing, equality, event conversion, direction masks, and `np.diff` IATs without returning writable aliases.

- [x] **[STEP-10-9dd2d45c] Step 4: Migrate PCAPNG and normalization boundaries**

Add `parse_pcapng_trace()` as the scientific-core entry point. Vectorize normalization and closed-window alignment. Keep `parse_pcapng_bytes()` as a thin event-boundary adapter until all external callers are migrated.

- [x] **[STEP-11-ffccc7d9] Step 5: Add in-process round-trip evidence and architecture text**

Prove PCAPNG → `TrafficTrace` → events → PCAPNG agreement for both directions and timestamp resolution, then update `architecture/SYSTEM.md` to define the three read-only columns and boundary adapters.

- [x] **[STEP-12-f518a9fc] Step 6: Verify and commit the trace layer**

Run Ruff, Pyright, trace/PCAPNG/capture-validation unit suites, property tests, and `tests/integration/test_model_pipeline.py`. Commit `feat: add columnar traffic trace`.

### Task 3: [TASK-3-ee55f8eb] Vectorize model features and preserve model mathematics

**Files:**
- Modify: `src/trafficlab/models/common.py`
- Modify: `src/trafficlab/models/poisson.py`
- Modify: `src/trafficlab/models/markov_renewal.py`
- Modify: `src/trafficlab/models/mmpp.py`
- Modify: `src/trafficlab/models/registry.py`
- Modify: `tests/unit/models/test_poisson.py`
- Modify: `tests/unit/models/test_markov_renewal.py`
- Modify: `tests/unit/models/test_mmpp.py`
- Modify: `tests/scientific/test_model_validation.py`
- Create: `tests/property/test_model_vectorization_properties.py`

**Interfaces:**
- Consumes: `TrafficTrace` columns and `PCG64`-compatible array values.
- Produces: vectorized Type 7 boundaries, state encodings, transition/mark counts, and unchanged fitted-model semantics.

- [x] **[STEP-13-4e73834a] Step 1: Add failing scalar-versus-vector oracle properties**

For literal traces and generated finite traces, compare `np.quantile(method="linear")` boundaries to the independent Type 7 oracle, state encodings to scalar bin assignment, transition matrices to scalar pair counts, and mark counts to a `Counter` oracle. Expect failures while scalar-only helpers remain.

- [x] **[STEP-14-41f7fa82] Step 2: Run focused model properties and record RED output**

Run the bounded focused command for the new property file plus the three model unit modules; confirm the missing vector interfaces or wrong return types cause the failures.

- [x] **[STEP-15-28681796] Step 3: Implement NumPy quantiles, encoding, and counts**

Use `np.quantile(..., method="linear")`, `searchsorted` with the documented boundary side, and `bincount` on flattened `(source * K + destination)` indexes. Preserve empty-row smoothing, observed holding-time samples, mark diagnostics, finite checks, and exact integer totals.

- [x] **[STEP-16-03c229ae] Step 4: Migrate all model fit paths to `TrafficTrace`**

Poisson derives `(n - 1) / W` from arrays; Markov renewal vectorizes states and transition samples; MMPP consumes columnar IATs without changing arrival-epoch generation. Delete redundant per-model trace validators after shared validation covers the same behavior.

- [x] **[STEP-17-016119e6] Step 5: Prove scientific tolerances and direct diagnostics**

Run existing hand examples and scientific recovery tests unchanged, plus exact direction/length/count assertions. Any changed floating result must remain within `1e-12` and be explained by an independently checked vector kernel.

- [x] **[STEP-18-565142a4] Step 6: Verify and commit vectorized model fitting**

Run Ruff, Pyright, all model unit tests, the new properties, `tests/scientific/test_model_validation.py`, and model-pipeline integration. Commit `perf: vectorize traffic model features`.

### Task 4: [TASK-4-b05d71dd] Adopt SciPy KS and reproducible bootstrap reporting

**Files:**
- Modify: `src/trafficlab/similarity/ks.py`
- Modify: `src/trafficlab/similarity/autocorrelation.py`
- Modify: `src/trafficlab/similarity/multiscale.py`
- Create: `src/trafficlab/statistics.py`
- Modify: `src/trafficlab/comparison.py`
- Modify: `scripts/run_validation_study.py`
- Modify: `tests/unit/similarity/test_ks.py`
- Modify: `tests/unit/similarity/test_autocorrelation.py`
- Modify: `tests/unit/similarity/test_multiscale.py`
- Create: `tests/unit/test_statistics.py`
- Create: `tests/property/test_similarity_vectorization_properties.py`

**Interfaces:**
- Produces: `BootstrapInterval` and `bootstrap_interval(values, *, seed, n_resamples=10_000, confidence_level=0.95)`.
- Preserves: similarity score/diagnostic formulas and nearest-rank IAT diagnostics; no KS p-value enters artifacts.

- [x] **[STEP-19-5a07f83f] Step 1: Write failing KS, ACF, multiscale, and bootstrap tests**

Compare SciPy KS statistics with the independent merged-ECDF scan on tied integer/float samples. Compare vectorized ACF and bin cells with scalar oracles. Assert literal bootstrap metadata and fixed-seed interval bytes for a predeclared sample.

- [x] **[STEP-20-15248770] Step 2: Run the new statistical selection and verify RED**

Run the focused bounded selection for KS, ACF, multiscale, statistics, and property modules; expect the bootstrap API to be missing and vectorization expectations to fail.

- [x] **[STEP-21-4baa3391] Step 3: Replace generic kernels without changing Trafficlab policy**

Use `ks_2samp(...).statistic`, centered NumPy dot products for selected lags, and vectorized per-direction `bincount` after existing four-ULP bin-index snapping. Retain the independent ECDF oracle in tests only and retain exact normalized-L1 accumulation in production.

- [x] **[STEP-22-44353315] Step 4: Implement seeded percentile bootstrap records**

Call `scipy.stats.bootstrap` with `Generator(PCG64(seed))`, 95% confidence, 10,000 resamples, and `method="percentile"`. Reject empty/nonfinite samples and nonfinite or inverted intervals. Persist seed, generator, method, resamples, sample size, statistic, and bounds.

- [x] **[STEP-23-44cb329a] Step 5: Integrate intervals into validation-study report inputs**

Add intervals for declared training/held-out scalar summaries without presenting p-values or ground truth. Extend report fixtures and in-process study tests to recompute rather than trust stored interval fields.

- [x] **[STEP-24-99b9cca1] Step 6: Verify and commit statistical integration**

Run Ruff, Pyright, all similarity/statistics unit and property tests, comparison integration, and validation-study pipeline tests. Commit `feat: adopt scipy statistical kernels`.

### Task 5: [TASK-5-7004bb5e] Consolidate similarity, failure, and best-model schemas with Pydantic

**Files:**
- Modify: `src/trafficlab/comparison.py`
- Modify: `src/trafficlab/errors.py`
- Modify: `src/trafficlab/models/registry.py`
- Create: `src/trafficlab/artifact_schemas.py`
- Modify: `tests/unit/test_similarity_artifact.py`
- Modify: `tests/unit/test_failure_outcome_contract.py`
- Modify: `tests/unit/models/test_registry.py`
- Create: `tests/unit/test_artifact_schemas.py`

**Interfaces:**
- Produces: strict frozen Pydantic roots `ComparisonResult`, `FailureOutcomeRecord`, `BestModel`, family payload union, method diagnostic union, and `PUBLIC_ARTIFACT_MODELS`.
- Preserves: duplicate-key hooks, canonical render bytes, identity recomputation, atomic publication, and existing error categories.

- [ ] **[STEP-25-528d71a3] Step 1: Write failing strict-schema and JSON-Schema tests**

Mutate valid fixtures with booleans-as-integers, NaN/Inf, unknown/missing keys, wrong discriminators, inconsistent aggregate arithmetic, and duplicate keys. Assert each public root appears in `PUBLIC_ARTIFACT_MODELS` and emits a Draft 2020-12-compatible schema.

- [ ] **[STEP-26-c17625a0] Step 2: Run artifact tests and confirm missing registry/strict failures**

Run the focused bounded selection for similarity, failure outcome, best-model, and artifact schema tests; record failures caused by the missing registry and manual validators accepting paths Pydantic must own.

- [ ] **[STEP-27-01ac7967] Step 3: Convert owning records to strict frozen models**

Use `ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)`, constrained numeric fields, and discriminated unions. Model validators enforce local score/weight/row consistency; helper functions translate `ValidationError` into stable `TrafficlabError` messages.

- [ ] **[STEP-28-d8705169] Step 4: Keep canonical and cross-artifact policy outside Pydantic**

Parse bytes with duplicate-key rejection before `model_validate`, render explicit canonical key order from `model_dump(mode="json")`, and recompute aggregate scores, content identities, observation windows, and bounds at the owning cross-artifact boundary.

- [ ] **[STEP-29-40063905] Step 5: Prove valid round trips and deterministic corrupt rejection**

Run every checked best-model, similarity, and failure fixture through parse/render/parse equality; retain exact rejection categories for the corruption matrix and integration publication/reuse behavior.

- [ ] **[STEP-30-a40f80d8] Step 6: Verify and commit core artifact schemas**

Run Ruff, Pyright, focused artifacts, model registry, comparison pipeline, failure public matrix, and coverage for changed schema functions. Commit `refactor: validate core artifacts with pydantic`.

### Task 6: [TASK-6-f01fa03d] Convert checkpoint state and RNG records to strict Pydantic schemas

**Files:**
- Modify: `src/trafficlab/genetic/types.py`
- Modify: `src/trafficlab/genetic/checkpoint.py`
- Modify: `src/trafficlab/artifact_schemas.py`
- Modify: `tests/unit/genetic/test_types.py`
- Modify: `tests/unit/genetic/test_checkpoint.py`
- Modify: `tests/integration/test_genetic_fitting.py`
- Modify: `tests/property/test_genetic_properties.py`

**Interfaces:**
- Produces: strict checkpoint root with discriminated candidate status/failure records, population/trial/history records, and named RNG state.
- Preserves: canonical checkpoint bytes, exact resume semantics, arithmetic/lineage checks, atomic publication, and incompatibility errors.

- [ ] **[STEP-31-c7042437] Step 1: Add failing checkpoint schema and corruption properties**

Cover every candidate status, invalid family payload, nonfinite score, malformed ID, inconsistent history/evaluation count, duplicate key, altered input identity, and RNG discriminator/state mutation. Assert generated schema contains all variants.

- [ ] **[STEP-32-ca233ecb] Step 2: Run checkpoint RED selection**

Run the bounded focused checkpoint/types/property tests; confirm failures identify absent Pydantic roots or manual structural paths targeted for removal.

- [ ] **[STEP-33-f7b34f27] Step 3: Model checkpoint structures and canonical decode**

Replace manual type/key parsing with strict frozen models and discriminators. Keep generation/population arithmetic, family registry checks, configuration identities, and resume compatibility as explicit post-parse validators.

- [ ] **[STEP-34-5c1ac82f] Step 4: Preserve exact rendering and resume equivalence**

Render from validated records in canonical key order, parse back before publish, and prove uninterrupted versus checkpoint-resumed histories/candidates/artifacts are identical for locked seeds.

- [ ] **[STEP-35-3afee253] Step 5: Measure manual validation reduction**

Add the checkpoint functions to the source-measurement inventory and require at least 30% fewer manual parse/type/key-validation executable lines without counting tests or generated schemas.

- [ ] **[STEP-36-b597c830] Step 6: Verify and commit checkpoint schemas**

Run Ruff, Pyright, checkpoint/types/property suites, genetic-fitting integration, deterministic fit fixture check, and changed-function coverage. Commit `refactor: model checkpoints with pydantic`.

### Task 7: [TASK-7-7103439a] Model validation-study and lineage artifacts with Pydantic

**Files:**
- Modify: `src/trafficlab/study_evidence.py`
- Modify: `scripts/run_validation_study.py`
- Modify: `scripts/audit_validation_study.py`
- Modify: `src/trafficlab/artifact_schemas.py`
- Modify: `tests/unit/test_study_evidence.py`
- Modify: `tests/unit/validation_study/test_protocol.py`
- Modify: `tests/unit/validation_study/test_audit.py`
- Modify: `tests/unit/validation_study/test_audit_boundaries.py`
- Modify: `tests/integration/test_validation_study_pipeline.py`

**Interfaces:**
- Produces: strict roots for environment, prerequisite, manifest, lineage, lifecycle, protocol, report-input, and report artifacts.
- Preserves: file modes/hashes, source binding, no-hardlink reconstruction, arithmetic recomputation, and atomic candidate publication.

- [ ] **[STEP-37-44d511e5] Step 1: Write failing strict study-root tests**

For each public root, start from a valid literal and mutate exact type, key set, schema version, hash, mode, relative path, count, phase transition, and linked identity. Assert duplicate keys fail before Pydantic and cross-record mismatches fail after local validation.

- [ ] **[STEP-38-8841a9fa] Step 2: Run protocol/audit RED selection**

Run the bounded focused protocol, audit-boundary, study-evidence, and schema registry tests; record missing roots and manual-parse paths.

- [ ] **[STEP-39-e3b6ca38] Step 3: Implement strict study models in the package**

Move reusable artifact shape decisions from scripts into typed frozen models in `study_evidence.py`; keep filesystem reads, subprocess collection, and publication orchestration in scripts.

- [ ] **[STEP-40-49ca7950] Step 4: Rewire collection and audit to validate then recompute**

Use the models for structure, then independently recompute manifests, hashes, counts, scores, bootstrap intervals, lifecycle transitions, and lineage. Do not accept persisted derived values merely because they satisfy field constraints.

- [ ] **[STEP-41-bf74a66d] Step 5: Prove standalone and in-process compatibility**

Run the standalone copied-script test, candidate fixture generator/check, full in-process validation-study pipeline, and corruption matrix with unchanged deterministic rejection classes.

- [ ] **[STEP-42-c0557b94] Step 6: Verify and commit study schemas**

Run Ruff, Pyright, all validation-study unit/integration selections, schema generation, and manual-validation reduction measurement. Commit `refactor: validate study artifacts with pydantic`.

### Task 8: [TASK-8-56cf382e] Migrate production RNGs and publish scientific artifact schema v3

**Files:**
- Modify: `src/trafficlab/scientific_schema.py`
- Modify: `src/trafficlab/models/common.py`
- Modify: `src/trafficlab/models/poisson.py`
- Modify: `src/trafficlab/models/markov_renewal.py`
- Modify: `src/trafficlab/models/mmpp.py`
- Modify: `src/trafficlab/genetic/population.py`
- Modify: `src/trafficlab/genetic/operators.py`
- Modify: `src/trafficlab/genetic/strategy.py`
- Modify: `src/trafficlab/genetic/checkpoint.py`
- Modify: deterministic fixture generators and affected fixtures
- Modify: traffic-model and genetic architecture documents

**Interfaces:**
- Produces: `Generator(PCG64(seed))` draw protocol and JSON-compatible named RNG state; scientific artifact schema version 3.
- Invalidates: schema-v2 best models/checkpoints with existing explicit refit instructions.

- [ ] **[STEP-43-eb9ed857] Step 1: Add failing named-generator, draw-order, and v2 rejection tests**

Assert no production constructor accepts module-global RNG, exact first draws for a literal seed and operation sequence, JSON state restore equality, schema-v2 artifact rejection, and schema-v3 render/parse/resume identity.

- [ ] **[STEP-44-2f6276eb] Step 2: Run focused RNG/schema tests and capture RED**

Run model generation, genetic population/operators/strategy/checkpoint, and scientific schema tests; confirm failures arise from Python `random.Random` state and schema version 2.

- [ ] **[STEP-45-959bb089] Step 3: Introduce one typed PCG64 construction/state boundary**

Centralize explicit `Generator(PCG64(seed))` construction and state encode/decode without wrapping every NumPy method. State records include bit-generator name and exact JSON-compatible state; mismatched names fail.

- [ ] **[STEP-46-e8b6efb5] Step 4: Migrate model and genetic draws in declared order**

Translate uniform, integer, choice, exponential, shuffle/permutation, mutation, and crossover draws with explicit shapes/endpoints. Keep the call order stable and document it in owning architecture files. Never use `default_rng`.

- [ ] **[STEP-47-fc4ae3f2] Step 5: Bump schema and regenerate deterministic artifacts**

Set `SCIENTIFIC_ARTIFACT_SCHEMA_VERSION = 3`, regenerate model/fit/similarity/validation fixtures through generators, update manifests, and prove two locked runs produce identical arrays and bytes.

- [ ] **[STEP-48-f0a687c7] Step 6: Verify and commit RNG/schema v3**

Run Ruff, Pyright, all model/genetic unit and scientific tests, integration pipelines, and every affected generator `--check`. Commit `feat: adopt pcg64 scientific schema v3`.

### Task 9: [TASK-9-87396d92] Implement and evaluate the SciPy MMPP likelihood probe

**Files:**
- Create: `tests/scientific/probes/__init__.py`
- Create: `tests/scientific/probes/mmpp_likelihood.py`
- Create: `tests/scientific/probes/test_mmpp_likelihood.py`
- Create: `examples/scientific_stack/mmpp_cases.json`
- Modify: `scripts/run_scientific_stack_probes.py`

**Interfaces:**
- Produces: `mmpp_log_likelihood(iats, terminal_silence, rates) -> float`, bounded transformed-rate optimization, and machine-readable pass/reject evidence.
- Does not modify: production `MmppFamily.fit` unless all gates pass in a later reviewed adoption commit.

- [ ] **[STEP-49-334f3889] Step 1: Write hand-likelihood, extreme-rate, and recovery tests first**

Use literal two-state matrix cases independently calculated at high precision, explicit arrival-epoch initialization, terminal silence, extreme positive finite rates, and fixed synthetic seeds with predeclared recovery tolerances.

- [ ] **[STEP-50-b609ac7e] Step 2: Run probe tests and verify missing likelihood failure**

Run the bounded focused probe module; expect import failure for `mmpp_log_likelihood`.

- [ ] **[STEP-51-00b3b165] Step 3: Implement scaled forward recursion and bounded optimization**

Build `D0 = Q - diag(lambda)` and `D1 = diag(lambda)`, multiply `expm(D0*u) @ D1`, normalize every positive finite forward row, accumulate log scales, and finish with `expm(D0*terminal_silence) @ ones`. Decode transformed rates with `lambda1 > lambda0` and finite configured bounds.

- [ ] **[STEP-52-3e0f5129] Step 4: Compare equal evaluation budgets**

For several fixed seeds, compare synthetic recovery and held-out likelihood or production similarity against simulation-distance fitting using the same objective-evaluation count. Record rates, starts, evaluations, termination, held-out inputs, and results.

- [ ] **[STEP-53-8dfa1ed0] Step 5: Emit deterministic probe fixtures and decision**

Write canonical `mmpp_cases.json` with hand, extreme, recovery, and equal-budget records. The decision is `pass` only if every predeclared gate passes; otherwise it is `reject` with exact failed gates and production remains unchanged.

- [ ] **[STEP-54-9ad4d1c4] Step 6: Verify and commit the MMPP probe**

Run Ruff, Pyright, focused probe tests twice, the existing MMPP scientific suite, and the probe runner `--check`. Commit `test: evaluate scipy mmpp likelihood`.

### Task 10: [TASK-10-a7eb53a0] Implement and evaluate the pymoo optimizer probe

**Files:**
- Create: `tests/scientific/probes/pymoo_optimizer.py`
- Create: `tests/scientific/probes/test_pymoo_optimizer.py`
- Create: `examples/scientific_stack/pymoo_cases.json`
- Modify: `scripts/run_scientific_stack_probes.py`

**Interfaces:**
- Produces: one independent public-interface pymoo adapter per family, explicit public-state snapshot, resume comparison, and pass/reject evidence.
- Preserves: Trafficlab evaluation cache, invalid classification, common seeds/window/limits/weights, minimum family budget, and champion comparison.

- [ ] **[STEP-55-bdd22e80] Step 1: Write known-optimum, fairness, cache, and replay tests**

Predeclare a bounded continuous sphere optimum, mixed integer/real optimum, three-family initial budgets, common trial seeds, cache keys, uninterrupted history, and checkpoint-resumed history. Assert no dill/pickle bytes occur.

- [ ] **[STEP-56-3feb49d7] Step 2: Run pymoo probe tests and verify RED**

Run the bounded focused pymoo probe module; expect missing adapter/state extraction failures.

- [ ] **[STEP-57-3bf92809] Step 3: Implement independent family adapters through public pymoo APIs**

Map exact Trafficlab gene bounds/types, execute sequential seeded algorithms, allocate equal initial evaluations, and retain Trafficlab objective/caching/diagnostics. Do not construct a categorical model-family variable.

- [ ] **[STEP-58-f472849e] Step 4: Implement transparent state extraction and replay**

Extract population genes/objectives, generation, evaluation count, termination, configuration, pymoo version, and RNG state into strict records. Resume and compare every trial-history field to the uninterrupted locked run.

- [ ] **[STEP-59-ac2fc38b] Step 5: Measure adoption gate and record decision**

Record known optima, deterministic repeats, fairness/cache results, replay equality, and estimated production genetic LOC reduction. Mark `pass` only if exact public-state replay works and a production replacement would remove at least 40% without losing diagnostics; otherwise retain the existing strategy.

- [ ] **[STEP-60-337f7bfd] Step 6: Verify and commit the pymoo probe**

Run Ruff, Pyright, focused probe tests twice, existing genetic integration tests, and probe runner `--check`. Commit `test: evaluate pymoo optimizer`.

### Task 11: [TASK-11-765e988f] Implement and evaluate the typed Scapy PCAPNG probe

**Files:**
- Create: `tests/scientific/probes/scapy_pcapng.py`
- Create: `tests/scientific/probes/test_scapy_pcapng.py`
- Create: `examples/scientific_stack/scapy_cases.json`
- Create: `examples/scientific_stack/SCAPY_LICENSE_DECISION.md`
- Modify: `scripts/run_scientific_stack_probes.py`

**Interfaces:**
- Produces: development-only typed `read_with_scapy()`/`write_with_scapy()` adapters returning `TrafficTrace`, differential and benchmark evidence, and explicit license/adoption decision.
- Does not modify: production PCAPNG imports or bytes unless every gate and a separate compatibility decision pass.

- [ ] **[STEP-61-cca0725e] Step 1: Write differential, malformed, deadline, and typing tests**

Cover Ethernet IPv4/IPv6/ARP, one interface, endian variants, timestamp resolution, padding/options, source-MAC directions, frame lengths, truncated/malformed blocks, and an injected per-packet clock deadline. Compare canonical traces, not Scapy packet reprs.

- [ ] **[STEP-62-f70033a3] Step 2: Run Scapy probe tests and verify missing adapter failure**

Run the bounded focused Scapy probe module and strict Pyright; expect missing adapter imports.

- [ ] **[STEP-63-20046ce1] Step 3: Implement the narrow development-only adapter**

Confine dynamic Scapy objects behind locally typed protocols and explicit conversions. Reject extra interfaces/link types and apply Trafficlab direction/window/deadline rules. Do not add broad `Any`, blanket ignores, or production imports.

- [ ] **[STEP-64-0be97894] Step 4: Run 100,000- and 1,000,000-frame comparisons**

In fresh subprocesses, compare production and Scapy adapters for trace identity, median wall time, and peak RSS across five post-warmup runs. Record raw measurements and the predeclared material-regression decision.

- [ ] **[STEP-65-ed5bf94b] Step 5: Record license and adoption outcome**

Document that the current change is development-only, copies no GPL code, and makes no production import. Mark production adoption blocked pending a separate compatibility decision even if technical gates pass; retain the current codec on any technical failure.

- [ ] **[STEP-66-ed1b51c3] Step 6: Verify and commit the Scapy probe**

Run Ruff, strict Pyright, focused probe tests twice, full production PCAPNG/capture-validation tests, and probe runner `--check`. Commit `test: evaluate scapy pcapng adapter`.

### Task 12: [TASK-12-a1245088] Simplify project-scoped Docker cleanup

**Files:**
- Modify: `src/trafficlab/cleanup.py`
- Modify: `src/trafficlab/capture.py`
- Modify: `src/trafficlab/docker_cli.py`
- Modify: `tests/unit/test_cleanup.py`
- Modify: `tests/integration/test_cleanup_boundary.py`
- Modify: `tests/support/docker.py`
- Modify: `tests/docker/test_capture_failures.py`
- Modify: `tests/docker/test_run_docker.py`
- Modify: `architecture/CAPTURE.md`

**Interfaces:**
- Produces: one bounded `compose down --volumes --remove-orphans` production path and dedicated-test label inventory/sweep.
- Preserves: exact project-name validation, process deadline/termination, primary-error arbitration, and no global cleanup.

- [ ] **[STEP-67-a7c84d0f] Step 1: Rewrite tests around the simpler observable contract**

Add failing unit tests for one exact down command, timeout/kill/reap, invalid scope, nonzero status, and primary-versus-secondary failure. Move real resource-removal assertions to Docker lifecycle tests using exact Compose project labels.

- [ ] **[STEP-68-9b4ce29e] Step 2: Run cleanup tests and confirm old state-machine expectations fail**

Run the focused bounded cleanup unit/integration selection; verify failures demonstrate the production inventory/state-machine behavior that the new contract removes.

- [ ] **[STEP-69-19a98eac] Step 3: Implement one bounded cleanup command**

Reduce `cleanup_project` to validated scope, single `start_down`, bounded wait, terminate/kill/reap, and a compact success/failure result. Remove production post-down inventory ordering while retaining actionable command output.

- [ ] **[STEP-70-40039936] Step 4: Move leak verification to real Docker ownership fixtures**

Have each Docker test register its unique project, assert no labeled containers/networks/volumes/orphans remain, and run one bounded session teardown sweep for registered test projects only.

- [ ] **[STEP-71-d7ce8726] Step 5: Update capture arbitration and stable architecture**

Ensure `capture.py` always invokes cleanup in `finally`, attaches cleanup failure secondarily when a stage already failed, and otherwise surfaces cleanup as primary. Update `architecture/CAPTURE.md` with the single-command production boundary and dedicated lifecycle assertions.

- [ ] **[STEP-72-1288f057] Step 6: Verify and commit Docker simplification**

Run Ruff, Pyright, cleanup/capture unit and in-process integration tests, then the bounded Docker selection on a capable host with post-run label inventory empty. Commit `refactor: simplify compose cleanup`.

### Task 13: [TASK-13-94963935] Generate schemas, benchmarks, examples, and final real-program evidence

**Files:**
- Create: `scripts/generate_artifact_schemas.py`
- Create: `scripts/benchmark_scientific_stack.py`
- Create: `scripts/measure_scientific_stack_reduction.py`
- Finalize: `scripts/run_scientific_stack_probes.py`
- Create: `examples/schemas/scientific-artifact-v3/`
- Create: `examples/scientific_stack/experiment.toml`
- Create: `examples/scientific_stack/benchmark.json`
- Create: `examples/scientific_stack/code_reduction.json`
- Create: `docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md`
- Regenerate: fixture manifests and accepted validation-study evidence
- Modify: `architecture/DEVELOPMENT.md`, `architecture/TESTING.md`, relevant model/similarity documents

**Interfaces:**
- Produces: deterministic `--check` generators, one-million-event benchmark evidence, exact code-reduction evidence, public JSON Schemas, distributable example config, and audited Docker/Internet real-program evidence.

- [ ] **[STEP-73-2fcc2956] Step 1: Write failing generator/benchmark/reduction contract tests**

Assert deterministic sorted schema filenames/content, benchmark seed `20260819`, exactly 1,000,000 events, five post-warmup subprocess samples, scalar/vector agreement within `1e-12`, and named before/after production-function inventories excluding tests/generated files.

- [ ] **[STEP-74-e5cdb5af] Step 2: Run evidence contract tests and verify RED**

Run the bounded focused generator and fixture tests; expect missing scripts and artifacts.

- [ ] **[STEP-75-b6b19af7] Step 3: Implement deterministic generators and acceptance calculations**

Generate every public root schema, benchmark normalization/IAT/multiscale/ACF in isolated subprocesses, compute median time/peak RSS, and require at least 25% numerical-loop and 30% artifact-validation reductions from explicit Git baseline/function inventories.

- [ ] **[STEP-76-af2c91ab] Step 4: Check in example configuration and machine-readable evidence**

Create a small locked full-workflow configuration using fixed PCG64 seeds and all three families. Run every probe and benchmark twice, retain canonical JSON plus commands/environment/lock identity, and write the human evidence document without overstating rejected probes.

- [ ] **[STEP-77-6f491ba3] Step 5: Produce and audit real-program validation evidence**

From the final implementation source commit, run all deterministic generator checks, bounded Docker/Internet prerequisites with an explicit credential-free HTTPS URL, the validation study, held-out/bootstrap reporting, and the offline audit in the required detached clone with regular copied evidence. Commit the manifest-bound accepted bundle and evidence docs.

- [ ] **[STEP-78-68462365] Step 6: Run release gates, final review, and commit completion evidence**

Run `uv sync --locked --all-groups`, Ruff format check, Ruff lint, strict Pyright, bounded Ordinary, bounded Coverage at or above 90%, every generator `--check`, offline accepted-bundle audit, and combined serial Docker/Internet gate. Obtain a final whole-branch review with no Critical or Important findings, mark every plan checkbox accurately, commit `docs: record scientific stack validation`, and verify the worktree is clean.

## [SECTION-3-8990fe91] Delivery

Do not push, merge, publish, or delete the worktree during execution. After Task 13 passes and the final review is clean, use `superpowers:finishing-a-development-branch` and present the repository's integration choices to the user.
