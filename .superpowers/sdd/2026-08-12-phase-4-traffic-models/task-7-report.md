# Task 7 Report: Phase 4 Gate and Roadmap Accounting

## Status and scope

Phase 4's six deliverable boxes and five test boxes are proven and marked complete. Phase 3 remains `(Current)`;
its eight Docker/Internet-dependent test boxes, pending-evidence paragraph, and Done-when status are unchanged. No
Docker or Internet command ran. The controller will dispatch the independent Task 7 and whole-phase reviews; this
report does not claim independent approval.

Phase gate commit: `37f6ac1 docs: complete Phase 4 traffic models`.

The gate found two Class 1 Ruff formatting defects and one Class 3/4 process-guard timing defect. Ruff mechanically
formatted only the Phase 4 plan and `tests/unit/models/test_poisson.py`; behavior did not change. The process-guard
repair is retained separately in commit `17ec61c fix(tooling): bound late scope probe`. No Phase 4 production model,
traffic-model architecture contract, fixture, or Phase 3 external-evidence text changed during Task 7.

## Host safety and scope cleanup

Every broad or diagnostic pytest scope was run sequentially through `scripts/run_bounded.sh` with all five named
resource flags, `--`, and `uv run --locked`. Broad scopes used exactly `-n 4 --dist worksteal`; focused scopes used
`-n 0`. No raw pytest or direct systemd test invocation ran.

Fresh broad prechecks repeatedly reported approximately 14 GiB available host memory, 4 GiB configured swap with
0 bytes used, no pytest/model-fixture/Trafficlab test process, and no active `trafficlab-test-guard-*.scope`. Each
completed scope was followed by the same process/scope inspection. No pytest worker, fixture generator, Trafficlab
subprocess, or guard scope survived, including both failed coverage attempts.

## Narrow predecessor verification

Before Phase 4 accounting, the bounded serial Task 6/model-pipeline regression passed:

```text
tests/integration/test_generate_cli.py tests/integration/test_model_pipeline.py
37 passed in 0.62s
```

## Locked dependency and static gate

The first full format check found only two already-implemented files that Ruff would reformat: the active Phase 4
plan and two long lines in the Poisson test. Ruff formatted those files mechanically. The final post-fix locked gate
then reported:

```text
uv sync --locked --all-groups: resolved 20, checked 19
uv lock --check: resolved 20
ruff format --check .: 122 files already formatted
ruff check .: All checks passed
pyright: 0 errors, 0 warnings, 0 informations
git diff --check: clean
uv.lock SHA-256 before/after: 78e2de0c8f49600b5329af1cefb95ba68381a5b203c2d11188ec081e2a138a87
```

## Fast and coverage gates

The final exact fast command passed `1376 passed in 1.68s` with the required marker expression
`not integration and not docker and not internet`.

The first exact four-worker coverage attempt reached 96.17% and passed 1457 tests, but
`test_signal_during_activation_cannot_leave_a_late_scope` exceeded its two-second `communicate()` bound. Serial
diagnostics localized the failure:

- the exact failed node passed without coverage in 1.76 seconds;
- the exact node also passed with coverage instrumentation in 2.37 seconds (the pinpoint command itself exited only
  because project-wide `cov-fail-under=90` is intentionally unsuitable for one test);
- all seven serial containment tests passed in 8.59 seconds; and
- neither the failed outer scope nor its inner scope/process survived.

One controller-authorized replacement of the exact broad coverage gate reproduced only that same timing failure:
1457 passed, one timeout, and 96.17% coverage. The repeated RED established a guard robustness defect rather than a
Phase 4 model defect. Root cause: after reaping the activation launcher, cleanup performed 100 sequential late-scope
ownership probes plus 10 ms waits. Under xdist/coverage contention, those manager calls plus the controlled wrapper's
short-lived `sleep` pipe holder exceeded the existing prompt-return assertion.

The minimal fix leaves token/ack ownership and collision semantics intact. `stop_launcher` still TERM-waits-KILLs and
reaps the exact launcher first; `prove_ownership` still accepts only the private acknowledgement token or exact unit
Description token; any proven owned scope is still killed and polled inactive; an unrelated same-name scope is still
never claimed. Once the launcher is reaped, only an already-dispatched manager request can appear, so the bounded
late-ownership observation was reduced from 100 to 40 token probes. This retains a 40-query/approximately 400 ms
late-activation window while restoring the test's pre-existing under-two-second signal contract.

