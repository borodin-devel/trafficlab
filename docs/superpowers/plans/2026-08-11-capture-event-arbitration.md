# Capture Event Arbitration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make capture races, in-process deadlines, signalling, and zero-budget
cleanup deterministic and fully testable.

**Architecture:** One event arbiter collects all terminal events visible in an
observation and applies a fixed priority before another action. The same
monotonic deadline reaches PCAPNG parsing and validation, while cleanup uses
last known inventory and performs no blocking work after budget expiry.

**Tech Stack:** Markdown architecture documents, monotonic deadlines, Docker
Compose container state, PCAPNG frame iteration, pytest unit/integration design,
uv

## Global Constraints

- Use one event arbiter at every capture wait boundary.
- Read the monotonic clock once and collect every ready event in one observation.
- Apply this fixed priority: user interruption, natural target stop, unexpected
  capture stop, stage-specific timeout, total-run timeout.
- Process the selected event before another wait or orchestration action.
- Never replace an existing primary failure with a later failure.
- Make target kill the next action after unexpected capture exit and require
  target to stop within five seconds, independently of workload timeout.
- Send capture `SIGINT` and wait for flush only while capture is alive.
- Pass the monotonic total-run deadline to PCAPNG parsing and validation; check
  before starting and after every frame.
- Keep the last known project resource inventory during the lifecycle.
- With zero cleanup budget, launch no Docker command; record cleanup timeout and
  last known inventory.
- On cleanup expiry, terminate the local Compose CLI and issue no later Docker
  query; report inventory as possibly remaining.
- Add no setting, process manager, thread, helper service, artifact field,
  security feature, or production container.
- Keep exactly two production services, three traffic models, four similarity
  methods, one genetic strategy, and seven roadmap phases.
- Keep edited lines at no more than 120 characters.

---

### Task 1: Define one authoritative event loop

**Files:**
- Modify: `architecture/CAPTURE.md:64-184`

**Interfaces:**
- Consumes: target/capture state events, interruption, stage timers, monotonic
  total deadline, target status, capture liveness, resource inventory
- Produces: one selected terminal event, one immutable primary failure,
  secondary diagnostics, bounded parsing, and bounded cleanup

- [ ] **Step 1: Replace ad hoc race wording with the event arbiter**

State that each wait observation samples the monotonic clock once, collects all
ready events, and applies this exact priority:

```text
1. user interruption
2. natural target stop
3. unexpected capture stop
4. stage-specific timeout
5. total-run timeout
```

The selected event is processed before another wait. Natural target stop wins a
same-observation race with capture or deadline; a stage timeout wins a
same-observation race with total timeout.

- [ ] **Step 2: Make capture-failure response immediate and conditional**

Require target kill as the next orchestration action after unexpected capture
exit. Reject capture output directly, without `SIGINT` or flush wait, because
capture is already stopped. On other failure paths, signal and flush only if
capture remains alive.

- [ ] **Step 3: Define cooperative in-process deadline checks**

Pass the monotonic total-run deadline into PCAPNG parsing and validation. Check
it before starting and after every frame. Expiry aborts before accepting another
frame and follows the arbiter's total-timeout precedence.

- [ ] **Step 4: Make cleanup bounded at zero budget**

Record project name and observed resource names or IDs during normal lifecycle
operations. Enter cleanup on every path:

```text
remaining budget > 0 -> run Compose down within that budget
remaining budget = 0 -> launch no Docker command; record cleanup timeout
cleanup command expires -> terminate local CLI; make no later Docker query
```

In both timeout cases, report last known inventory as possibly remaining. Do not
promise exact post-deadline resource state.

- [ ] **Step 5: Complete error precedence and evidence summary**

Make total-run timeout primary only when no higher-priority event or earlier
primary failure exists. After natural target nonzero it is secondary; after
natural target zero it may become primary during flush, parsing, or validation.
Name event-pair tests, slow-parser deadline tests, zero-budget cleanup, live/dead
capture signalling, and the five-second capture-failure response test.

- [ ] **Step 6: Verify the authoritative lifecycle**

Run:

