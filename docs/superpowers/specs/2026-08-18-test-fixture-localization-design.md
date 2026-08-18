# [DESIGN-1-c07f4eca] Test Fixture Localization Design

## [SECTION-1-2e323a0d] Goal

Replace the repository-wide `fixtures/` taxonomy with ownership-local data:

- runnable example assets live under `examples/data/`;
- static test-only assets live under `tests/fixtures/data/<domain>/`;
- shared test path constants live in `tests/fixtures/paths.py`;
- production package code never imports or reads from `tests/`.

The migration preserves fixture bytes, executable modes, deterministic generator
behavior, validation-study provenance, and the existing phase-free filename rule.

## [SECTION-2-af4e12e0] Structure

```text
examples/
  data/
    manifest.json
    ... runnable example inputs and expected outputs ...
tests/
  fixtures/
    __init__.py
    paths.py
    data/
      manifest.json
      diagnostics/
      docker/
      process_guard/
      validation_study/
```

Example configuration may reference `examples/data/`. Test modules may import
`tests.fixtures.paths` and consume either example data or test-only data.
`src/trafficlab/` may not reference `tests/`, `tests.fixtures`, or
`tests/fixtures`.

## [SECTION-3-1a1dde4e] Integrity and provenance

Each static-data owner has its own canonical manifest containing regular-file
path, size, SHA-256, and mode. Generated Python cache directories are excluded.
The layout checker rejects the obsolete root `fixtures/`, direct static data in
`tests/docker`, and the old `tests/support/fixture_paths.py` catalog.

The validation-study fixture remains source-bound. Its source commit includes
all production and example-data changes; the later deterministic fixture commit
changes only test infrastructure. The public auditor may treat committed
`tests/` changes as non-production, but it does not import, open, or name any
test fixture path.

## [SECTION-4-83383476] Verification

Use TDD for the layout contract, preserve byte/mode parity through moves, run all
fixture generators in `--check` mode, audit a regular copied validation bundle,
and run the standard locked static, non-external, branch-coverage, and available
Docker gates. Independent review must report no Critical or Important findings.
