# Task 13 - Offline validation-study auditor

## Scope and result

Implemented a deterministic, offline audit path for a retained validation-study
candidate and wired it into the existing accepted-bundle publisher. The auditor
does not start Docker, create network connections, invoke subprocesses, mutate
the candidate, or regenerate scientific artifacts.

The authoritative adverse-condition fixture remains
`tests/fixtures/diagnostics/failure-outcomes.jsonl`; the obsolete plan spelling
`canonical_adverse_conditions.jsonl` was not introduced.

## Implementation

| Boundary | Implementation and evidence |
| --- | --- |
| Candidate inventory | `scripts/audit_validation_study.py` rejects symlinks, temporary paths, non-regular files, missing files, unlisted files, duplicate JSON keys, invalid manifest records, wrong owners, and wrong lineage before scientific reconstruction. |
| Manifest | `write_manifest()` writes deterministic compact JSON with every retained regular file's relative path, owner, size, SHA-256, and lineage. `manifest.json` is deliberately excluded from recursive hashing. |
| Run evidence | The auditor strictly parses TOML, checkpoint, best-model, capture, similarity, history, PCAPNG, and JSONL run logs. It validates schema 2, portable/effective config rendering, environment controls, exact nine-file run inventory, checkpoint/history/winner/final controls, and all content identities and lineage edges. |
| Scientific reconstruction | It uses the existing reconstruction owners to reproduce the winner's final trace and all four comparison methods plus their aggregate, then checks raw identities and result fields. |
| Study report | It reconstructs pairwise natural variation and validates the persisted report arithmetic. |
| Publication | `scripts/run_validation_study.py publish` delegates audit-before-publish to existing `publish_accepted_bundle`, preserving its atomic collision behavior and occupied destination bytes. |

The credential-free checked fixture is
`tests/fixtures/validation_study_candidate/`: 29 files total, consisting of a
manifest, an index, and three complete nine-artifact retained run trees. The
manifest contains 28 retained files because it does not hash itself.

## RED evidence

1. Initial focused collection RED: importing `scripts.audit_validation_study`
   failed because the module did not exist.
2. The first fixture assembly attempted direct generation from the intentionally
   portable Phase 5 snapshot. Correct preflight rejected its relative run
   directory. The auditor now retains the stored portable configuration bytes
   while rebasing only the in-memory effective configuration for offline
   reconstruction.
3. Initial strict parsing incorrectly required compact canonical encoding for
   production `capture.json` and `run.log`. Production only promises valid
   duplicate-safe decoding for those formats, so the auditor now rejects
   malformed/duplicate content without imposing an unsupported byte encoding.
4. Direct file execution RED:
   `uv run --locked --offline python scripts/audit_validation_study.py ...`
   raised `ModuleNotFoundError: No module named 'scripts'`. A repository-root
   bootstrap and an in-process `__main__` regression make the documented CLI
   executable directly.
5. Strict Pyright RED: `runpy.run_path()` required a string path in the new
   direct-script regression. The test now passes `str(path)`.

## GREEN evidence

| Command | Result |
| --- | --- |
| `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 3m --kill-after 10s -- uv run --locked pytest -q -n 0 tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py -k 'offline_bundle or audit_cli or audited_bundle or strict_artifact_parser or audit_script_main'` | `20 passed, 175 deselected in 0.87s` |
| `uv run --locked --offline python scripts/audit_validation_study.py tests/fixtures/validation_study_candidate --repository .` | Accepted `28 retained files` offline. |
| Focused coverage command with `--cov=scripts.audit_validation_study --cov-branch --cov-fail-under=0 --cov-report=json:.coverage-task13.json` | `20 passed`; narrow whole-script coverage `79%`. |
| `uv run --locked python scripts/generate_fit_fixtures.py --check` | Checked-in Phase 5 fit fixture bytes match deterministic owner output. |
| Bounded validation-study target | `195 passed in 16.45s`. |
| Scoped `ruff format`, scoped `ruff check`, repository `ruff check .`, and strict `pyright` | `All checks passed!`; `0 errors, 0 warnings, 0 informations`. |
| `scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G --wall-time 10m --kill-after 10s -- uv run --locked pytest -q -n 4 --dist worksteal -m 'not docker and not internet'` | `2853 passed in 14.86s`. |

## Function coverage

The parser behavior RED identified `_strict_artifacts()` as the affected
function. Branch-aware coverage JSON reports no missing executable lines or
branch exits in source range `scripts/audit_validation_study.py:449-480`.
The bounded focused coverage run intentionally leaves defensive failure paths
outside the exercised matrix, yielding 79% for the entire new script; it does
not reduce the required 100% evidence for the RED-identified function.

## Canonical failures

Audit failures are wrapped at the publication boundary as finite canonical
`FailureOutcome` records, preserving delegated typed outcomes when an owning
stage already provides one. The table-driven tests cover missing, corrupt,
foreign/unlisted, symlink, temporary, owner, lineage, duplicate-key,
environment, final-control, and occupied-publication conditions, including
preservation or nonpublication assertions.

## Files

- `scripts/audit_validation_study.py`
- `scripts/run_validation_study.py`
- `tests/unit/test_validation_study.py`
- `tests/integration/test_validation_study_pipeline.py`
- `tests/fixtures/validation_study_candidate/`
- `examples/validation_study/README.md`

## Self-review and concerns

The audit is deliberately a small adapter over existing artifact parsers,
scientific reconstruction, and publication ownership; it does not create a
workflow engine, database, network client, or security subsystem. All tests
block Docker/process/network paths where applicable. The three-run fixture is a
credential-free deterministic audit fixture, not a substitute for Task 14's
real validation-study protocol and accepted evidence bundle.
