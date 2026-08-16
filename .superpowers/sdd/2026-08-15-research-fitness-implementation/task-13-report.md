# Task 13 - Offline validation-study auditor and complete Phase 7 fixture

## Scope

Implemented the minimal offline auditor, complete schema-2 accepted-candidate
fixture, fixture owner with `--check`, and audit-before-publication adapter.
It stays offline and in-process: no Docker, network, high-level run, retained
artifact mutation, or added infrastructure.

The authoritative adverse-condition fixture remains
`tests/fixtures/diagnostics/failure-outcomes.jsonl`; the stale plan spelling
`canonical_adverse_conditions.jsonl` was not introduced.

## Complete retained evidence

The deterministic credential-free fixture has 155 regular retained files:

- Three workloads times three distinct training repeats, each with the strict
  nine-file run inventory.
- Portable and realized configuration pairs for every training run.
- Nine same-reference `fresh_simulation` records.
- Three independent `held_out/<workload>/` capture/model-output/comparison
  bundles, evaluated with the frozen retained training model.
- Prerequisite command/stdout/stderr/status/JUnit evidence; transfer headers
  and observations; environment and compatibility evidence; protocol, report
  inputs, report, index, and manifest.

`index.json`, `manifest.json`, and retained JSONL logs use canonical UTF-8
bytes with duplicate-key rejection. The manifest records normalized
bundle-relative UTF-8-byte-sorted paths, owner, lineage, byte size, and
SHA-256 for every retained file except itself.

## Boundary mapping

| Boundary | Validation |
| --- | --- |
| Inventory/manifest | Deterministic first mismatch for missing, extra, symlink, temporary, nonregular, noncanonical, duplicate, identity, owner, and lineage evidence. |
| Environment/protocol | Schema, source commit/tree, lock digest, immutable image refs/IDs, host/runtime controls, compatibility, final seed, workload/repetition matrix, and selection seeds. |
| Training reconstruction | Public config, capture, checkpoint, best-model, PCAPNG, and comparison parsers; normalized `W`; checkpoint/model/generated/settings/comparison identities and lineage; four metrics plus aggregate. |
| Fresh simulation | Same-reference final-seed records are retained as `fresh_simulation`, not mislabelled held-out evidence. |
| Held-out evaluation | `evaluate_study_held_out()` requires a distinct reference, validates final controls/window, retains original training-model lineage, and regenerates/computes through public scientific owners. |
| Report | Recomputes natural variation, training selection fitness, fresh simulation, held-out results, and arithmetic from retained bytes. |
| Publication | `publish_audited_bundle()` audits before the existing exclusive publisher; invalid candidates are not published and occupied destinations remain byte-identical. |

## RED evidence

1. The former three-run candidate could not represent the Phase 7 workload,
   repetition, independent held-out, prerequisite, protocol, environment, and
   report requirements.
2. `test_validation_fixture_generator_rejects_nonhex_source_identities` RED:
   `Failed: DID NOT RAISE ValueError`. The new source-ID guard rejects nonhex
   and all-zero commit/tree values.
3. Canonical JSONL RED: the run log reported `is not canonical JSON` instead
   of the canonical JSONL diagnostic. `_canonical_jsonl()` now duplicate-safely
   parses each line and compares exact canonical line bytes.
4. A JSON-valid but schema-invalid `similarity.json` leaked `ValueError` from
   the public decoder. `_training()` now maps it to canonical
   `artifact_corrupt` at the publication boundary.
5. The current coverage pass exposed non-list index type exits introduced for
   static narrowing. The public matrix now covers malformed `training`,
   `fresh_simulation`, and `held_out` values.

## GREEN evidence

| Command | Result |
| --- | --- |
| `uv run --locked pytest -q tests/unit/test_validation_study.py -k 'study_held_out_evaluator_requires_an_independent_reference_and_uses_the_fixed_training_model'` | `1 passed, 257 deselected`; valid, same-reference, wrong-type, wrong-seed, and wrong-window evaluator paths. |
| `uv run --locked pytest -q tests/unit/test_validation_study.py -k 'offline_bundle_audit_covers_complete_index_schema_boundaries'` | `12 passed, 251 deselected`. |
| `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 4m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py` | `266 passed in 22.52s` after fixture provenance regeneration. |
| Same target with `--cov=scripts.audit_validation_study --cov=scripts.generate_validation_study_fixture --cov=scripts.run_validation_study --cov-branch --cov-fail-under=0 --cov-report=json:.coverage-task13-core-final-02.json` | No missing executable lines or branch exits in every core function listed below. |
| `uv run --locked python scripts/generate_validation_study_fixture.py --check` | Checked-in candidate bytes equal deterministic owner output. |
| `uv run --locked ruff check . && uv run --locked pyright` | `All checks passed!`; `0 errors, 0 warnings, 0 informations`. |
| `scripts/run_bounded.sh --memory-high 3G --memory-max 4G --swap-max 512M --wall-time 10m --kill-after 10s -- uv run --locked pytest -q -n 4 --dist worksteal -m 'not docker and not internet'` | `2924 passed in 15.84s`. |

