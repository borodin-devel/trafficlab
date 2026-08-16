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
| Bounded Task13 unit/in-process target | Final exact result appended after fixture provenance regeneration. |
| Same target with `--cov=scripts.audit_validation_study --cov=scripts.generate_validation_study_fixture --cov=scripts.run_validation_study --cov-branch --cov-fail-under=0 --cov-report=json:.coverage-task13-core-final-02.json` | No missing executable lines or branch exits in every core function listed below. |
| `uv run --locked python scripts/generate_validation_study_fixture.py --check` | Checked-in candidate bytes equal deterministic owner output. |
| `uv run --locked ruff check . && uv run --locked pyright` | `All checks passed!`; `0 errors, 0 warnings, 0 informations`. |
| Bounded `pytest -q -n 4 --dist worksteal -m 'not docker and not internet'` | Final exact result appended after fixture provenance regeneration. |

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
