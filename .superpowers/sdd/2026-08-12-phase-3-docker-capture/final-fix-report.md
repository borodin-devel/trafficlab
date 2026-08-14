# Phase 3 whole-phase final fix report

Date: 2026-08-12

Base: `06cfdef`

Implementation: `52d972a5214de5beb3ab8fb3571fdb78981747ae`

## Classification and design

All ten review findings were confirmed as Class 3. The approved architecture
resolved every design choice; no Class 5 decision or architecture change was
needed. The minimal implementation boundaries are:

1. Pass `min(stage_deadline, total_deadline)` to every active workload and
   flush command, then classify an overrun from one post-command clock reading.
2. Make a clean capture exit the sole validation/publication eligibility fact.
3. Record one observation in fixed priority order, retaining every other
   visible event or status as typed secondary evidence.
4. Arbitrate readiness once with interruption first and never start target
   after a visible interruption.
5. Convert `KeyboardInterrupt` inside the owned lifecycle to the same kill,
   induced-status, bounded-flush, diagnostic, and cleanup transition.
6. Preserve natural target status and use exit 130 for user interruption.
7. Check the total deadline before and after every packet in aggregation.
8. Carry containers, labelled networks, and labelled volumes in typed project
   inventory and in cleanup verification; retain the known default network as
   soon as project creation may have started.
9. Require a DNS hostname in the configured probe URL; reject IP literals.
10. Translate temporary-directory creation and teardown errors, preserve an
    earlier primary, and roll back an owned reusable pair after teardown error.

The self-review found and fixed one final edge before replacement gates: a
target poll that expired before the capture poll hid the last-known running
capture and skipped the required flush. `_observe_workload` now returns fresh
capture evidence when available and otherwise the last-known live evidence.

## TDD evidence

Every pytest command ran serially under the required 2/3 GiB, 512 MiB swap,
five-minute systemd scope for RED/GREEN work. Each reported scope ended
`inactive/dead`, and the exact `pytest`/`py.test` process scan was empty.

| Finding | RED evidence and reason | GREEN evidence |
| --- | --- | --- |
| 1 deadlines | `run-r3fc`: workload received `160.0`, expected stage `105.0`. `trafficlab-red-remaining-27218`: signal/state stage expiry was `flush_failed`, not `stage_timeout`. `trafficlab-red-preexpired-flush-26870`: expired flush made no `kill_capture`. | `run-rb64` 1 passed; `trafficlab-green-boundary-10676` 22 passed; `trafficlab-green-flush-15846` 22 passed. Command-overrun paths: `trafficlab-green-command-overrun-4920` 4 passed. |
| 2 close eligibility | Grouped diagnostic RED ended without the expected timeout diagnostic; failed-close regression would otherwise call publication. | `run-r7466` 4 passed; focused compatibility retained timeout/interruption diagnostics only after clean close and rejected failed close. |
| 3 all visible evidence | Grouped arbitration RED lost lower-priority capture/deadline/status evidence after selecting the primary. | `run-r0d0` arbitration slice passed; final fixed-order regression passed in the 478-test focused scope. |
| 4 readiness priority | `run-r0d0`: readiness interruption returned default status 2 after readiness won, instead of interruption 130; target-start risk remained. | `trafficlab-green-boundary-10676`: interruption won ready/stopped observations, target never started, and live capture flushed. |
| 5 SIGINT lifecycle | `run-r669`: injected `KeyboardInterrupt` escaped and aborted pytest with no completed test. Controlled installed-CLI RED `run-r258` lacked `kill_target`. | Injected GREEN `run-r3cba` 1 passed. Real subprocess SIGINT GREEN `run-r0a73` 1 passed in 0.26 s with exit 130 and kill < flush < cleanup. |
| 6 exact statuses | Pre-fix outcome construction discarded primary status/kind, so the CLI defaulted natural 23 and interruption to 2. | Natural 23 and interruption 130 passed in `run-r4b75` and the final focused scope; structured `primary_status` and typed secondary failures are asserted in `run.log`. Docker status input is constrained to 0..255. |
| 7 aggregation deadline | `run-r686`: `DID NOT RAISE DeadlineExceededError` during the second packet aggregation pass. | `run-rd563` 2 passed; replacement coverage reports `capture_validation.py` 100%. |
| 8 complete inventory | `run-re861`: `ProjectInventory` rejected the new `networks` field, proving the type was container-only. Later RED showed successful down with a remaining network/volume was accepted. | `run-r48ec` 7 passed; `trafficlab-green-boundary-10676` covered last-known project network and preflight partial-create inventory; final focused scope covers fresh container/network/volume verification. |
| 9 DNS probe | `run-r307`: IP-literal regression `DID NOT RAISE ValidationError`. | `run-rbbd` 14 passed; `config.py` is 100% covered in the replacement coverage gate. |
| 10 temp directory | `run-r898`: raw `OSError` escaped creation. `trafficlab-red-temp-rollback-9490`: teardown failure left `capture.json` published. | `run-rea476` 1 passed; `trafficlab-green-temp-rollback-16168` 3 passed with translated creation/teardown and post-teardown rollback. |

Late self-review regression: `trafficlab-red-last-known-flush-14864` failed
`assert capture is live_capture` because the result was `None`.
`trafficlab-green-last-known-flush-10499` then passed 5/5, and the final focused
compatibility scope `trafficlab-final-focused-22040` passed 478 tests in 3.12 s.

Several bounded real-SIGINT fixture-debug runs failed before the final proof;
they changed only the test fake's production-project selection and were never
treated as implementation GREEN evidence.

## Static and final gates

