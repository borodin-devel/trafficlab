# Task 6 report [TASK-6-594bb343]

## RED/GREEN evidence [STEP-1-a3554905]

The first specified RED command failed during collection with
`ModuleNotFoundError: trafficlab.comparison.postfit.c2st`. After the independent
C2ST tests were written, its focused GREEN run passed `14` tests covering exact
window features, reference-only scaling, guarded fold indexes, analytic
logistic loss/gradient, deterministic coefficients, tie-aware AUC, balanced
labels, separable/chance cases, solver failure, invalid coefficients, and
window/fold caps.

The artifact/config RED run then failed because `evaluate_postfit`, required
nested post-fit settings, and `PostfitDiagnostics` did not exist. The stage RED
case failed with `AttributeError` for `comparison.stage.evaluate_postfit`.
Focused GREEN passed the exact Task 6 selection with `67` tests, then the full
comparison selection passed.

Self-review mutation tests initially accepted five invalid artifacts:
noncanonical fold order/range/guards, a corrupted Fano curve, and transition
states inconsistent with counts. All five tests failed before their validators
were added and passed afterward. Further RED cases exposed a one-window Fano
artifact leaking `ZeroDivisionError` and an unfrozen transition run vocabulary;
both now fail strictly at schema validation.

## Implementation and files [STEP-2-3a5c18bf]

Added `postfit/c2st.py` with the frozen 14-coordinate `window-v1`
representation, bounded window allocation, contiguous guarded folds,
reference-training-only population transforms, deterministic zero-initialized
SciPy L-BFGS-B logistic regression with analytic gradient, stable probability
evaluation, balanced accuracy, and local tie-aware AUC.

Added required nested `DispersionSettings`, `TransitionSettings`,
`C2stSettings`, and `PostfitSettings` to `SimilarityConfig` without defaults.
Updated current default/minimal/fit configs, the schema-5 fit checkpoint and
experiment identities, canonical similarity fixture, manifest, and generated
checkpoint/comparison JSON Schemas. Historical validation-study evidence was
not changed.

Added typed Fano/Allan, transition, C2ST, and `PostfitDiagnostics` publication
models with exact nested fields, shared-`W` validation, score arithmetic,
reference state/count/run reconstruction, Fano/Allan curve reconstruction,
canonical fold partitions, solver/convergence/shape checks, and final-only
publication enforcement. `evaluate_postfit()` calls exactly the three post-fit
functions; the comparison stage joins its result to `evaluate_fitness()` before
lineage and publication.

Created `architecture/similarity_methods/classical_c2st.md` and updated the
system, similarity catalog, and testing contracts. The documentation freezes
features, boundary semantics, reference scaling, guards, objective/gradient,
solver, AUC/ties, score mapping, resource preconditions, and GA separation.

## Artifact and GA isolation evidence [STEP-3-4d5b2829]

The final wire root now requires exact `postfit_diagnostics` keys
`classical_c2st`, `fano_allan`, and `transition_matrix`, each containing only
`diagnostics` and `score`. Mutation cases reject missing/extra keys, mismatched
windows, inconsistent score/AUC, false convergence, coefficient/fold defects,
curve/state/count/run arithmetic corruption, unsafe smoothing arithmetic, and
publication without post-fit data. Canonical codec, exclusive publication, and
retry tests all exercise the regenerated final artifact.

Genetic evaluation still imports and calls only `evaluate_fitness`. A targeted
test monkeypatches `evaluate_postfit` and all three post-fit functions to raise;
the candidate remains valid with two ordinary eight-method trials. A separate
checkpoint mutation injects `postfit_diagnostics` into a trial and is rejected
by the strict checkpoint codec.

## Verification commands and results [STEP-4-d33fbd8a]

Final repeated Medium gate:

```text
uv run --locked pytest -q tests/unit/comparison tests/integration/comparison
518 passed

uv run --locked ruff check src/trafficlab/comparison tests/unit/comparison tests/integration/comparison
All checks passed!

uv run --locked pyright src/trafficlab/comparison tests/unit/comparison
0 errors, 0 warnings, 0 informations

uv run --locked pytest -q --cov=trafficlab.comparison --cov-branch --cov-report=term-missing tests/unit/comparison tests/integration/comparison
518 passed; total branch-aware coverage 91.46%
```

Supplemental in-scope evidence:

```text
uv run --locked pytest -q tests/unit/common/test_config_validation.py -k postfit
15 passed

uv run --locked pytest -q tests/unit/fitting/genetic/test_evaluation.py -k never_calls_postfit
1 passed

uv run --locked pytest -q tests/unit/fitting/genetic/checkpoint/test_codec.py -k postfit_diagnostics
1 passed

uv run --locked python scripts/generate_artifact_schemas.py --check
verified 13 public roots

uv run --locked python scripts/generate_similarity_fixtures.py --check
checked-in bytes match deterministic production output

uv run --locked python scripts/check_fixture_layout.py --check-manifest
passed
```

