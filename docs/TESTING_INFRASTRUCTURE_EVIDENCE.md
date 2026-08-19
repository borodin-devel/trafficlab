# Testing Infrastructure Evidence

This report preserves the collection, performance, and coverage evidence used
to accept the 2026-08-19 testing-infrastructure refactor. Temporary coverage
JSON files were normalized to stable file/line/branch projections before
hashing; timestamps and tool-generated display metadata were excluded.

## Revisions and tools

- implementation baseline: `87ae0a0a1db94ef58fdac2f8fafb5d7521bd3304`;
- modularized test commit measured for equivalence:
  `c6e38361a456340b12fa1efcef8bcf1dd5e3a857`;
- measured Git tree: `1d950c6c93ae8856822554b5249d6285aae26e24`;
- CPython 3.12.3;
- pytest 9.1.1 and pytest-xdist 3.8.0;
- Coverage.py 7.15.4 with the C extension and pytest-cov 7.1.0;
- Ruff 0.16.2 and Pyright 1.1.411.

The coverage measurements ran before the documentation-only coverage-decision
commit. That commit did not alter production or test execution. Final release
verification reruns the documented command on the delivery tree.

## Collection preservation

The baseline collected 3,521 tests: 3,502 offline, 204 carrying the integration
marker, 18 carrying Docker, and one carrying Internet. Every external case also
carries integration, so the ordinary post-hook `-m integration` selection is
185 after the 19 external cases are deselected; it is not the raw marker
incidence. The refactor adds one direct configuration-builder contract and one
root-conftest collection contract, while renaming an imported
`test_body_failure` helper that pytest accidentally collected. The resulting
inventory is 3,522 total and 3,503 offline; raw marker incidence remains
204 integration, 18 Docker, and one Internet.

The monolithic validation-study owner had 256 declared test functions and 735
intended collected cases after excluding the accidental imported helper. The
same names and parametrized suffixes now map as follows:

| New owner | Test functions | Collected cases |
| --- | ---: | ---: |
| `test_protocol.py` | 55 | 177 |
| `test_orchestration.py` | 41 | 89 |
| `test_audit.py` | 77 | 281 |
| `test_prerequisites.py` | 40 | 77 |
| `test_audit_boundaries.py` | 43 | 111 |
| Total | 256 | 735 |

For the semantic case manifest, normalization removes only the old/new owner
path and retains everything after the first `::`, including parametrized case
suffixes. Sorted UTF-8 lines without a trailing newline have SHA-256
`351610a185cea314cb9f14a24af4431450ce6f279a098409478cac198815e8e8` before and
after. Sorted declared test-function names have SHA-256
`47abdf2b6b153fd5587a3a7695525d78244c0f125742d25390109215ab504c2e` before and
after. None of these unit owners carries `integration`, `docker`, or `internet`.

## Runtime profile

The four-worker ordinary command was:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" --durations=50
```

The 3,502-test baseline passed in 53.77 seconds. The refactored 3,503-test run
passed in 53.70 seconds, so the ownership split is runtime-neutral. The long
tail remained the validation-study integration collection and audit boundaries;
no scientific case was removed or shortened.

## Coverage equivalence

The serial reference used `-n 0`; each parallel candidate used
`-n 4 --dist worksteal`. All three selected and passed the same 3,503 offline
tests with `--cov=trafficlab --cov-branch --cov-fail-under=90`:

| Run | Status | Seconds | Files | Statements | Missing lines | Branches | Missing branches | Total |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Serial reference | 3,503 passed | 561.71 | 42 | 8,842 | 158 | 2,946 | 109 | 97.73498473023413% |
| Parallel candidate 1 | 3,503 passed | 205.89 | 42 | 8,842 | 158 | 2,946 | 109 | 97.73498473023413% |
| Parallel candidate 2 | 3,503 passed | 214.56 | 42 | 8,842 | 158 | 2,946 | 109 | 97.73498473023413% |

For each source file, the normalized projection contains sorted
`executed_lines`, `missing_lines`, `executed_branches`, and `missing_branches`.
Canonical JSON with sorted keys and compact separators has SHA-256
`0ee2124f8e579f6af58dac90a34544a167bd09b1cd1f2ac354843b72b5f4dc9c` for all
three runs. Parallel coverage is therefore 2.62–2.73 times faster while
preserving the exact measured line and branch sets.

## Retained evidence and external gates

All four deterministic generators passed in `--check` mode: similarity, model,
genetic-fitting/checkpoint-resume, and validation-study fixtures. A no-local,
no-hardlink clone detached at retained source commit
`ca2522dcfae5b39a44355d9df5329744847b7136` received a regular-file copy of the
accepted bundle and the offline auditor accepted all 231 retained files.

The combined serial external selection collected each external case once and
passed 18 Docker plus one Internet test in 434.01 seconds. It used the retained
credential-free HTTPS object URL and verified project/image cleanup before
completion.

## Reproduction requirements

Repeat the serial/parallel comparison before changing pytest, pytest-xdist,
pytest-cov, Coverage.py, worker count, distribution mode, or test selection.
Acceptance requires equal statuses, case cardinality, source-file inventory,
and normalized projection digest; equal aggregate percentage alone is
insufficient.
