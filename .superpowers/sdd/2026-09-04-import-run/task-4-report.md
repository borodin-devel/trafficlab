# [REPORT-1-e2d73c10] Task 4 implementation report

Task 4 exposes the approved imported-reference coordinator through the public
CLI and makes that command the shortest documented English and Russian
workflow. Work was performed in the existing isolated worktree on
`feature/import-run-v2`, starting from `b406897ef994dd01f659612547734fb8bbd7f67a`.
No dependency, configuration field, artifact schema, process boundary, Docker
fallback, or alternate pipeline was added.

## [REPORT-2-88af425e] Delivered behavior

- `trafficlab import-run [-h] EXPERIMENT DUMP_DIRECTORY` accepts exactly two
  positionals and dispatches one injected or lazily imported
  `run_imported_experiment(Path, Path)` boundary.
- Success shares the `run` family, fitness, packet-count, aggregate-score, and
  output-directory formatter with the exact `import-run:` prefix.
- `TrafficlabError` preserves its status and corrective action. Interruption
  returns 130 and directs the user to inspect `run.log` before retrying
  `import-run`.
- The complete integration runs real raw-PCAP normalization, config-only
  preflight, fit, generation, comparison, final artifact validation, and
  `run_completed` publication in one process.
- The integration actively rejects subprocess/Popen calls, Docker-adapter
  imports, and `scripts` imports. It requires exact source/published content
  identities and the canonical nine-file inventory.
- An exact second invocation proves imported-reference, terminal checkpoint,
  best-model, generated-capture, and comparison reuse without changing any
  scientific artifact. A one-byte source change proves capture-stage rejection
  while preserving all scientific artifacts.
- README and QUICK_START_RU now lead their workflow instructions with the same
  named `balanced.toml` profile and one-command import. Manual preflight, copy,
  fit, generate, and compare remain documented as advanced stage/resume work.
- SYSTEM and TESTING contain the stable CLI and in-process verification
  contracts without progress or implementation-status prose.

## [REPORT-3-9c465af1] RED/GREEN evidence

CLI RED:

```text
uv run --locked pytest -q -n 0 tests/unit/pipeline/test_cli.py -k import_run
8 failed, 17 deselected
```

All failures named the absent `import_run` injection/parser command. After the
minimal CLI implementation, the same selection passed `8 passed, 17
deselected`.

Integration RED first exposed an invalid test-only family-table reduction, then
a deterministic final-seed trace too short for lag-one autocorrelation. The
fixture configuration was corrected independently by retaining only the enabled
Poisson table and constraining its configured rate factor to a deterministic
sufficient-event range. Production acquisition and scientific code required no
Task 4 modification. The resulting public-coordinator test passed in 1.36 s.

## [REPORT-4-de4c0892] Verification evidence

The prescribed focused gate completed successfully:

```text
ruff format --check: 3 files already formatted
ruff check: All checks passed
pyright: 0 errors, 0 warnings, 0 informations
pytest focused CLI/import/layout selection: 106 passed in 2.83s
trafficlab import-run --help: usage: trafficlab import-run [-h] EXPERIMENT DUMP_DIRECTORY
git diff --check: clean
```

The additional branch-aware CLI gate also completed successfully:

```text
28 passed in 2.78s
src/trafficlab/cli.py: 91.78% branch-aware coverage
```

The pre-task narrow acquisition regression remained green: `65 passed in 2.69s`.

## [REPORT-5-3c0dc1bf] Self-review

Review covered command ergonomics, exact parser arity, lazy imports, summary
parity, structured failures, interrupt wording/status, public coordinator use,
config-only preflight evidence, raw normalization identities, real downstream
stages, exact inventory, retry immutability, changed-source preservation, and
English/Russian consistency. No Critical or Important finding remains. The
implementation keeps the CLI change local and factors only the already shared
run summary; it does not refactor unrelated command dispatch.

## [REPORT-6-4534bd12] Deferred controller gate

Plan Step 44 remains unchecked exactly as requested for controller-led
independent public-flow review. The deferred Task 2 spool diagnostic Minor was
not changed. No remote operation, merge, tag movement, or other worktree change
was performed.