GREEN evidence after the fix:

```text
exact activation-signal node: 1 passed in 0.74s
complete containment proof: 7 passed in 4.43s
final exact four-worker coverage replacement: 1458 passed in 6.13s
total branch-aware package coverage: 96.17%
```

The seven containment cases cover invalid guard setup, child-status/scope cleanup plus existing-unit collision,
concurrent same-name unit ownership rejection, wall and memory termination of separate-session process trees,
external TERM cleanup of an active tree, TERM during delayed activation with no late scope, and fast nonzero child
status collection.

The final coverage report gives Phase 4 core files: artifacts 98%, generation 98%, common 93%, Poisson 96%, Markov
renewal 98%, MMPP 96%, and registry 94%. Direct behavioral tests cover every repair formula and estimator path;
Markov empty-row/conditional-source-global fallbacks; MMPP arrival/transition/tie races; common guards and limits;
strict nested JSON loading; generation stage failure/reuse; and generated publication reuse/rejection/races. Earlier
TDD reports retain 100% executable-line and branch evidence for every unit-test-identified defective function. Task 7
found no failed unit test and therefore introduced no new per-function 100% obligation.

## Fixture and Phase 4 pinpoint gates

The final fixture check reported byte identity. Exact retained hashes are:

```text
best_model.json   49c395c2d1d59bc092f93966fc5dc2d5b6007ad10b80f49759a541c1c0f8018e
generated.pcapng  69d4857801cc990891348afaabbc18c6e6ccd2f15bb05f6802a8fa4cb4fe8652
capture.json      2573517a5b00a2cdde835ea2b16e6d537f8dbd90c9de843aa55b70f1a8944315
reference.pcapng  fcbc3d5ab9a2b9d66f234850696c8ea98e02cb2e4d0dcd66889ca91103c40207
```

The final exact bounded serial pinpoint command over `tests/unit/models`, `test_model_pipeline.py`, and
`test_generate_cli.py` passed `394 passed in 0.79s` and left no process or scope.

## Roadmap accounting and self-review

Roadmap accounting is exactly 6/6 Phase 4 deliverables and 5/5 Phase 4 tests checked. Phase 3's `(Current)` marker,
eight unchecked external test boxes, evidence-pending paragraph, and unchecked Done-when condition remain exactly as
before. No Phase 5 or later checkbox changed.

The final self-review inspected model signatures, exact family/bounds/genes alignment, registry names/order,
fixture/stage/CLI flow, single-read lineage, no-reference-reopen behavior, complete-window semantics, frame/packet/byte
guards, stochastic draw ordering, hashes, and publication/reuse behavior. Placeholder/TODO/FIXME/NotImplemented,
implicit/public-boundary `Any`, global RNG, unordered sampling, raw test invocation, `-n auto`, and out-of-registry
name scans found no Phase 4 implementation defect. Explicit `Any` uses are confined to strict internal JSON helpers
or test doubles and pass strict Pyright. Model source owns local `random.Random` instances; test-only global RNG calls
prove isolation.

Carried Minor for the controller's final independent review: `publish_generated_pcapng` catches broad `Exception`
around creator-owned temporary publication and normalizes unexpected programming exceptions after cleanup. No
concrete failing behavior exposed it in Task 7, so it remains unchanged as directed.

## Independent-review fix round 1

The first Task 7 review found two Important process-guard defects in the earlier 40-probe repair. This section
supersedes that repair's attempt-count rationale above: 40 observations cannot establish that an already-dispatched
manager request has reached a definitive outcome, and a private acknowledgement proves only prior ownership, not
the identity of a later same-name unit incarnation.

The root causes were reproduced before the replacement implementation:

```text
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 5m \
  --kill-after 10s -- uv run --locked pytest -n 0 \
  tests/integration/test_process_guard.py::test_collected_owned_scope_replaced_before_cleanup_is_never_killed -q
RED: 1 failed in 0.37s; guard returned 125 and killed the unrelated replacement scope.

scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 5m \
  --kill-after 10s -- uv run --locked pytest -n 0 \
  tests/integration/test_process_guard.py::test_signal_after_dispatch_cancels_delayed_scope_ownership -q
RED: 1 failed in 2.16s; communicate(timeout=2.0) expired after actual in-scope dispatch, because the delayed
activation process retained the captured output pipes beyond guard exit.
```

