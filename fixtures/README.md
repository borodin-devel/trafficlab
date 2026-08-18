# Repository Fixtures

This directory is the single root for deterministic, reusable repository fixture data.
Production examples, accepted validation-study evidence, and pytest fixture functions are
not fixture data and remain with their owning code or documentation.

## Layout

- `examples/pipeline/`: small deterministic inputs and expected outputs for documented
  pipeline examples.
- `tests/diagnostics/`: expected diagnostic documents and rendered reports.
- `tests/docker/`: static Docker integration inputs.
- `tests/process_guard/`: helper programs executed by process-guard tests.
- `tests/validation_study/candidate/`: the current deterministic validation-study bundle.
- `tests/validation_study/pre-user-agent-r6/`: the retained pre-user-agent compatibility
  bundle.

## Integrity

`manifest.json` binds every fixture file's repository-relative path, byte size, SHA-256,
and Unix permission bits. It excludes this README and the manifest itself.

After an intentional fixture change, regenerate and verify it with:

```bash
uv run --locked python scripts/check_fixture_layout.py --write-manifest
uv run --locked python scripts/check_fixture_layout.py --check-manifest
```

Symlinks and other non-regular entries are prohibited. Fixture generators must write
deterministically, and tests must consume these root paths rather than private copies.
