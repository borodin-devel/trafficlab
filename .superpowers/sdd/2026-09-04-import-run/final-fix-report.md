# [REPORT-1-6aa91077] Import-run final-review fix report

This single coordinated fix wave starts from `FIX_BASE=55039b4` on
`feature/import-run-v2`. It addresses all seven Important findings and both
Minors from the controller-supplied whole-branch review package. It adds no
dependency, configuration field, schema version, process boundary, Docker
path, or source-tree write. Plan Steps 1–53 are reconciled as complete and
Step 54 remains the sole unchecked controller-owned scoped re-review gate.

## [REPORT-2-f3a041cd] Technical resolution

- Raw PCAPNG structure validation now uses one bounded option walker for IDB
  and EPB areas. It validates complete headers, value-plus-padding bounds,
  end-marker length/finality, `if_tsresol` length exactly one, and the existing
  decoder-sensitive EPB option lengths before Scapy is called. Spool seek/read
  `OSError` now names the spool-read boundary.
- Imported reads, hashes, and snapshot copies open the source first with
  `O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`, validate the bound descriptor with
  `fstat`, stream only through that descriptor, and compare the final path
  identity without reopening it.
- Run-directory creation traverses every existing parent component with
  directory descriptors and `O_NOFOLLOW`, creates missing components relative
  to the bound descriptor, and requires the visible parent/new directory to
  retain the bound device/inode identity. The exact post-overlap-guard parent
  swap to a source-pointing symlink is rejected without creating a source
  entry.
- Reuse now rechecks the absolute deadline, complete current source inventory
  and identities, canonical output content/file identities, the sole
  non-reused authority, and every matching reuse record after appending its
  reuse lineage. Fresh publication also rechecks deadline and source after its
  append before returning.
- `mkdtemp` filesystem failures are translated to actionable `TrafficlabError`
  inside acquisition, so the coordinator appends the ordinary capture-stage
  `run_failed` record.
- Publisher cleanup warnings are no longer ignored. Imported acquisition
  retries only retained `.capture-pair.*` entries whose identities match the
  publisher-owned pair; unresolved, unowned, or repeatedly failing cleanup
  stops acquisition before successful import lineage.
- `balanced.toml` now presents `trafficlab import-run` first and labels manual
  preflight/copy/fit/generate/compare as advanced diagnosis or resume.

## [REPORT-3-84a7715b] RED evidence

The clean pre-change affected baseline was:

```text
uv run --locked pytest -q -n 0 tests/unit/common/test_scapy_raw.py tests/unit/pipeline/test_imported.py tests/unit/pipeline/test_imported_review.py tests/unit/pipeline/artifacts/test_capture.py tests/unit/pipeline/artifacts/test_capture_interrupt.py tests/unit/preflight/test_stage.py tests/integration/preflight/test_preflight_cli.py -k 'not docker'
271 passed, 6 deselected in 4.40s
```

New raw regressions failed for the intended missing behavior:

```text
uv run --locked pytest -q -n 0 tests/unit/common/test_scapy_raw.py -k 'malformed_if_tsresol or malformed_bounded_option or spool_seek_or_read'
9 failed, 76 deselected in 3.82s
```

The failures showed malformed IDB/EPB options were accepted or reached Scapy,
and spool `OSError` escaped unwrapped.

New import/preflight regressions all failed for the intended gaps:

```text
uv run --locked pytest -q -n 0 tests/unit/pipeline/test_imported_review.py -k 'bind_nonfollowing or parent_swapped or temp_root_creation or publisher_cleanup_warnings or revalidates_every_authority or rechecks_source_after'
16 failed, 43 deselected in 4.88s
```

They demonstrated pathname opening instead of a bound descriptor, source-tree
creation after the parent swap, raw `PermissionError`, ignored publication
warnings, and missing post-append checks.

The profile contract also failed before its header changed:

```text
uv run --locked pytest -q -n 0 tests/integration/preflight/test_preflight_cli.py -k balanced_profile_header
1 failed, 28 deselected in 0.20s
```

