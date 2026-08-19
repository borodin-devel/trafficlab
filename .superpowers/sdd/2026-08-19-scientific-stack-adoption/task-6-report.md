# [TASK-6-f01fa03d] Strict Pydantic checkpoint and RNG schemas

## Status

Implemented. Checkpoint runtime records and the exact public checkpoint wire root are strict, frozen Pydantic models with `extra="forbid"`, `strict=True`, `allow_inf_nan=False`, and `revalidate_instances="always"`. The checkpoint registry entry is `PUBLIC_ARTIFACT_MODELS["checkpoint"]`.

Scientific artifact schema remains exactly 2. The `python.random.Random/MT19937` engine, Python `random.Random` state tuple, draw behavior, Gaussian-cache-null rule, canonical bytes, duplicate-key precedence, resume compatibility ordering, arithmetic, lineage, and atomic publication are unchanged. Task 8 still owns PCG64 and schema 3.

No Pydantic dataclass, positional-constructor shim, unvalidated production `model_copy(update=...)`, broad production `Any`, opaque payload serializer, or second checkpoint codec was added. All affected constructors are explicitly keyword-only; the single remaining positional construction is the test proving positional calls fail.

## Exact files

Production:

- `src/trafficlab/genetic/types.py`
- `src/trafficlab/genetic/checkpoint.py`
- `src/trafficlab/artifact_schemas.py`
- `src/trafficlab/genetic/coordinates.py`
- `src/trafficlab/genetic/evaluation.py`
- `src/trafficlab/genetic/operators.py`
- `src/trafficlab/genetic/population.py`
- `src/trafficlab/genetic/strategy.py`

Tests and explicit constructor/rebuild migrations:

- `tests/unit/genetic/test_types.py`
- `tests/unit/genetic/test_checkpoint.py`
- `tests/unit/genetic/test_evaluation.py`
- `tests/unit/genetic/test_operators.py`
- `tests/unit/genetic/test_population.py`
- `tests/unit/genetic/test_strategy.py`
- `tests/property/test_genetic_properties.py`
- `tests/integration/test_genetic_fitting.py`
- `tests/unit/test_artifact_schemas.py`
- `tests/unit/test_failure_outcome_public_matrix.py`
- `tests/unit/test_fitting.py`
- `tests/unit/test_run.py`
- `tests/unit/validation_study/test_protocol.py`
- `tests/support/validation_study.py`

## [STEP-31-c7042437] Schema and corruption tests

The public `CheckpointArtifact` schema exposes three candidate-status variants and six failure-kind variants through discriminated `oneOf` branches. It includes exact family coordinate variants, population/trial/history records, the named RNG engine and 624-word MT19937 state, identities, settings, best-candidate record, and terminal state.

Tests cover pending/valid/invalid status, all six failure kinds, invalid family and gene payloads, nonfinite scores, malformed two-integer IDs, population/history/evaluation arithmetic, duplicate candidate and trial IDs, altered experiment/reference/capture identities, RNG engine/version/word/index/cache mutations, unknown/missing fields, and canonical encoding. The existing duplicate-pairs hook still rejects duplicate JSON keys before Pydantic validation.

Every repository-owned checked checkpoint is independently validated with `Draft202012Validator` and then with the Pydantic root. Observed result:

```text
checkpoint schema + exact parse/render/parse: 19 documents
```

## [STEP-32-ca233ecb] Bounded RED

Baseline focused selection before the new tests: `221 passed in 1.47s`.

RED command:

```text
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 2m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/genetic/test_types.py tests/unit/genetic/test_checkpoint.py tests/property/test_genetic_properties.py
```

Observed RED: collection failed with `ImportError: cannot import name 'CheckpointArtifact' from trafficlab.genetic.checkpoint`, exit status 2. This was the intended missing-public-root failure before production changes.

## [STEP-33-f7b34f27] Strict models and canonical decode

`CandidateId`, method/trial records, candidate failure and duplicate records, `Candidate`, `HistoryRow`, family/settings/compatibility records, `RngState`, and `CheckpointState` are strict frozen BaseModels. The public wire root adds discriminated status/failure records without a dataclass shadow path.

