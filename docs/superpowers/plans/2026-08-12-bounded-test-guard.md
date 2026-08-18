# Bounded Test Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every documented pytest command use one reusable process-tree
guard and prove that its wall-clock and hard-memory limits leave no descendant
running.

**Architecture:** Add one small Bash boundary around a transient systemd user
scope. The boundary applies cgroup memory/swap limits, runs GNU `timeout`, and
performs a final scope-wide `SIGKILL` plus inactive-state verification so even a
descendant that created a new process group cannot escape. A deterministic
Python fixture creates parent/child/grandchild trees for low-budget wall-clock
and memory probes; no production Python module or test-runner framework is
added.

**Tech Stack:** Bash, systemd user scopes, cgroup v2 resource controls, GNU
coreutils `timeout`, CPython 3.12, pytest, uv, Ruff, Pyright

## Global Constraints

- Keep the project a one-process research prototype; this is development
  tooling, not a runtime service.
- Use `scripts/run_bounded.sh` for every pytest command documented in
  `architecture/DEVELOPMENT.md` and `architecture/TESTING.md`.
- Keep broad deterministic pytest at exactly four workers and focused or
  external-resource pytest serial at `-n 0`; never use `-n auto`.
- The guard must bound the entire process tree by resident memory, swap, and
  wall time, including descendants in separate process groups.
- `MemoryHigh` must remain below `MemoryMax`; `OOMPolicy=kill` and a scope-wide
  final kill are reliability behavior, not security behavior.
- A timed-out, cancelled, or OOM-killed command is not complete until its scope
  is inactive and every controlled descendant PID is gone.
- Controlled memory proof must use a small nested cgroup limit and must not put
  material pressure on the 15 GiB WSL host.
- Use uv only; add no dependency, task-runner package, NodeJS file, or CI
  abstraction.
- Follow TDD, keep lines at most 120 characters, and use `apply_patch` for
  hand-authored edits.

---

### Task 1: Process-tree guard, containment probes, and Roadmap evidence

**Files:**
- Create: `scripts/run_bounded.sh`
- Create: `fixtures/tests/process_guard/process_guard_tree.py`
- Create: `tests/integration/test_process_guard.py`
- Modify: `architecture/DEVELOPMENT.md`
- Modify: `architecture/TESTING.md`
- Modify: `architecture/ROADMAP.md`

**Interfaces:**
- Produces:
  `scripts/run_bounded.sh --memory-high VALUE --memory-max VALUE
  --swap-max VALUE --wall-time DURATION --kill-after DURATION
  [--unit NAME] -- COMMAND [ARG ...]`.
- Exit behavior: preserve the guarded command's nonzero status; use `125` when
  guard setup or final containment verification fails; never turn a timed-out,
  OOM-killed, or leaking command into success.
- The default unit name is unique and begins `trafficlab-test-guard-`; `--unit`
  exists so a controlled test can verify that one exact scope is inactive.
- The fixture writes `parent.pid`, `child.pid`, and `grandchild.pid` before it
  blocks or allocates. Children use separate sessions and ignore `SIGTERM` in
  wall-clock mode so only full process-tree containment can pass.

- [ ] **Step 1: Write the failing guard contract tests**

Create `tests/integration/test_process_guard.py`, marked `integration`, with
four direct behavioral tests:

1. invalid/missing limits, missing command, unavailable user manager, and an
   invalid `MemoryHigh >= MemoryMax` fail with status `125` and a corrective
   diagnostic;
2. an ordinary child command returns its exact status and leaves the named
   scope inactive;
3. a parent/child/grandchild tree whose descendants create new sessions and
   ignore `SIGTERM` is stopped by a `300ms` wall timeout plus `200ms` grace;
4. the same three-level tree allocating touched 4 MiB chunks is killed within a
   nested `MemoryHigh=63M`, `MemoryMax=64M`, `MemorySwapMax=0` scope. The narrow
   throttle band is intentional: this WSL kernel can otherwise remain in
   `mem_cgroup_handle_over_high` without reaching the hard limit during a short,
   low-pressure proof.

For both containment tests, wait for all three PID files, require the guard to
return nonzero within ten seconds, poll `/proc/PID` until every PID disappears,
and require `systemctl --user is-active UNIT.scope` to report inactive. The test
must fail actionably when systemd user scopes or cgroup memory control are not
available; do not silently replace the real proof with mocks.

- [ ] **Step 2: Confirm the guarded RED state**

