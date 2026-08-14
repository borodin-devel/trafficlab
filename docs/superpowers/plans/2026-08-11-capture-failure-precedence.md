# Capture Failure Precedence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the reviewed capture reliability gaps by monitoring both
containers, preserving causal errors, bounding cleanup, and requiring direct
Docker evidence.

**Architecture:** After target starts, Trafficlab watches target and capture
state together. The event that causes termination owns the primary error,
kill-induced statuses remain secondary, and every wait through cleanup is
bounded by the existing total-run deadline.

**Tech Stack:** Markdown architecture documents, Docker Compose services,
Docker container state and exit status, PCAPNG validation, Docker integration
tests, uv

## Global Constraints

- Start the configured total-run deadline when the Compose project is created.
- Cap target, capture, flush, validation, and cleanup waits by the remaining
  total-run budget.
- Monitor target and capture together after readiness.
- Kill target immediately when capture exits unexpectedly while target runs.
- Preserve a natural nonzero target status as primary.
- Preserve timeout, capture failure, or interruption as primary when it causes
  target termination; never replace it with the induced target status.
- Make flush or validation failure primary after natural target success.
- Make cleanup failure primary only when no earlier operation failed.
- Treat target as naturally stopped if both services are stopped in one state
  observation; target nonzero wins, while capture failure wins after target zero.
- Record all secondary failures and statuses in `run.log`.
- Bound cleanup with the remaining total-run budget; add no timeout setting.
- Keep exactly two production services, `{capture, target}`; the controlled
  endpoint remains only a test fixture.
- Keep Docker-provided Internet access and normal operation without `sudo`.
- Add no shell requirement, idle command, wrapper, PID protocol, process
  manager, setting, artifact field, security feature, or production service.
- Keep exactly three traffic models, four similarity methods, one genetic
  strategy, and seven roadmap phases.
- Keep edited lines at no more than 120 characters.

---

### Task 1: Complete the capture lifecycle

**Files:**
- Modify: `architecture/CAPTURE.md:64-148`

**Interfaces:**
- Consumes: readiness, workload, flush, and total-run timeouts; target and
  capture container states; natural target status; temporary capture artifacts
- Produces: one causal primary result, secondary diagnostics, bounded cleanup,
  and either a reusable successful pair or diagnostic-only failure output

- [ ] **Step 1: Apply the total-run deadline to the whole Docker lifecycle**

At Compose project creation, start the configured total-run deadline. State:

```text
Every later wait is capped by its stage timeout where applicable and by the
remaining total-run budget. Cleanup is always attempted with that remaining
budget; expiry stops the local Compose wait and reports labelled resources that
remain.
```

Do not add a cleanup timeout setting or fixed deadline.

- [ ] **Step 2: Monitor both services after readiness**

Replace the target-only wait with this contract:

```text
After target starts, monitor target and capture container state together.
A natural target stop closes the workload window and supplies its status.
An unexpected capture stop while target is running is capture failure and kills
the entire target container immediately, before workload timeout.
```

Keep capture alive after natural target completion for bounded `SIGINT` flush.

- [ ] **Step 3: Publish causal error precedence**

Add an ordered precedence section:

```text
1. Natural target nonzero is primary; later flush, validation, and cleanup
   failures are secondary.
2. Timeout, unexpected capture exit, or interruption is primary when it causes
   target kill; the resulting target status is secondary.
3. After natural target zero, flush or validation failure is primary.
4. Cleanup failure is primary only when nothing earlier failed.
```

For one observation where both services are stopped, treat target as naturally
stopped. Target nonzero is primary; target zero makes the unexpected capture
exit primary. Record every secondary detail in `run.log`.

- [ ] **Step 4: Complete the integration-test summary**

Require the capture document to name:

```text
prompt capture-failure detection
natural versus kill-induced target status
SIGINT-ignoring capture flush timeout
malformed-output cleanup
hanging-cleanup total deadline
exact production service set {capture, target}
normal Docker launch without sudo
```

- [ ] **Step 5: Verify the lifecycle contract**

Run:

```bash
rg -n \
  -e 'monitor.*target.*capture|capture.*while target.*kill' \
  -e 'remaining total-run|natural.*nonzero|induced|same.*observation' \
  -e 'cleanup.*primary|run.log|exactly.*capture.*target|without.*sudo' \
  architecture/CAPTURE.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/CAPTURE.md
```

Expected: target-only waiting is gone, the termination cause determines error
precedence, cleanup is bounded without a new setting, and formatting is clean.

- [ ] **Step 6: Commit the complete lifecycle**

```bash
git add architecture/CAPTURE.md
git commit -m "docs: fix capture failure handling"
```

---

### Task 2: Align the system failure policy

**Files:**
- Modify: `architecture/SYSTEM.md:91-96`
- Modify: `architecture/SYSTEM.md:128-151`
- Modify: `architecture/SYSTEM.md:257-286`

**Interfaces:**
- Consumes: Task 1 lifecycle and precedence
- Produces: concise public capture orchestration, timeout semantics, and
  system-wide primary-error rules

- [ ] **Step 1: Mention concurrent state monitoring**

Update the `capture` command summary to say it monitors target and capture
together after readiness and stops target promptly on unexpected capture exit.

- [ ] **Step 2: Define the existing total-run timeout**

After the argument-vector rule, state:

```text
For capture, the total-run deadline starts when the Compose project is created
and caps every later wait through unconditional cleanup. Stage-specific
readiness, workload, and flush timeouts are also enforced within that budget.
```

This defines an existing field; it does not create another setting.

- [ ] **Step 3: Replace the unqualified status rule**

Replace `A nonzero target status remains primary` with causal rules:

```text
Natural target nonzero remains primary. Timeout, capture failure, or
interruption remains primary over the status induced by killing target. After
natural target success, flush or validation failure is primary. Cleanup failure
is primary only if the run otherwise succeeded. Secondary details go to run.log.
```

- [ ] **Step 4: Verify the system contract**

Run:

```bash
rg -n \
  -e 'monitor.*target.*capture|unexpected capture' \
  -e 'total-run deadline|remaining.*budget|natural target.*nonzero' \
  -e 'induced|flush or validation|cleanup.*primary|run.log' \
  architecture/SYSTEM.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/SYSTEM.md
```

Expected: the system summary matches Task 1, uses no new setting, and never
mistakes a kill-induced target status for the cause of failure.

- [ ] **Step 5: Commit the system policy**

```bash
git add architecture/SYSTEM.md
git commit -m "docs: clarify capture error precedence"
```

---

### Task 3: Require the missing Docker evidence

**Files:**
- Modify: `architecture/TESTING.md:177-220`

**Interfaces:**
- Consumes: Tasks 1-2 lifecycle, precedence, service-set, and timeout rules
- Produces: observable integration evidence for every reviewed reliability edge

- [ ] **Step 1: Add concurrent-monitoring and precedence cases**

Require a long-running target plus a capture that exits unexpectedly. Assert
that target is stopped before workload timeout and capture failure is primary.
Add paired cases proving natural target nonzero is primary while timeout,
capture failure, and interruption remain primary over kill-induced status.

- [ ] **Step 2: Add flush, malformed-output, and cleanup deadlines**

Require:

```text
a capture fixture that ignores SIGINT -> killed at flush timeout, output rejected
malformed capture output -> no reusable pair, complete cleanup
hanging Compose cleanup -> stopped at remaining total deadline, leftovers named
```

- [ ] **Step 3: Assert scope and launch contract**

Inspect rendered production Compose configuration and require service keys to be
exactly `{capture, target}`. The endpoint service is permitted only in the test
fixture. Inspect the invoked command vector and require `docker` directly, never
`sudo docker`.

- [ ] **Step 4: Make cleanup cases exhaustive**

Extend the cleanup list to include malformed output, flush timeout, cleanup
timeout, and all precedence fixtures. Continue checking labelled containers,
networks, volumes, and orphans, and print remaining names on failure.

- [ ] **Step 5: Verify Docker evidence ownership**

Run:

```bash
rg -n \
  -e 'before.*workload timeout|kill-induced|natural.*nonzero' \
  -e 'ignores.*SIGINT|malformed|hanging.*cleanup|remaining total' \
  -e 'exactly.*capture.*target|without.*sudo|never.*sudo' \
  -e 'flush timeout|cleanup timeout|containers, networks, volumes, and orphans' \
  architecture/TESTING.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/TESTING.md
```

Expected: each reliability rule has direct Docker evidence and the deterministic
endpoint does not become a production service.

- [ ] **Step 6: Commit the evidence contract**

```bash
git add architecture/TESTING.md
git commit -m "docs: complete capture failure tests"
```

---

### Task 4: Assign the review fixes to Phase 3

**Files:**
- Modify: `architecture/ROADMAP.md:110-157`

**Interfaces:**
- Consumes: Tasks 1-3
- Produces: Phase 3 implementation and acceptance ownership for every reviewed
  reliability behavior

- [ ] **Step 1: Complete the lifecycle deliverables**

Add Phase 3 deliverables for monitoring both services, prompt target kill after
capture failure, causal error precedence, remaining-total cleanup deadline, and
secondary diagnostics in `run.log`.

- [ ] **Step 2: Complete the Phase 3 test checklist**

Name all Task 3 cases: prompt capture failure, natural versus induced status,
SIGINT-ignoring capture, malformed output, hanging cleanup, exact production
service set, direct Docker without sudo, and labelled-resource inspection.

- [ ] **Step 3: Verify roadmap ownership and stable phases**

Run:

```bash
rg -n \
  -e 'monitor.*target.*capture|prompt|natural.*induced|remaining total' \
  -e 'ignores.*SIGINT|malformed|hanging cleanup|exactly.*capture.*target' \
  -e 'without.*sudo|run.log' \
  architecture/ROADMAP.md
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/ROADMAP.md
```

Expected: Phase 3 owns all review fixes, no later phase changes, seven phases
remain, and formatting is clean.

- [ ] **Step 4: Commit roadmap ownership**

```bash
git add architecture/ROADMAP.md
git commit -m "docs: plan capture failure handling"
```

---

### Task 5: Verify and re-review the corrected contract

**Files:**
- Verify: `architecture/CAPTURE.md`
- Verify: `architecture/SYSTEM.md`
- Verify: `architecture/TESTING.md`
- Verify: `architecture/ROADMAP.md`
- Verify: `docs/superpowers/specs/2026-08-11-capture-process-ownership-design.md`

**Interfaces:**
- Consumes: Tasks 1-4
- Produces: fresh structural/tooling evidence and independent confirmation that
  all Important review findings are closed

- [ ] **Step 1: Verify cross-document terminology and scope**

Run:

```bash
rg -n 'monitor.*target.*capture|remaining total-run|natural.*nonzero|induced' \
  architecture/CAPTURE.md architecture/SYSTEM.md architecture/TESTING.md \
  architecture/ROADMAP.md
test "$(find architecture/traffic_models -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 3
test "$(find architecture/similarity_methods -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 4
test "$(find architecture/genetic_models -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 1
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
```

Expected: all four documents use the same causal vocabulary and algorithm counts
remain unchanged.

- [ ] **Step 2: Verify relative links and formatting**

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
```

Expected: no broken internal link or whitespace error.

- [ ] **Step 3: Run repository checks**

Run:

```bash
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest -q
```

Expected: lock, Ruff, and Pyright checks pass. If no tests exist yet, record
pytest exit 5 and `no tests ran` as scaffold state rather than claiming tests
pass.

- [ ] **Step 4: Request independent review**

Give a read-only reviewer the design spec, this plan, and the four implementation
commits. Require explicit confirmation that concurrent monitoring, primary-error
precedence, bounded cleanup, and all requested integration cases are complete.

- [ ] **Step 5: Review the final diff**

Run:

```bash
git diff HEAD~4 -- architecture/CAPTURE.md architecture/SYSTEM.md \
  architecture/TESTING.md architecture/ROADMAP.md
git status --short --branch
```

Expected: only the reviewed reliability gaps changed, and the worktree is clean.