## Literal core-function branch coverage

The current branch-aware JSON has no missing executable lines or branch exits:

- `scripts/audit_validation_study.py`: `files_for_candidate:211-251`,
  `write_manifest:303-326`, `_verify_inventory:329-358`,
  `owner_for_path:377-443`, `lineage_for_path:446-466`,
  `_metadata:469-494`, `_environment:502-597`, `_canonical_jsonl:729-742`,
  `_training:803-987`, `_fresh:990-1044`, `_held_out:1047-1147`,
  `_audit:1249-1447`, `audit_bundle:1450-1495`.
- `scripts/generate_validation_study_fixture.py`:
  `validate_source_identities:260-268`, `generate_fixture_tree:271-466`.
- `scripts/run_validation_study.py`: `evaluate_study_held_out:107-177`,
  `publish_audited_bundle:5412-5427`.

Module aggregates remain lower only on unrelated CLI/defensive error paths.

## Fixture provenance

The second commit regenerates the retained tree through
`scripts/generate_validation_study_fixture.py` with the immutable source
identity from the first implementation commit:

- Source commit: `a1a9466cc1afb915d69dde53ec8f870c1d452107`
- Source tree: `9b80a999711f74ac8113e7a6ac94868aea117f31`

The generator reported `wrote 155 deterministic retained files`, and its
subsequent `--check` reported byte-for-byte agreement.

## Files

- `scripts/audit_validation_study.py`
- `scripts/run_validation_study.py`
- `scripts/generate_validation_study_fixture.py`
- `tests/unit/test_validation_study.py`
- `tests/integration/test_validation_study_pipeline.py`
- `tests/fixtures/validation_study_candidate/`
- `examples/validation_study/README.md`
- `examples/validation_study/REPORT.md`
- `examples/validation_study/results.json`
- `README.md`
- `docs/superpowers/plans/2026-08-15-research-fitness-implementation.md`

## Self-review and concerns

Mappings stay at existing evidence/stage boundaries: no workflow engine,
database, network client, security subsystem, or alternate science parser was
added. The checked fixture is credential-free deterministic evidence, not a
substitute for Task 14 real-program collection/publication. Fixture provenance
is finalized in a second commit so its source IDs bind to the immutable first
implementation commit without self-reference.

## Task 13 review fix round 2

### Scope and mapping

| Review requirement | Final owner and evidence |
| --- | --- |
| Frozen prerequisites | `run_validation_study.py` owns one retained prerequisite codec for exact guarded argv, stdout, stderr, status, JUnit identities, and positive all-passed JUnit counts. The runner, fixture owner, and auditor all use it. |
| Relocated environment | `audit_validation_study.py` binds the recorded commit/tree and `uv.lock` to the actual relocated Git checkout, then binds image refs, image IDs, and tool version to `docker/capture/image-lock.json`. |
| Clean checkout | `test_validation_study_pipeline.py` uses a local `git clone --no-hardlinks --no-checkout`, checks out the cited source, blocks sockets and non-Git subprocesses, and proves imports and reads stay inside the clone. Unit audit setup uses detached Git worktrees so repeated verification does not duplicate repository objects. |
| Training selection | Protocol records freeze `highest_best_fitness_then_lowest_repeat`; the auditor reconstructs the selected model from completed training before validating held-out bindings. |
| Natural variation | The fixture owner and auditor independently calculate both comparison directions and the symmetric mean under a common normalized window and settings identity. |
| First mismatch | The public publisher keeps the complete first canonical primary outcome while malformed, missing, foreign, and substituted candidate evidence leaves candidate, destination, and temporary inventories unchanged. |

### RED and GREEN evidence

The initial review-focused command recorded `4 failed, 1 passed`: the public
prerequisite codec was absent, the auditor accepted source/image mismatch,
the fixture did not freeze selection or bidirectional variation, and the clean
clone accepted a source mismatch. The passing node was the simultaneous
first-mismatch inventory characterization. Those failures produced the source
commits listed below.

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 3m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 \
  tests/unit/test_validation_study.py::test_retained_prerequisite_codec_freezes_all_output_identities_and_aggregates_production_junit \
  tests/unit/test_validation_study.py::test_offline_auditor_binds_the_environment_to_the_relocated_git_and_image_locks \
  tests/unit/test_validation_study.py::test_complete_fixture_freezes_training_model_selection_and_bidirectional_variation \
  tests/integration/test_validation_study_pipeline.py::test_clean_checkout_auditor_rejects_a_candidate_bound_to_a_different_source_revision \
  tests/unit/test_validation_study.py::test_simultaneous_evidence_mismatches_preserve_the_first_complete_primary_and_all_inventories