Run the new test inside the existing raw architecture guard because the script
does not exist yet:

```bash
systemd-run --user --scope --collect \
  -p MemoryHigh=2G -p MemoryMax=3G -p MemorySwapMax=512M -p OOMPolicy=kill \
  timeout --kill-after=10s 5m \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

Expected: collection or test setup fails because `scripts/run_bounded.sh` and
the controlled fixture do not exist. After the command, verify the outer scope
is inactive and no `pytest` or `py.test` process remains.

- [ ] **Step 3: Implement the minimal Bash guard**

Use `set -eu`; parse only the six named options above and `--`; reject empty or
malformed values before launching a scope. Accept memory values as nonnegative
integers with an optional `K`, `M`, or `G` binary suffix; accept positive
durations with `ms`, `s`, or `m`; normalize the two memory thresholds to bytes
and require `MemoryHigh < MemoryMax`. Verify `systemd-run`, `systemctl`, and
`timeout` exist and that `systemctl --user is-system-running` is usable.

Launch exactly:

```bash
systemd-run --user --quiet --scope --collect --unit="$unit" \
  -p "MemoryHigh=$memory_high" \
  -p "MemoryMax=$memory_max" \
  -p "MemorySwapMax=$swap_max" \
  -p OOMPolicy=kill \
  timeout --kill-after="$kill_after" "$wall_time" "${command[@]}"
```

An EXIT/INT/TERM/HUP cleanup function checks `UNIT.scope`; while it is active,
send `systemctl --user kill --kill-whom=all --signal=KILL`, then poll for at
most five seconds. Emit the exact unit in every cleanup/setup error. If the
guarded command returned success but descendants kept the scope active, kill
them and return `125`. Preserve `124` or another nonzero command status after a
successful final cleanup.

- [ ] **Step 4: Implement the controlled process-tree fixture**

The fixture accepts `wall|memory`, a PID directory, and an internal role. The
parent starts a child with `start_new_session=True`; the child starts a
grandchild the same way. Each role atomically writes its own PID file. In wall
mode every role ignores `SIGTERM` and blocks. In memory mode every role retains
touched 4 MiB `bytearray` chunks until the nested cgroup OOM-kills the scope.
No role daemonizes, changes user, or writes outside the supplied temporary
directory.

- [ ] **Step 5: Run focused GREEN and defect-function coverage**

Use the new guard around pytest itself:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

Require all tests to pass, the outer unit to become inactive, no pytest
descendant to remain, and host swap use to stay negligible. Repeat only focused
nodes when a failure exposes a guard function; the affected Bash branch must
have a direct behavioral regression even though shell coverage is not added.

- [ ] **Step 6: Replace raw documented invocations with the guard**

Update `architecture/DEVELOPMENT.md` and `architecture/TESTING.md` so fast,
coverage, Docker, Internet, pinpointed-node, and `--lf` examples call the
script. Preserve the exact existing memory, swap, worker, and wall-clock values.
Explain exit `125`, unique units, final scope kill/verification, and the
controlled proof command. Keep the equivalent-CI allowance but state that it
must provide its own corresponding process-tree proof.

- [ ] **Step 7: Run the complete Phase 1 evidence**

Run sequentially with no overlap:

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_config_validation.py::test_operator_defaults
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
uv run --locked trafficlab preflight examples/configs/minimal.toml --config-only
git diff --check
```

After every guarded command, require its scope inactive and no pytest process.
Check the two Phase 1 test boxes only after this fresh evidence passes. Do not
change Phase 3's current marker or its pending Docker/Internet boxes.

- [ ] **Step 8: Commit and self-review**

Review exact exit preservation, signal cleanup, scope-name diagnostics,
separate-process-group containment, low memory pressure, and documentation
values. Commit:

```bash
git add scripts/run_bounded.sh fixtures/tests/process_guard/process_guard_tree.py \
  tests/integration/test_process_guard.py architecture/DEVELOPMENT.md \
  architecture/TESTING.md architecture/ROADMAP.md
git commit -m "test(tooling): enforce bounded process trees"
```

Write the SDD report with RED/GREEN output, exact transient unit names, PID
disappearance proof, memory/swap observations, complete gate results, commit
hash, and self-review findings.

---

## Completion Gate

The task is complete only when the guard is independently reviewed with no
Critical or Important findings, the two controlled child-tree tests pass on
this WSL systemd host, the full Phase 1 tooling gate passes through the new
guard, both reopened Roadmap boxes are accurately checked, and the working tree
is clean.