- `uv sync --locked --all-groups`: passed.
- `uv lock --check`: passed.
- `ruff format .`, `ruff format --check .`, `ruff check .`: passed.
- strict `pyright`: 0 errors, 0 warnings.
- `git diff --check`: passed.
- Pre-gate host evidence: 14 GiB available, swap usage 0 B, only
  `init.scope`, no exact pytest process.
- Superseded first broad scope `trafficlab-phase3-final-broad.scope`: 1011
  passed in 1.76 s, inactive/dead. Superseded because self-review then changed
  production code.
- Superseded first coverage scope `trafficlab-phase3-final-coverage.scope`:
  1051 passed, 96.04%, inactive/dead.
- Replacement final broad scope
  `trafficlab-phase3-final-broad-replacement.scope`: exactly
  `-n 4 --dist worksteal`, 1012 passed in 1.36 s, inactive/dead,
  `Result=success`, no descendants.
- Replacement final non-Docker coverage scope
  `trafficlab-phase3-final-coverage-replacement.scope`: exactly
  `-n 4 --dist worksteal`, 1052 passed in 3.19 s, 96.13% branch coverage,
  inactive/dead, `Result=success`, no descendants.
- Defect-focused coverage: capture policy, validation, and config modules are
  100%; `_flush_capture` has no missing executable line or branch in the final
  report; Docker CLI is 99%, cleanup 98%, and capture orchestration 95%.

## Docker limitation

The host has no `docker` executable. No Docker or Internet tests were run and
no evidence was fabricated. Phase 3 remains Current; all Docker and Internet
Roadmap boxes remain unchecked. The installed fake-Docker subprocess SIGINT
integration is local, bounded, and does not claim real Docker evidence.

## Files and self-review

Implementation commit `52d972a` changes capture orchestration/policy,
validation, cleanup, configuration, Docker inventory, preflight inventory, and
their unit/in-process integration regressions. No dependency, runtime service,
security feature, Node application, or production service was added.

Final self-review checked deadline/status flow, fresh versus last-known
inventory, clean-close publication ownership, ordered primary/secondary
evidence, interruption cleanup, direct Docker argv, and the final diff. No
Critical or Important issue remains in the locally testable scope. The only
open concern is the explicit environmental Docker/Internet evidence gate.

## Residual final remediation

Residual implementation commit: `c407c1ab5195b6fb39dad5c37fdc32e4ce391591`.

Two additional Class 3 findings were confirmed after the first final wave:

1. `_record_observation` passed natural status zero through a no-op while no
   primary existed. A simultaneous lower-priority failure then became primary,
   but the already-visible successful status could no longer be recovered.
   Status zero is now deferred only until the first visible lower-priority
   failure becomes primary, then appended exactly once as typed
   `NATURAL_TARGET_STATUS` before still-lower evidence. A lone natural zero
   remains success, and natural nonzero precedence is unchanged.
2. Target ownership was recorded only after `start_target` returned. A SIGINT
   inside that Docker call could leave a partially started target while the
   interruption transition flushed capture first. `target_may_exist` is now
   set immediately before the call and is consumed only by interruption
   handling. Target kill is therefore the next Docker action, followed by
   induced-status inspection and clean bounded flush. Ordinary
   `TrafficlabError` from target start retains its existing validation primary
   and does not request target kill.

Guarded RED scope `trafficlab-phase3-residual-red.scope` collected three tests
and failed all three for the intended reasons:

- target-zero plus capture-stop produced `secondary_failures=[]`;
- target-zero plus stage/total expiry retained only total timeout, omitting the
  typed status zero;
- partial-start SIGINT ordered `signal_capture, state_capture, start_down,
  inventory` instead of `kill_target, state_target, signal_capture,
  state_capture`.

The first GREEN attempt `trafficlab-phase3-residual-green.scope` stopped after
one failure because the pre-existing exact error-string assertion did not yet
include the newly retained secondary evidence. No production change followed
from that assertion failure. `trafficlab-phase3-residual-green2.scope` then
passed 4/4, covering both status races, partial-start ordering, exit 130,
diagnostic publication/cleanup, and unchanged ordinary start-error precedence.

Focused branch evidence:

- `trafficlab-phase3-residual-function-cov.scope`: 72 passed, capture module
  96.15%. Its `term-missing` report has no missing executable line or partial
  branch in `_record_observation` (lines 145–173) or `_interrupt_lifecycle`
  (lines 469–509), giving both defective flow functions 100% line/branch
  coverage. Remaining capture-module misses are pre-existing readiness and
  diagnostic-publication error paths.
- `trafficlab-phase3-residual-compat.scope`: 169 passed in 1.70 seconds,
  including the existing installed fake-Docker SIGINT integration. A second
  real-SIGINT fixture was unnecessary because the residual boundary is the
  synchronously injected `start_target` call.

Fresh static and final evidence after the residual production change:

- locked sync and lock check passed;
- Ruff format/check/lint passed; strict Pyright reported zero errors and
  warnings; `git diff --check` passed;
- pre-gate memory was 14 GiB available with zero swap usage, only
  `init.scope`, and no exact pytest process;
- `trafficlab-phase3-residual-final-broad.scope`: exact four-worker worksteal
  selection, 1018 passed in 1.44 seconds, inactive/dead, `Result=success`, no
  pytest descendants;
- `trafficlab-phase3-residual-final-coverage.scope`: exact four-worker
  non-Docker/non-Internet branch coverage, 1058 passed in 2.93 seconds,
  96.26%, inactive/dead, `Result=success`, no pytest descendants.

Every residual pytest scope used the required systemd process-tree guard. The
Docker executable remains absent, so no Docker or Internet evidence was run or
claimed; Phase 3 and its external Roadmap boxes remain unchanged.