```bash
rg -n \
  -e 'event arbiter|user interruption|natural target stop|unexpected capture' \
  -e 'stage-specific timeout|total-run timeout|after every frame' \
  -e 'next orchestration action|only if.*alive|no blocking Docker call' \
  -e 'no further Docker query|possibly remaining|five seconds' \
  architecture/CAPTURE.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/CAPTURE.md
```

Expected: all races use one fixed table, in-process work cooperates with the
deadline, capture failure never flushes a dead process, and cleanup cannot start
blocking work with zero budget.

- [ ] **Step 7: Commit event arbitration**

```bash
git add architecture/CAPTURE.md
git commit -m "docs: define capture event arbitration"
```

---

### Task 2: Align the system contract

**Files:**
- Modify: `architecture/SYSTEM.md:91-98`
- Modify: `architecture/SYSTEM.md:151-160`
- Modify: `architecture/SYSTEM.md:277-294`

**Interfaces:**
- Consumes: Task 1 event table, monotonic deadline, capture liveness, and cleanup
  inventory rules
- Produces: concise command behavior and system-wide failure policy without a
  contradictory unconditional flush

- [ ] **Step 1: Summarize the event arbiter**

Say that capture monitors target, capture, interruption, stage timeouts, and the
total deadline through one arbiter. An unexpected capture exit makes target kill
the next action.

- [ ] **Step 2: Define parser and validator cooperation**

State that the same monotonic deadline is checked before PCAPNG processing and
after each frame. No worker process or thread is added.

- [ ] **Step 3: Correct signalling and cleanup wording**

Limit `SIGINT` and bounded flush to paths where capture remains alive. If capture
already exited, reject its output directly. Define zero-budget cleanup, local CLI
termination, no post-deadline Docker query, and last-known inventory reporting.

- [ ] **Step 4: Publish the fixed event priority**

Include the same five-item order from Task 1 and state that total-run timeout is
primary only without a higher-priority or earlier primary failure.

- [ ] **Step 5: Verify the system contract**

Run:

```bash
rg -n \
  -e 'event arbiter|user interruption|natural target stop|unexpected capture' \
  -e 'stage-specific timeout|total-run timeout|after every frame' \
  -e 'capture remains alive|already exited|zero.*budget' \
  -e 'no.*Docker query|last known.*inventory' \
  architecture/SYSTEM.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/SYSTEM.md
```

Expected: the system summary matches Task 1 and contains no unconditional flush
after capture failure.

- [ ] **Step 6: Commit system alignment**

```bash
git add architecture/SYSTEM.md
git commit -m "docs: align capture event policy"
```

---

### Task 3: Require deterministic arbitration evidence

**Files:**
- Modify: `architecture/TESTING.md:177-238`

**Interfaces:**
- Consumes: Tasks 1-2 event order, deadline checkpoints, signal rule, cleanup
  inventory, and prompt-response bound
- Produces: exact unit, in-process integration, and Docker integration evidence

- [ ] **Step 1: Add event-arbiter unit cases**

Require table-driven tests for every simultaneous pair in the five-event order.
Add the target-stop/capture-stop/total-timeout triple and require natural target
status to win.

- [ ] **Step 2: Add cooperative parser deadline evidence**

Use a fake monotonic clock and a multi-frame PCAPNG fixture. Advance the clock
past the deadline after one frame and require parsing/validation to abort before
accepting the next frame.

- [ ] **Step 3: Add signalling and cleanup-boundary evidence**

Require:

```text
live capture -> SIGINT and bounded flush
already-stopped capture -> no SIGINT and no flush wait
zero cleanup budget -> no Docker command, cleanup timeout, inventory reported
hanging cleanup -> local CLI terminated, no later Docker query, inventory
```

- [ ] **Step 4: Strengthen prompt Docker failure evidence**

After unexpected capture exit, assert target kill is the next command and target
stops within five seconds. Choose a workload timeout much longer than five
seconds so waiting for that timeout cannot pass.

- [ ] **Step 5: Correct cleanup resource expectations**

Require no labelled resources after ordinary Docker cases. For controlled
cleanup-timeout cases, require last known inventory to be reported as possibly
remaining instead of asserting successful removal.

- [ ] **Step 6: Verify test ownership**

Run:

```bash
rg -n \
  -e 'every simultaneous pair|target stop.*capture stop.*total' \
  -e 'fake monotonic|after every frame|before.*next frame' \
  -e 'already-stopped|zero.*budget|no.*Docker command|no later Docker query' \
  -e 'next.*command|within five seconds|possibly remaining' \
  architecture/TESTING.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/TESTING.md
```

Expected: every new rule has an exact deterministic test and ordinary Docker
cleanup is not conflated with the controlled timeout fixture.

- [ ] **Step 7: Commit arbitration evidence**

```bash
git add architecture/TESTING.md
git commit -m "docs: test capture event arbitration"
```

---

### Task 4: Assign event arbitration to Phase 3

**Files:**
- Modify: `architecture/ROADMAP.md:110-170`

**Interfaces:**
- Consumes: Tasks 1-3
- Produces: Phase 3 deliverables and tests for all final reviewed reliability
  details

- [ ] **Step 1: Add final lifecycle deliverables**

Name the five-event arbiter priority, next-action target kill, conditional
capture flush, per-frame deadline checks, and zero-budget cleanup inventory.

- [ ] **Step 2: Add final Phase 3 tests**

Require all Task 3 cases, including every event pair, the triple race, fake-clock
parser expiry, live/dead capture signalling, zero-budget and hanging cleanup, and
the five-second Docker response bound.

- [ ] **Step 3: Verify roadmap ownership and stable phases**

Run:

```bash
rg -n \
  -e 'event arbiter|user interruption|natural target|unexpected capture' \
  -e 'stage-specific|total-run|after every frame|zero.*budget' \
  -e 'five seconds|already-stopped|possibly remaining' \
  architecture/ROADMAP.md
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/ROADMAP.md
```

Expected: Phase 3 owns every final detail and seven phases remain.

- [ ] **Step 4: Commit roadmap ownership**

```bash
git add architecture/ROADMAP.md
git commit -m "docs: plan capture event arbitration"
```

---

### Task 5: Verify and independently re-review

**Files:**
- Verify: `architecture/CAPTURE.md`
- Verify: `architecture/SYSTEM.md`
- Verify: `architecture/TESTING.md`
- Verify: `architecture/ROADMAP.md`
- Verify: `docs/superpowers/specs/2026-08-11-capture-process-ownership-design.md`

**Interfaces:**
- Consumes: Tasks 1-4
- Produces: fresh repository evidence and explicit independent confirmation that
  the event/deadline contradictions are closed

- [ ] **Step 1: Verify shared vocabulary and scope counts**

Run:

```bash
rg -n 'event arbiter|total-run timeout|after every frame|zero.*budget' \
  architecture/CAPTURE.md architecture/SYSTEM.md architecture/TESTING.md \
  architecture/ROADMAP.md
test "$(find architecture/traffic_models -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 3
test "$(find architecture/similarity_methods -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 4
test "$(find architecture/genetic_models -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 1
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
```

- [ ] **Step 2: Run link, formatting, and repository checks**

Run:

```bash
uv run --locked python - <<'PY'
from pathlib import Path
import re

missing = []
for source in Path("architecture").rglob("*.md"):
    for target in re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", source.read_text()):
        if "://" not in target and not (source.parent / target).resolve().exists():
            missing.append(f"{source}: {target}")
assert not missing, "\n".join(missing)
PY
git diff --check
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest -q
```

Expected: links, formatting, lock, Ruff, and Pyright pass. If the repository has
no tests yet, record pytest exit 5 and `no tests ran` as scaffold state.

- [ ] **Step 3: Request independent read-only review**

Give the reviewer the design spec, this plan, and the four implementation
commits. Require confirmation that total-timeout arbitration, cooperative parser
deadline, zero-budget cleanup, conditional flush, and prompt failure evidence
are complete without adding prototype machinery.

- [ ] **Step 4: Review the exact diff and clean state**

Run:

```bash
git diff HEAD~4 -- architecture/CAPTURE.md architecture/SYSTEM.md \
  architecture/TESTING.md architecture/ROADMAP.md
git status --short --branch
```

Expected: only final event-arbitration corrections changed and worktree is clean.