The first regression deterministically waits for the original acknowledged `--collect` scope to disappear, creates
an unrelated same-name scope at the cleanup observation boundary, and proves that it remains active. The second
regression signals only after a real scope command has recorded dispatch, hides the ownership Description for three
seconds, and proves both prompt status 143 and absence of any later child or scope. An initial one-second version of
the second seam was insufficient because the old 40 manager queries sometimes spanned that delay; three seconds
made the unsupported cutoff deterministic without weakening the prompt-return or no-leak assertions.

The replacement implementation removes late ownership polling. The activation launcher now runs in a dedicated
`setsid` process group, and cancellation TERM-waits-KILLs and reaps both the launcher and that entire group. Thus the
activation request/process tree reaches a definitive stopped outcome without guessing how many manager queries are
enough. For a previously acknowledged scope, cleanup now reads its live Description token, treats absence or token
mismatch as a different incarnation, and repeats the token check immediately before any name-based kill. The private
acknowledgement remains valid only for establishing historical ownership; it no longer authorizes destructive
cleanup by itself. The controlled-PATH setup and invalid-dependency matrix also cover the new `setsid` dependency.

Focused GREEN and containment evidence, all serial and bounded with the same five flags, was:

```text
exact collected-scope replacement regression: 1 passed in 0.22s
exact delayed post-dispatch cancellation regression: 1 passed in 4.12s
complete tests/integration/test_process_guard.py containment: 9 passed in 5.99s
```

The nine containment behaviors now cover invalid setup/dependencies; exact child status and successful-command leak
cleanup; pre-existing collision; concurrent creator collision; acknowledged collected-scope replacement; wall and
memory termination of session-separated trees; external TERM of an active tree; TERM before activation dispatch;
TERM after actual dispatch with delayed ownership; and fast nonzero collection. These direct behavioral regressions
exercise every branch implicated by the two shell-function defects; Python line/branch instrumentation does not
apply to this Bash guard. Every focused scope was followed by exact process/unit inspection: approximately 14 GiB
memory remained available, swap use was zero, and no pytest, guard process, or `trafficlab-test-*` scope remained.

Post-fix verification was clean:

```text
uv sync --locked --all-groups: resolved 20, checked 19
uv lock --check: resolved 20
ruff format --check .: 122 files already formatted
ruff check .: All checks passed
pyright: 0 errors, 0 warnings, 0 informations
bash -n scripts/run_bounded.sh: passed
git diff --check: passed
uv.lock SHA-256: 78e2de0c8f49600b5329af1cefb95ba68381a5b203c2d11188ec081e2a138a87
```

No broad or coverage suite was rerun in this review-fix round; the controller owns the replacement final gate. The
Roadmap, Phase 3 evidence, and Phase 4 model code remain unchanged.

The review also labeled the retained two-commit Task 7 range Important. No history was rewritten: root `AGENTS.md`
requires coherent verified increments as durable history, `17ec61c` is the independently coherent guard fix and
`37f6ac1` is the separate Roadmap/accounting closure, and destructive history rewriting was outside this fix round's
authority. This response is recorded for scoped-review reassessment rather than represented as a code change.

The scoped re-review approved all three findings with no new Critical or Important breakage. Fresh controller-owned
replacement gates then ran sequentially through the fixed guard:

```text
fast exact-four-worker gate: 1376 passed in 1.77s
non-Docker/non-Internet exact-four-worker branch coverage: 1460 passed in 8.26s, 96.17%
fixture regeneration check: checked-in bytes match deterministic production output
post-gate state: approximately 14 GiB available, zero swap use, no pytest process, only init.scope
```

The final whole-phase review later found three Important gaps and the carried publication-exception Minor. The single
final fix wave in `8c78098` corrected both-artifact fixture checking, the acknowledgement-before-parent cleanup race,
binary-resolution endpoint quantization/window validation, and expected-versus-unexpected publication exceptions.
Its retained evidence is 424 focused tests, 1,384 fast tests, and 1,481 non-Docker tests at 96.23% branch coverage.

The scoped final re-review approved all three Important fixes and introduced no new Critical or Important breakage.
It left one theoretical edge on the original Minor: a non-`OSError` programming exception raised by `Path.unlink`
could replace another unexpected primary exception. This is parked as non-load-bearing because the real filesystem
boundary reports unlink failures as `OSError`; all production cleanup failures preserve the primary exception, and an
unexpected defect in the cleanup operation itself remains intentionally visible.
