# SDD ledger — plan: docs/superpowers/plans/2026-08-12-bounded-test-guard.md

- Task: Task 1 — process-tree guard, containment probes, and Roadmap evidence
- Base: `151df722ce2982e99432afb5f064d93cd5f5923b`
- Worktree: `/home/bsa/projects/trafficlab/.worktrees/full-implementation`
- 2026-08-12: Initialized before implementation; reading authoritative plan and existing test documentation.
- 2026-08-12 RED: raw outer unit `run-rb3146b996f564acabfd34c3e82165e41.scope`; 4 failed in 0.07s because
  `scripts/run_bounded.sh` and `tests/fixtures/process_guard_tree.py` were absent. Outer scope subsequently reported
  `inactive` (status 4), no pytest process remained, host memory was ~14 GiB available, and swap use was 0 B.
- 2026-08-12 memory diagnosis: the exact 48M/64M/0 probe reached only 56,856,576 bytes after about five seconds;
  `memory.events` reported `high 598`, `max 0`, `oom 0`, and `oom_kill 0`, with all three roles blocked in
  `mem_cgroup_handle_over_high`. Removing the allocator sleep, replacing Python page touches with a C-level slice,
  and eight native `memset` threads per role all timed out with status 124. Each attempt's exact scope was killed and
  verified inactive, every controlled PID disappeared, no pytest process remained, host memory stayed ~14 GiB
  available, and swap use stayed 0 B.
- 2026-08-12 Class 4 resolution: changed only the controlled nested proof to MemoryHigh=63M, MemoryMax=64M, and
  MemorySwapMax=0. This keeps `MemoryHigh < MemoryMax` and the safe 64 MiB cap while minimizing the diagnosed WSL
  throttle band. Ordinary documented profiles remain unchanged.
- 2026-08-12 GREEN: `trafficlab-test-guard-memory-evidence-63.scope` returned 137 (not timeout 124); controlled PIDs
  parent 52138, child 52139, and grandchild 52140 disappeared and the scope reported inactive. Host memory stayed
  ~14 GiB available and swap use stayed 0 B. The complete focused file passed 4 tests in 0.86s.
- 2026-08-12 Phase 1 gate: sync resolved 20 and checked 19 packages; lock check resolved 20; Ruff format checked 105
  files; Ruff lint passed; Pyright reported 0 errors; `trafficlab-test-guard-phase1-broad.scope` ran exactly four
  workers with 1,018 passed in 1.67s; `trafficlab-test-guard-phase1-pinpoint.scope` ran serially with 1 passed in
  0.02s; `trafficlab-test-guard-phase1-containment.scope` ran serially with 4 passed in 0.83s; config-only preflight
  prepared `runs/minimal`; `git diff --check` passed. Every named scope was inactive afterward, no pytest process
  remained, host memory was ~14 GiB available, and swap use was 0 B.
- 2026-08-12 self-review: added direct behavior for a successful command that leaks a detached child (guard returns
  125 after exact-scope kill) and accepted the observed systemd scope timeout statuses 124/137. Focused result was
  4 passed in 0.96s; scope inactive, no pytest remained, and swap use was 0 B. No Critical or Important self-review
  finding remains; independent review is pending from the parent coordinator.
- 2026-08-12 commits: implementation `0f2ffc450ffcc4ba036a1a0f0f68c06427fa51dd`; evidence-only plan correction
  `1fe071baf26f0f0f0ea1dadbef35fe4088414539`. Tracked working tree clean.
- 2026-08-12 independent review round 1 RED: `trafficlab-test-guard-review-red.scope` ran four focused tests and all
  four failed in 3.31s. A controlled `systemd-run` launch status 23 escaped instead of 125; TERM did not let the
  wrapper exit within two seconds; the memory proof exposed cleanup status 125 instead of OOM 137 while its scope
  was `deactivating`; and the corrected collision probe returned raw status 1 for an already-loaded unit. Every exact
  scope was cleaned, no pytest process remained, host memory was ~14 GiB available, and swap use was 0 B.
- 2026-08-12 review fix GREEN: the guard now rejects a preloaded unit before launch, launches asynchronously, marks
  only an observed active scope owned, lets INT/TERM/HUP interrupt its wait, exact-kills only the owned scope, reaps
  the launcher, translates unactivated nonzero launch failure to 125, and treats `deactivating` as a live cleanup
  state. Focused `trafficlab-test-guard-review-green1.scope`: 4 passed in 1.27s. TERM evidence PIDs were parent 56179,
  child 56180, and grandchild 56181; wrapper status 143 within two seconds; all PIDs gone and scope inactive.
- 2026-08-12 review fix Phase 1 gate: sync/lock passed; Ruff checked 105 files and lint passed; Pyright reported 0
  errors; `trafficlab-test-guard-fix1-broad.scope` ran exactly four workers with 1,018 passed in 1.52s;
  `trafficlab-test-guard-fix1-pinpoint.scope` ran serially with 1 passed in 0.02s; and
  `trafficlab-test-guard-fix1-containment.scope` ran serially with 4 passed in 1.31s. Preflight and diff check passed;
  all scopes inactive, no pytest process, ~14 GiB available, and 0 B swap used.
- 2026-08-12 review-fix commit: `6fcd331acd72d838e994a9c02d396aef04e77889`. Tracked working tree clean;
  independent rereview pending.
- 2026-08-12 independent review round 2 RED: deterministic real-scope shims reproduced all three races under
  `trafficlab-test-guard-race-red2.scope`: concurrent creator returned raw 1 and could be claimed by name/state;
  activation-time TERM did not exit within two seconds; and a fast command's status 42 became 125 after collection.
  Three failed in 3.08s. Exact resources were cleaned; no pytest remained; host memory was ~14 GiB available and swap
  use was 0 B.
- 2026-08-12 round 2 GREEN: private `mktemp` ownership directory, random Description token, and an internal
  `--scope-child` atomic acknowledgement bind ownership to this invocation. Cleanup stops/reaps the launcher, then
  kills only an acknowledged or token-matching exact scope. The three race nodes passed in 3.79s; the complete seven
  guard tests passed in 8.52s under `trafficlab-test-guard-round2-focused2.scope`.
- 2026-08-12 round 2 Phase 1 gate: sync/lock passed; Ruff format checked 105 files; Ruff lint passed; Pyright 0 errors;
  `trafficlab-test-guard-round2-broad.scope` ran exactly four workers with 1,018 passed in 1.73s;
  `trafficlab-test-guard-round2-pinpoint.scope` ran serially with 1 passed in 0.02s; and
  `trafficlab-test-guard-round2-containment.scope` ran serially with 7 passed in 8.49s. Preflight and diff check
  passed; all scopes inactive, no pytest process, ~14 GiB available, and 0 B swap used.
- 2026-08-12 round 2 commit: `4010fe72fc6d1b01e9e54da11242db79fb6ddcc8`. Tracked working tree clean;
  independent rereview pending.
- Task 1: fix round 1/5 — `6fcd331acd72d838e994a9c02d396aef04e77889`; prompt signal cleanup, safe
  pre-existing-unit rejection, launch failure 125, and exact OOM status 137 verified.
- Task 1: fix round 2/5 — `4010fe72fc6d1b01e9e54da11242db79fb6ddcc8`; token/ack ownership closes
  activation-signal, concurrent-creator, and fast-collection races.
- Task 1: complete (commits 151df72..4010fe7, review clean).
