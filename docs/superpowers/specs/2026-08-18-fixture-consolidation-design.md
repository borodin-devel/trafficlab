# [PLAN-1-a6385796] Fixture Consolidation Design

## [SECTION-1-a7da61b4] Scope

This change centralizes checked deterministic fixture data under one repository-root
`fixtures/` tree, removes `phase` from every tracked filename, and adds concise
progress and generated-task identifier rules to `AGENTS.md`.

The migration covers static fixture bytes currently owned by `tests/fixtures/`,
`tests/docker/compose.endpoint.json`, and `examples/data/`. It also covers the
scripts, tests, configurations, and documentation that address those bytes.
Accepted validation-study evidence remains under
`examples/validation_study/evidence/<study-id>/` because that is a published
scientific artifact with an architecture-mandated location, not a reusable test
fixture. Pytest fixture functions remain Python test support rather than data.

## [SECTION-2-17e43a6c] Root Taxonomy

The destination layout is responsibility-based:

```text
fixtures/
  README.md
  manifest.json
  examples/pipeline/                 # deterministic CLI/example artifacts
  tests/diagnostics/                 # canonical adverse outcomes
  tests/docker/                      # static Docker test documents
  tests/process_guard/               # executable process-tree fixture
  tests/validation_study/candidate/  # complete offline candidate fixture
  tests/validation_study/pre-user-agent-r6/
```

`tests/support/fixture_paths.py` owns path constants used by tests. Production
scripts use repository-root-relative constants directly so they do not import
test code. Generator scripts remain under `scripts/`; generated data moves, the
generator implementation does not.

## [SECTION-3-9ab1c022] Copy-Verify-Switch-Delete Migration

The migration is intentionally non-atomic across commits:

1. Copy legacy trees byte-for-byte into `fixtures/` and generate a sorted
   size/SHA-256 manifest. Keep every legacy byte present.
2. Compare each copied tree against its source and run existing generator check
   modes from the copied destinations.
3. Switch all code, tests, examples, and documentation to root fixture paths.
4. Rename the six historical `phase-N` plan files to their specific outcome
   names and rename `generate_phase2_fixtures.py` to
   `generate_similarity_fixtures.py`; update every reference.
5. Delete legacy fixture paths only after focused and repository-wide checks use
   the copies successfully.

This sequence makes the user's “first copy and check” requirement observable in
Git history and prevents an unverified move from losing fixture bytes.

## [SECTION-4-47c5c830] Validation

`scripts/check_fixture_layout.py` owns deterministic manifest generation/checking
and repository layout validation. It rejects:

- a manifest entry whose size or SHA-256 differs;
- an unmanifested regular fixture file;
- a tracked basename containing `phase` (case-insensitive);
- legacy `tests/fixtures`, `examples/data`, or Docker compose fixture paths after
  cutover; and
- fixture references that resolve outside root `fixtures/`, except the explicit
  accepted-evidence and pytest-function exclusions.

Tests first exercise these failures in temporary repositories, then assert the
real repository layout. Existing generator `--check` modes, Ruff, strict Pyright,
the complete non-external suite, branch coverage, and available Docker tests form
the final gate.

## [SECTION-5-0ac9087f] Naming and Governance Exclusions

The filename rule applies to tracked basenames, not established domain terms in
source APIs, JSON schemas, historical prose, or runtime values. Renaming
`phase_capture_image` or `PhaseCaptureImage` would be an unrelated public schema
change; only filenames are in scope.

Generated task documents created after this rule lands use immutable labels of
the form `[TASK-<ordinal>-<crc32>]`, `[STEP-<ordinal>-<crc32>]`, or an equivalent
kind. The CRC is eight lowercase hexadecimal digits computed from the UTF-8
nanosecond creation timestamp plus document path, kind, and ordinal; a new
timestamp is generated on collision. Progress updates use integer whole-task
percentages and include an ETA when a task starts or the percentage changes.
