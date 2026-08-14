# Task 8 — Public capture CLI and installed-entry integration

## Result

- Added `trafficlab capture EXPERIMENT` as a thin in-process CLI route.
- Successful capture prints `capture: packets=N output=PATH`.
- Expected `TrafficlabError` values retain the `capture` stage and corrective
  action. A capture-stage `KeyboardInterrupt` prints an actionable message and
  exits 130.
- `capture` alone imports the capture/Docker stack lazily; config-only preflight
  remains Docker-import-free.
- The installed entry-point test runs against a bounded fake `docker`, checks
  direct Docker argv (never `sudo`), and records Docker's direct parent argv to
  prove no internal Python relay sits between the entry point and Docker.

## TDD evidence

- RED: guarded serial scope `run-r6eca991a84974339b1f898b9d6efdf06.scope`;
  5 failures, all expected missing capture registration/injection. The preceding
  first draft also revealed a missing `Path` test import, corrected before the
  confirmed red run.
- GREEN: guarded serial scope `run-re04b20f0f454418c91ed69e86c686cd4.scope`;
  `31 passed in 0.83s` for `tests/unit/test_package.py`,
  `tests/integration/test_capture_cli.py`, and
  `tests/integration/test_preflight_cli.py`.
- Every pytest invocation used the prescribed serial `systemd-run` memory and
  timeout guard with `-n 0`; post-scope checks found no pytest descendant.

## Verification

- `uv run --locked ruff format --check src/trafficlab/cli.py tests/unit/test_package.py tests/integration/test_capture_cli.py tests/integration/test_preflight_cli.py` — pass.
- `uv run --locked ruff check src/trafficlab/cli.py tests/unit/test_package.py tests/integration/test_capture_cli.py tests/integration/test_preflight_cli.py` — pass.
- `uv run --locked pyright` — `0 errors, 0 warnings, 0 informations`.
- `git diff --check` — pass.

## Review

Independent review found and this task fixed two Important issues before commit:

1. Eager capture import could violate config-only's Docker-free import contract;
   resolved through a lazy capture import and reload regression test.
2. Fake-Docker argv alone could not exclude an internal Python relay; resolved
   by recording and asserting Docker's direct parent argv.

## Files

- `src/trafficlab/cli.py`
- `tests/unit/test_package.py`
- `tests/integration/test_capture_cli.py`
- `tests/integration/test_preflight_cli.py`
- `architecture/SYSTEM.md`

## Concerns

No remaining task-local concerns. Real Docker and Internet evidence remains the
separate Task 9 environmental requirement.

## Commit

`0c6b8c1 feat(capture): expose capture CLI`
