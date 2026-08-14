# Task 1 report — bounded test guard

## Scope

- Base: `151df722ce2982e99432afb5f064d93cd5f5923b`
- Worktree: `/home/bsa/projects/trafficlab/.worktrees/full-implementation`
- Added one generic Bash guard, one real three-role process fixture, and four integration tests.
- Replaced every pytest example in `architecture/DEVELOPMENT.md` and `architecture/TESTING.md`; ordinary 6G/8G/1G
  and 2G/3G/512M profiles and worker counts are unchanged.

## TDD and containment evidence

- RED outer unit: `run-rb3146b996f564acabfd34c3e82165e41.scope`.
- RED result: 4 failed in 0.07s because `scripts/run_bounded.sh` and the process-tree fixture did not exist.
- RED cleanup: outer scope `inactive` (status 4), no pytest/py.test process, ~14 GiB available, 0 B swap used.
- Wall proof: parent/child/grandchild start separate sessions and ignore `SIGTERM`; nested 300ms deadline plus 200ms
  grace returned a nonzero timeout status, all PID files existed, every `/proc/PID` disappeared, and the exact scope
  became inactive.
- Explicit memory unit: `trafficlab-test-guard-memory-evidence-63.scope`.
- Memory result: status 137, distinct from wall timeout 124; parent 52138, child 52139, and grandchild 52140 all
  disappeared; scope inactive; ~14 GiB available and 0 B host swap used.
- Focused GREEN: `trafficlab-test-guard-focused-green.scope`, 4 passed in 0.86s, then inactive with no pytest.
- Final self-review GREEN: `trafficlab-test-guard-self-review-green.scope`, 4 passed in 0.96s, then inactive with no
  pytest and 0 B swap used.

## WSL MemoryHigh diagnosis

The initial exact 48M/64M/0 nested probe did not reach the hard limit. Live cgroup evidence showed 56,856,576 bytes,
`high 598`, `max 0`, `oom 0`, and `oom_kill 0`; all roles waited in `mem_cgroup_handle_over_high`. Removing sleeps,
using a C-level slice, and using native `memset` threads all returned wall status 124. Exact scopes and controlled PIDs
were cleaned after every attempt; host memory stayed ~14 GiB available with 0 B swap use. The Class 4 resolution moved
only the controlled proof to MemoryHigh=63M, MemoryMax=64M, MemorySwapMax=0. This retains High < Max and the safe hard
cap while minimizing this WSL kernel's throttle band. The authorized plan records that correction.

## Complete Phase 1 gate

- `uv sync --locked --all-groups`: resolved 20, checked 19 packages.
- `uv lock --check`: resolved 20 packages.
- Ruff format: 105 files already formatted; Ruff lint: all checks passed.
- Pyright: 0 errors, 0 warnings, 0 information messages.
- Broad unit scope `trafficlab-test-guard-phase1-broad.scope`: exactly four workers; 1,018 passed in 1.67s.
- Pinpoint scope `trafficlab-test-guard-phase1-pinpoint.scope`: serial; 1 passed in 0.02s.
- Containment scope `trafficlab-test-guard-phase1-containment.scope`: serial; 4 passed in 0.83s.
- Config-only preflight prepared `runs/minimal`; `git diff --check` passed.
- After every guarded command the exact outer scope reported inactive and no pytest process remained. Host memory was
  ~14 GiB available and swap use remained 0 B.
- Only the two reopened Phase 1 test boxes were checked. Phase 3 remains Current and all pending Docker/Internet boxes
  remain unchecked.

## Self-review

- Exact exit: status 37 is preserved; timeout/OOM nonzero remains nonzero; successful command with a detached child
  becomes 125 after exact-scope kill.
- Setup: missing/malformed values, High >= Max, integer overflow, missing commands, invalid unit names, and unavailable
  user manager return 125 with the exact unit in diagnostics.
- Cleanup: EXIT/INT/TERM/HUP converge on exact scope-wide SIGKILL plus a five-second inactive poll; the collected-unit
  disappearance race is rechecked rather than corrupting a completed child's status.
- Process tree: child and grandchild use separate sessions; all three PID files precede blocking/allocation; no role
  daemonizes or writes outside its supplied directory.
- Independent review: approved after two fix rounds. Critical findings: 0. Important findings: 0.
- Reviewer line evidence: scope ownership is token-bound at `scripts/run_bounded.sh:258-300`; activation cleanup stops
  and reaps the launcher before bounded token resolution at `scripts/run_bounded.sh:175-230`; internal child atomic
  acknowledgement precedes command exec at `scripts/run_bounded.sh:5-17`; race regressions cover concurrent creator,
  activation-time signal, and collected fast status at `tests/integration/test_process_guard.py:221`, `:418`, and
  `:470`. The reviewer approved the complete range through `4010fe7` with no remaining Critical or Important finding.