```

```text
4 failed, 1 passed in 3.35s
```

The added rejection-coverage matrix initially produced one harness RED:

```text
1 failed, 20 passed, 269 deselected
test_offline_auditor_reconstructs_all_training_model_selection_rejections[
    mismatch-artifact_foreign]
```

The fixture's selected record already used repeat `3`, so the test mutation was
a no-op. The test now changes it to a different repeat. This was not a product
defect. The exact GREEN command was:

```bash
scripts/run_bounded.sh --memory-high 1G --memory-max 2G --swap-max 256M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_validation_study.py \
  -k 'retained_prerequisite_codec_rejects_invalid_public_forms or retained_prerequisite_rejection_branches or environment_binding_after_the_first_identity_check or foreign_image_lock_binding or capture_lineage_that_disagrees or training_model_selection_rejections or natural_variation_without_common_controls or fixture_generator_rejects_natural_variation_with_mismatched_windows'
```

```text
21 passed, 269 deselected in 20.83s
```

| Command | Result |
| --- | --- |
| `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 10m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py --junitxml=/tmp/task13-round2-postfixture-766986.xml` | `294` tests, `0` failures, `0` errors, `0` skips after final fixture regeneration. |
| Same validation target with `--cov=scripts.run_validation_study --cov=scripts.audit_validation_study --cov=scripts.generate_validation_study_fixture --cov-branch --cov-fail-under=0 --cov-report=json:/tmp/task13-round2-finalcore-765076.json` | `294` tests, `0` failures/errors/skips; every listed core function below has no missing executable line or branch exit. |
| `uv run --locked python scripts/generate_validation_study_fixture.py --check --source-commit 23125d4c03898910f3643ca4492851a381a9bf06 --source-tree 706ffb8bca2b19457f5b30a9972eed85463f78d5` | `checked-in paths and bytes match deterministic production output`; `155` retained regular files. |
| Scoped `ruff format --check`, `ruff check`, and strict `pyright` across the five Task13 scripts/tests | `5 files already formatted`; `All checks passed!`; `0 errors, 0 warnings, 0 informations`. |
| `uv run --locked ruff check . && uv run --locked pyright` | `All checks passed!`; `0 errors, 0 warnings, 0 informations`. |
| `scripts/run_bounded.sh --memory-high 3G --memory-max 4G --swap-max 512M --wall-time 10m --kill-after 10s -- uv run --locked pytest -q -n 4 --dist worksteal -m 'not docker and not internet' --junitxml=/tmp/task13-round2-full-768355.xml` | `2952 passed in 28.61s`. |

### Literal round-two core coverage

`/tmp/task13-round2-finalcore-765076.json` reports 100% executable-line and
branch coverage for every review-affected core function:

- `scripts/run_validation_study.py`: `prerequisite_junit_counts:2432`, `prerequisite_command_argv:2945`, `validate_frozen_prerequisite_command:2957`, `render_retained_prerequisites:3139`, `parse_retained_prerequisites:3144`, `retained_prerequisite_paths:3152`, and `publish_audited_bundle:5600`.
- `scripts/audit_validation_study.py`: `_environment:533`, `_prerequisites:719`, `_capture_lineage:885`, `_require_config_images:896`, `_training:909`, `_selected_training:1164`, `_held_out:1222`, `_report_inputs:1351`, `_audit:1468`, and `audit_bundle:1674`.
- `scripts/generate_validation_study_fixture.py`: `_capture_lineage:119`, `_selected_training_records:149`, `_natural_variation:181`, `validate_source_identities:354`, and `generate_fixture_tree:365`.

The combined scoped report is 94% branch-aware because unrelated CLI and
defensive branches remain outside this review's defect set. The named core
functions are each 100% lines and branches.

### Provenance and files

- Final source-tree commit: `23125d4c03898910f3643ca4492851a381a9bf06`.
- Final source tree: `706ffb8bca2b19457f5b30a9972eed85463f78d5`.
- Fixture regeneration commit: `56dfae7`.
- Earlier round-two source commits retained in history: `828939b`, `32b2ab5`, and `8111039`.
- Review-fix files: `scripts/run_validation_study.py`, `scripts/audit_validation_study.py`, `scripts/generate_validation_study_fixture.py`, `tests/unit/test_validation_study.py`, `tests/integration/test_validation_study_pipeline.py`, and `tests/fixtures/validation_study_candidate/`.

### Self-review and concern

No external action, Docker invocation, network access, workflow engine,
database, or producer-private reconstruction helper was introduced. Existing
public parsers and stage boundaries remain the owners of scientific validation.
The repository-wide `ruff format --check .` remains non-green only for six
pre-existing unrelated files under `docs/superpowers/plans/` and failure,
preflight, and study test modules; they were deliberately left untouched.