The current fit checkpoint also passes the regenerated schema through
`jsonschema.validate`. Expanded config/checkpoint support passed focused Ruff
and strict Pyright. `git diff --check` produced no output.

## Coverage and resource checks [STEP-5-a4388d1c]

`trafficlab.comparison.postfit.c2st` has 91% branch-aware coverage and the full
comparison package has 91.46%. Tests directly cover the declared 65,536-window
configuration bound, runtime maximum window count, insufficient guarded folds,
transition state/cell caps inherited from the existing diagnostic, and
Fano/Allan direction-window cap. Allocation checks occur before feature/fold or
dense transition work.

## Self-review [STEP-6-3d31dca6]

Reviewed temporal leakage, generated-data influence, GA entry points, artifact
arithmetic, and allocation bounds. IATs are formed only within a block; every
guard index is absent from both train and evaluation; the typed artifact
reconstructs the exact canonical partition. Every fold mean/scale is computed
only from reference training rows and retained for inspection. No fitting source
references `evaluate_postfit`, and post-fit results cannot enter trial payloads.

Removed redundant transition length/leakage checks once stronger reconstruction
proved the same invariants. Added missing reconstruction for dispersion factors,
transition occupancy/rows/runs, and exact C2ST folds. No Critical or Important
self-review finding remains.

## Concerns [STEP-7-4a36ec30]

No open concern. The dense transition probabilities make the checked-in
`similarity.json` intentionally large, but the declared 256-state/65,536-cell
caps bound it and the current two-by-two bin configuration uses 40 states.

## Fix round 1 [STEP-8-125c044c]

RED was captured before production edits. The focused C2ST command

```text
uv run --locked pytest -q tests/unit/comparison/postfit/test_c2st.py \
  -k 'decimal_boundary or retained_index_evidence'
```

failed `2` tests: `0.3 / 0.1` was assigned to block two instead of block
three, and a `32,769 * 2` retained-fold index layout crossed 65,536 without an
error. The schema mutation/cap/smoothing command failed `13` tests: C2ST did
not reconstruct `W / width`, the configured cap, ordered arrays, or exact
`divmod` fold sizes; Fano/Allan did not reconstruct width/window/cap values;
transition artifacts did not freeze threshold order, Cartesian vocabulary,
two-event minima, or bin-derived caps; and huge finite smoothing could produce
zero probabilities instead of failing before division.

GREEN applies the shared four-ULP snap to both window and event quotients.
Feature extraction now groups sorted block indexes once and consumes contiguous
slices rather than constructing one packet mask per block. Guarded folds check
`window_count * fold_count <= 65,536` before any `range` call, then construct
ordered partitions directly from two outside ranges rather than a full set
difference. The regression monkeypatches the module's `range` name to prove the
cap fires before index materialization.

The publication schema now reconstructs snapped C2ST/Fano window counts, exact
ordered fold arrays and remainder sizes, the 65,536 C2ST window/fold-evidence
caps, Fano widths within `W` and direction-cell cap, and the exact transition
Cartesian vocabulary. Transition validation also requires nondecreasing
nonnegative thresholds, two states per trace, bin-derived 256-state/65,536-cell
caps, complete row shapes before arithmetic, and state-derived counts/runs.
Smoothing rejects nonfinite denominators or nonpositive/nonfinite probabilities
with `ValueError` before division-dependent comparisons or logarithms.

The original resource-evidence statement in `[STEP-5-a4388d1c]` was premature:
the configuration type already bounded the field, but there was no direct
accepted/rejected edge test and no separate retained-fold evidence cap. The
correct evidence is now an explicit configuration test accepting `65,536` and
rejecting `65,537`, plus a pre-allocation runtime/schema cap of 65,536 total
retained fold-index cells.

Focused final verification:

```text
uv run --locked pytest -q \
  tests/unit/comparison/postfit/test_c2st.py \
  tests/unit/comparison/test_schema.py \
  tests/unit/comparison/test_codec.py \
  tests/unit/comparison/test_publication.py \
  tests/unit/comparison/test_stage.py \
  tests/integration/comparison/test_comparison_pipeline.py
220 passed

uv run --locked pytest -q tests/unit/common/test_config_validation.py \
  -k 'postfit or maximum_window_count'
16 passed

uv run --locked ruff check <changed source and focused tests>
All checks passed!

uv run --locked pyright <changed source and focused tests>
0 errors, 0 warnings, 0 informations

uv run --locked pytest -q --cov=trafficlab.comparison --cov-branch \
  --cov-fail-under=0 --cov-report=term-missing <focused comparison tests>
220 passed; c2st.py 91%, schema.py 90% branch-aware coverage

uv run --locked python scripts/generate_artifact_schemas.py --check
verified 13 public roots

uv run --locked python scripts/generate_similarity_fixtures.py --check
checked-in bytes match deterministic production output

git diff --check
no output
```

No open concern remains from fix round 1. The focused coverage selection is
intentionally smaller than the whole comparison-package coverage gate; the two
changed modules independently remain at or above 90% branch-aware coverage.