## Commits

- Implementation: `0f2ffc450ffcc4ba036a1a0f0f68c06427fa51dd` (`test(tooling): enforce bounded process trees`).
- Evidence-only plan correction: `1fe071baf26f0f0f0ea1dadbef35fe4088414539`
  (`docs(testing): record WSL memory proof limit`).

## Independent review fix round 1

The reviewer found deferred Bash signal traps around foreground `systemd-run`, unsafe ownership if `--unit` collided,
raw setup-failure status, and an OOM assertion that admitted containment failure 125.

- RED outer unit `trafficlab-test-guard-review-red.scope`: 4 failed in 3.31s. Controlled launch status 23 escaped;
  TERM did not exit the wrapper within two seconds; memory cleanup returned 125 while the unit was `deactivating`;
  and an already-loaded named unit returned raw status 1. Tests exact-cleaned all resources; no pytest remained.
- Fix: precheck exact unit `LoadState=not-found`; launch `systemd-run` asynchronously; mark ownership only after the
  requested scope is observed active; make signal traps interrupt the launcher wait; exact-kill only an owned scope;
  poll `deactivating` through inactive; reap the launcher; and translate an unactivated nonzero launch to status 125.
- Ownership proof: an existing scoped child remains alive and its scope active after the second guard returns 125;
  only the test's exact cleanup removes it.
- Signal proof: TERM returns wrapper status 143 within two seconds. Controlled PIDs parent 56179, child 56180, and
  grandchild 56181 all disappeared and the exact named scope became inactive.
- OOM proof now requires status 137 exactly, so setup/final-verification status 125 and wall status 124 cannot satisfy
  the hard-memory test.
- Focused GREEN `trafficlab-test-guard-review-green1.scope`: 4 passed in 1.27s; outer and controlled scopes inactive,
  no pytest process, ~14 GiB available, and 0 B swap used.
- Fresh complete Phase 1 gate: sync/lock passed; Ruff format checked 105 files; Ruff lint passed; Pyright reported 0;
  `trafficlab-test-guard-fix1-broad.scope` ran exactly four workers with 1,018 passed in 1.52s;
  `trafficlab-test-guard-fix1-pinpoint.scope` ran serially with 1 passed in 0.02s;
  `trafficlab-test-guard-fix1-containment.scope` ran serially with 4 passed in 1.31s; config-only preflight and diff
  check passed. Every scope was inactive afterward, no pytest remained, and host swap use was 0 B.
- Review-fix commit: `6fcd331acd72d838e994a9c02d396aef04e77889`
  (`fix(tooling): own bounded scopes safely`).

## Independent review fix round 2

The rereviewer identified activation-time signal, same-name concurrent creator, and fast-command collection races in
the name/state ownership handshake.

- Deterministic RED `trafficlab-test-guard-race-red2.scope`: 3 failed in 3.08s. A real unrelated same-name scope won
  between precheck and launch; activation-time TERM did not exit within two seconds; and a real fast status 42 became
  setup status 125 when active-state polling missed the collected scope. Exact cleanup left no scope or pytest.
- Fix: create a mode-700 private `mktemp` directory and random invocation token; store the token in the transient
  unit's Description; invoke the same script in internal `--scope-child` mode to atomically acknowledge that exact
  token before `exec` of timeout and the original argv. Ownership requires the exact acknowledgement or exact unit
  Description token. Name and ActiveState alone never establish ownership.
- Activation cleanup stops and reaps the launcher, polls boundedly for that invocation's acknowledgement or token,
  and kills the exact scope only when token-bound ownership is proven. A concurrent unrelated scope has neither and
  survives guard status 125 until the test performs its exact cleanup.
- The persistent acknowledgement distinguishes a fast guarded status 42 from launch failure even after `--collect`;
  the private directory is removed only after ownership and launcher resolution.
- Race GREEN: 3 passed in 3.79s. Complete focused GREEN
  `trafficlab-test-guard-round2-focused2.scope`: 7 passed in 8.52s; all scopes inactive, no pytest, 0 B swap used.
- Fresh complete Phase 1 gate: sync/lock passed; Ruff checked 105 files; lint passed; Pyright reported 0;
  `trafficlab-test-guard-round2-broad.scope` ran exactly four workers with 1,018 passed in 1.73s;
  `trafficlab-test-guard-round2-pinpoint.scope` ran serially with 1 passed in 0.02s;
  `trafficlab-test-guard-round2-containment.scope` ran serially with 7 passed in 8.49s; config-only preflight and diff
  check passed. Every scope was inactive afterward, no pytest remained, and host swap use was 0 B.
- Round 2 commit: `4010fe72fc6d1b01e9e54da11242db79fb6ddcc8`
  (`fix(tooling): bind guard scope ownership`).