`parse_checkpoint` now performs duplicate-free JSON decode, schema-version and experiment-identity precedence, strict `CheckpointArtifact.model_validate`, explicit wire-to-runtime conversion, then the existing compatibility and scientific post-parse checks. Nested Pydantic instances are dumped to primitives and reconstructed at schema boundaries; a deliberately poisoned nested instance is rejected on root revalidation.

Manual key/type/array parsers removed include `_exact_object`, `_array`, `_boolean`, `_frozen_json`, `_parse_coordinate`, `_parse_family`, `_parse_genetic`, `_parse_generation_limits`, `_parse_similarity`, `_parse_compatibility`, `_parse_rng`, `_parse_identifier`, `_parse_method`, `_parse_trial`, `_parse_failure`, `_parse_duplicate`, `_parse_candidate`, and `_parse_history_row`. Scientific registry, coordinate, arithmetic, lineage, termination, and resume-compatibility checks remain explicit policy.

## [STEP-34-5c1ac82f] Canonical bytes and resume identity

Rendering validates the runtime state, constructs the established canonical wire document, revalidates the complete public root, confirms validation did not change the JSON tree, and emits sorted compact finite JSON with one final newline. Parsing re-renders before acceptance. All 19 checked checkpoint files round-trip to their exact original bytes.

Focused resume/corruption command result:

```text
39 passed in 1.24s
```

This selection includes byte-identical interrupted/resumed integration, uninterrupted-versus-resumed population/history/winner/RNG identity, duplicate-key rejection, every parameterized RNG mutation, and operator-tamper-before-reproduction behavior.

The deterministic fit fixture gate reports:

```text
genetic-fitting and checkpoint-resume fixture: checked-in paths and bytes match deterministic output
```

No checkpoint, history, best-model, manifest, or deterministic fixture byte changed.

## [STEP-35-3afee253] Manual validation reduction

The AST measurement uses the Task 6 parent commit `734af74` and counts executable statement lines in `types.py` and `checkpoint.py` functions matching the established migrated-validation inventory: `_strict*`, `_bounded*`, `_exact*`, `_ranged*`, `_float*`, `_normalized*`, `_validate*`, `_parse*`, `_build*`, `from_dict`, `from_json`, and `__post_init__`. Tests, comments, blanks, schemas, and generated code are excluded.

```text
baseline types.py=71 checkpoint.py=447 total=518
after    types.py=8  checkpoint.py=303 total=311
reduction = 207 / 518 = 40.0%
```

This exceeds the required 30% reduction while retaining cross-record scientific validation.

## [STEP-36-b597c830] Verification and self-review

Focused checkpoint/types/property/schema result:

```text
230 passed in 4.05s
```

Final checkpoint/types/property/genetic-fitting integration result:

```text
232 passed in 3.23s
```

Full Fast gate:

```text
3419 passed in 33.93s
```

The first Fast attempt exposed 583 stale, already-prunable Git worktree registrations left by earlier validation-study tests. After bounded diagnosis, the stale metadata was pruned and the two last-failed validation-study cases were migrated from `dataclasses.replace` to fully validating `rebuild_genetic_record`; both passed before the fresh full Fast run.

Affected-function branch coverage ran 201 checkpoint/strategy tests and reported:

```text
parse_checkpoint missing lines: []
parse_checkpoint missing branches: []
```

Static and fixture verification:

```text
ruff format --check . -> 233 files already formatted
ruff check .          -> All checks passed!
pyright               -> 0 errors, 0 warnings, 0 informations
git diff --check       -> clean
generate_fit_fixtures.py --check -> checked-in paths and bytes match deterministic output
```

Self-review found no Critical or Important issue. Candidate/status/failure schema variants are reachable from the one registered root; Pydantic owns local structure while Trafficlab still recomputes trusted-looking scores, counts, history, identities, compatibility, and terminal state. Atomic replacement still validates persisted temporary bytes before rename. Test-only deliberate-corruption helpers use `model_construct`; production has no unvalidated update path.

Concerns: none. The stale prunable worktree metadata was environmental test residue and was removed; no live user worktree or source file was deleted.