## [REPORT-4-1ac3e4f8] GREEN and affected-owner evidence

The exact RED selections became green:

```text
raw regression selection: 9 passed, 76 deselected in 0.34s
import/preflight regression selection: 16 passed, 43 deselected in 1.62s
balanced profile selection: 1 passed, 28 deselected in 0.22s
```

The profile test initially split at the comment text `[run].directory` rather
than the TOML table boundary; changing its independent delimiter to
`"\n[run]\n"` made the intended behavioral check green.

Formatting changed three files and left four already formatted:

```text
uv run --locked ruff format src/trafficlab/common/scapy_io/raw.py src/trafficlab/pipeline/imported_io.py src/trafficlab/pipeline/imported.py src/trafficlab/artifacts/run_directory.py tests/unit/common/test_scapy_raw.py tests/unit/pipeline/test_imported_review.py tests/integration/preflight/test_preflight_cli.py
3 files reformatted, 4 files left unchanged
```

The affected raw/import/preflight/publisher/config/CLI/in-process integration
owner selection first reported `400 passed, 5 failed`. Those five were confined
to one preserved error-text expectation, two test-only path-state call indices,
and the repository's 600-line production/1000-line test cohesion limits. The
copy and warning helpers were moved to their existing focused I/O owner, the
test-only independent PCAPNG builder moved to `tests/support/scapy_raw.py`, and
the call indices/error wording were corrected. The same owner selection then
reported `403 passed, 2 failed`; both remaining failures were a stale test hook
that still addressed `imported_module.os` after cleanup ownership moved to
`imported_io`. The exact failed owner rerun after that test-only correction was:

```text
uv run --locked pytest -q -n 0 tests/unit/pipeline/test_imported_review.py -k publisher_cleanup_warnings
2 passed, 57 deselected in 0.36s
```

No production file changed after the `403 passed` affected-owner run.

## [REPORT-5-6dc4d46e] Coverage and deferred verification truth

The raw owner itself passed all `85` tests under `coverage run`, but the copied
plan command used a filesystem path in `--source`; Coverage treated it as a
module name, collected no data, and exited 2. A follow-up module-form source
attempt hit the environment's intermittent NumPy
`ImportError: cannot load module more than once per process` during collection.
No 100% coverage claim is made from those failed reports.

At the controller's explicit speed stop, no additional suite was authorized.
Consequently this wave does not claim a fresh Ruff check, strict Pyright,
deterministic fixture checker, or installed PCAP/PCAPNG reproduction. The
checked fixture/evidence bytes were not edited. These commands remain for the
single scoped re-review controller rather than being represented as passed.

## [REPORT-6-f4cd85d4] Self-review

Self-review traced each new test to the production mutation it catches and
checked the final diff for the approved constraints. The option walker accepts
an exactly exhausted option area with or without a terminal marker, rejects a
nonzero or nonfinal end marker, and does not interpret option payloads beyond
the required decoder-sensitive lengths. Descriptor opens cannot follow a
symlink or block on a FIFO. Snapshot bytes remain chunked and source-only.

Run creation retains ordinary recursive-parent behavior while refusing linked
traversal and checking the descriptor/path identity before publication. Import
warning recovery removes only publisher-owned hard-link identities; any
uncertain entry is preserved and fails before lineage. Post-append failures may
leave the truthful append already durably present, but they do not return a
success authority. Canonical capture pairs are preserved, and the coordinator
continues to own `run_failed` publication. No shell, subprocess, repository
script, Docker import, artifact count, dependency, config, or schema surface was
added.

## [REPORT-7-83cab542] Commit and handoff

The coherent commit message for this report, production changes, tests, profile,
and plan reconciliation is `fix(import): close final review findings`. The
branch is handed back as `READY_FOR_SCOPED_REVIEW`; Step 54 intentionally stays
open and no push, merge, tag move, or other worktree was performed.
