# Capture Process Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the idle-container and Compose-exec capture design with one
Docker-owned target service command and bounded whole-container cleanup.

**Architecture:** The capture service owns the shared Docker network namespace
and starts first. The target joins it with `network_mode: service:capture`, runs
the configured argument vector directly under Docker's tiny init, and stops as
one container before capture is flushed and validated.

**Tech Stack:** Markdown architecture documents, Docker Compose services,
Docker container lifecycle, PCAPNG capture, Docker integration tests

## Global Constraints

- Use exactly two services for a normal production capture: `capture` and
  `target`.
- Let `capture` own the shared network namespace and `eth0`.
- Configure `target` with `network_mode: service:capture` and `init: true`.
- Run the configured workload argument vector directly as the target service
  command.
- Start the target only after capture readiness.
- On target timeout, capture failure, or interruption, kill the whole target
  container rather than one launcher process.
- Flush capture with `SIGINT` under the existing capture-flush timeout.
- Keep target failures primary, preserve valid diagnostic capture, and clean up
  unconditionally.
- Keep Docker-provided Internet access and normal operation without `sudo`.
- Add no shell requirement, idle command, wrapper, PID-file protocol, process
  manager, setting, artifact field, persistent service, security feature, or
  additional production container.
- Keep the existing controlled endpoint fixture for deterministic Docker tests.
- Keep exactly three traffic models, four similarity methods, one genetic
  strategy, and seven roadmap phases.
- Keep edited lines at no more than 120 characters.

---

### Task 1: Make Docker own the workload lifetime

**Files:**
- Modify: `architecture/CAPTURE.md:1-130`

**Interfaces:**
- Consumes: target image, argument vector, environment, working directory,
  mounts, readiness timeout, workload timeout, flush timeout, and run identity
- Produces: one validated capture pair or one bounded failure with diagnostic
  output and project-scoped cleanup

- [ ] **Step 1: Reverse namespace ownership in the topology**

Replace the topology diagram and explanation with this contract:

```text
Docker Compose default bridge (Docker-provided Internet, DNS, and NAT)
  capture service
    owns the network namespace and eth0
    capture tool is the service process
  target service
    network_mode: service:capture
    configured workload argv is the service command
```

State that both services observe the same `eth0`, capture stays alive after the
target stops, and explicitly configured published ports belong to the capture
service because it owns the shared network namespace.

- [ ] **Step 2: Replace the target image contract**

Delete the POSIX shell and idle-command requirements. Specify:

```text
The target uses init: true and runs the configured argument vector directly.
The tiny init may be PID 1; the workload remains the container's single
supervised service command. The target image needs only that workload and its
ordinary runtime dependencies.
```

Keep the target's configured user, environment, working directory, and mounts.
In preflight, replace the shell/idle probe with validation of the rendered
Compose configuration and configured Docker-facing target inputs.

- [ ] **Step 3: Publish the exact lifecycle**

Replace the current idle-start and `docker compose exec -T` steps with:

```text
1. Create the unique Compose project without starting target.
2. Start capture and wait for its interface metadata and readiness signal.
3. Start target only after readiness.
4. Wait for the target container and read its exit status within the workload
   timeout.
5. Signal capture with SIGINT and wait within the flush timeout.
6. Validate temporary capture.json and PCAPNG.
7. Publish the reusable pair only after a successful target exit.
8. Run project-scoped Compose cleanup unconditionally.
```

State that the stopped target's process namespace cannot retain a background
child while the capture network namespace remains alive under `capture`.

- [ ] **Step 4: Define failure ownership**

Update `Reliability behavior` to require:

```text
readiness failure -> target never starts
target nonzero -> bounded capture flush, diagnostic capture, target status wins
workload timeout -> kill target container, bounded capture flush
capture failure -> kill target container, reject capture
flush timeout -> kill capture container, reject incomplete output
interruption -> kill target, attempt one bounded capture flush
all paths -> remove project resources; cleanup errors remain secondary
```

Say explicitly that a timed-out target is not given an additional graceful-stop
protocol because the run has already failed.

- [ ] **Step 5: Align the integration-test topology**

Retain the controlled endpoint fixture and opt-in public Internet smoke test.
Change production topology wording to `target` joining `capture`, and require
tests for direct service-command launch, child cleanup, exact target status,
bounded capture flush, and absence of shell, idle-command, wrapper, and Compose
`exec` dependencies.

- [ ] **Step 6: Verify the capture contract**

Run:

```bash
rg -n \
  -e 'network_mode: service:capture|init: true|service command' \
  -e 'target container|background|SIGINT|diagnostic|unconditional cleanup' \
  architecture/CAPTURE.md
if rg -n \
  -e 'network_mode: service:target|idle target image|Start the idle target' \
  -e 'Start the target service with its idle command|docker compose exec -T' \
  -e 'target image contract is deliberately small: a POSIX shell' \
  architecture/CAPTURE.md; then exit 1; fi
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/CAPTURE.md
```

Expected: capture owns the namespace, the target workload has one Docker-owned
lifetime, no old launcher protocol remains, and formatting is clean.

- [ ] **Step 7: Commit the Docker lifecycle**

```bash
git add architecture/CAPTURE.md
git commit -m "docs: simplify capture ownership"
```

---

### Task 2: Align the system contract

**Files:**
- Modify: `architecture/SYSTEM.md:91-96`
- Modify: `architecture/SYSTEM.md:128-149`
- Modify: `architecture/SYSTEM.md:254-273`

**Interfaces:**
- Consumes: the authoritative capture lifecycle from Task 1
- Produces: one concise public command contract and matching system-level
  failure policy

- [ ] **Step 1: Rewrite the capture command summary**

Make the `capture` section state this sequence:

```text
Start capture as network-namespace owner, wait for readiness, start the target
argument vector as its service command, wait for its container status, flush
capture, validate artifacts, and tear down the project.
```

- [ ] **Step 2: Clarify configuration application**

After the argument-vector rule, state that target argv is applied directly as
the Compose service command and is never evaluated as a shell string. Keep all
existing experiment fields and timeout names unchanged.

- [ ] **Step 3: Align failure and cleanup rules**

Extend `Failure policy` with these exact responsibilities:

```text
Trafficlab kills the complete target container on workload timeout, capture
failure, or interruption. It attempts capture SIGINT and bounded flush before
cleanup. A nonzero target status remains primary; cleanup errors never replace
it. No target process manager or PID protocol exists.
```

- [ ] **Step 4: Verify the system summary**

Run:

```bash
rg -n \
  -e 'namespace owner|service command|container status' \
  -e 'complete target container|bounded flush|nonzero target|PID protocol' \
  architecture/SYSTEM.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/SYSTEM.md
```

Expected: the public command and failure policy summarize Task 1 without adding
a setting, command, subprocess protocol, or alternate capture path.

- [ ] **Step 5: Commit the system contract**

```bash
git add architecture/SYSTEM.md
git commit -m "docs: align capture orchestration"
```

---

### Task 3: Require lifecycle integration evidence

**Files:**
- Modify: `architecture/TESTING.md:177-209`

**Interfaces:**
- Consumes: Task 1 topology and failure behavior
- Produces: deterministic Docker evidence for process ownership, exit status,
  bounded failure handling, packet correctness, and cleanup

- [ ] **Step 1: Correct the tested production topology**

Replace `network_mode: service:target` with the production rule that capture
owns the default-bridge network namespace and target uses
`network_mode: service:capture`. Preserve the controlled endpoint fixture.

- [ ] **Step 2: Expand lifecycle assertions**

Require the Docker tests to prove:

```text
capture readiness occurs before target service-command start
successful and nonzero target statuses are propagated exactly
a background child cannot survive normal target-container exit
a timed-out target and its children are killed
capture failure stops target
SIGINT yields a readable capture within the flush timeout
interruption performs bounded flush and cleanup
```

- [ ] **Step 3: Make cleanup and contract assertions complete**

Require every case to inspect project-labelled containers, networks, volumes,
and orphans. Add one contract fixture whose target image has no shell or idle
command, proving production launch does not use a wrapper, PID file, or Compose
`exec`. Keep Docker tests serial and the Internet smoke test opt-in.

- [ ] **Step 4: Verify test ownership**

Run:

```bash
rg -n \
  -e 'network_mode: service:capture|service-command|nonzero target status' \
  -e 'background child|timed-out target|capture failure|flush timeout' \
  -e 'containers, networks, volumes, and orphans|no shell|Compose.*exec' \
  architecture/TESTING.md
if rg -n 'network_mode: service:target' architecture/TESTING.md; then exit 1; fi
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/TESTING.md
```

Expected: every approved success and failure path has observable Docker
evidence, while public Internet remains outside the deterministic gate.

- [ ] **Step 5: Commit lifecycle evidence**

```bash
git add architecture/TESTING.md
git commit -m "docs: test capture ownership"
```

---

### Task 4: Assign implementation to Phase 3

**Files:**
- Modify: `architecture/ROADMAP.md:110-147`

**Interfaces:**
- Consumes: Tasks 1-3
- Produces: Phase 3 implementation and test checklist for the approved topology

- [ ] **Step 1: Replace the old Phase 3 lifecycle deliverable**

Replace the idle-target and namespace-sharing-sidecar item with deliverables
that require:

```text
capture owns eth0 and starts first
target joins with network_mode: service:capture
target argv runs directly as the service command under init: true
target container status closes the workload window
timeout/capture failure/interruption kill the complete target container
capture SIGINT and artifact validation remain bounded
```

- [ ] **Step 2: Complete the Phase 3 test checklist**

Keep packet and direction checks. Expand failure tests to name exact nonzero
status, normal and timed-out background children, capture failure, interruption,
bounded flush, absence of shell/idle/exec requirements, and inspection for all
remaining project resources.

- [ ] **Step 3: Verify roadmap ownership and phase stability**

Run:

```bash
rg -n \
  -e 'capture owns|network_mode: service:capture|service command|init: true' \
  -e 'complete target container|background children|bounded flush|no shell' \
  architecture/ROADMAP.md
if rg -n 'idle target|namespace-sharing capture sidecar|compose exec' \
  architecture/ROADMAP.md; then exit 1; fi
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/ROADMAP.md
```

Expected: Phase 3 owns every approved behavior, later phases are unchanged,
seven phases remain, and formatting is clean.

- [ ] **Step 4: Commit roadmap ownership**

```bash
git add architecture/ROADMAP.md
git commit -m "docs: plan capture ownership"
```

---

### Task 5: Verify the architecture as one contract

**Files:**
- Verify: `architecture/CAPTURE.md`
- Verify: `architecture/SYSTEM.md`
- Verify: `architecture/TESTING.md`
- Verify: `architecture/ROADMAP.md`
- Verify: `architecture/traffic_models/README.md`
- Verify: `architecture/similarity_methods/README.md`
- Verify: `architecture/genetic_models/README.md`

**Interfaces:**
- Consumes: Tasks 1-4
- Produces: evidence that topology, lifecycle, tests, and roadmap agree without
  expanding MVP scope

- [ ] **Step 1: Search for contradictory ownership**

Run:

```bash
if rg -n \
  -e 'network_mode: service:target|idle target image|Start the idle target' \
  -e 'Start the target service with its idle command|docker compose exec -T' \
  -e 'target image contract is deliberately small: a POSIX shell' \
  architecture; then exit 1; fi
rg -n 'network_mode: service:capture' \
  architecture/CAPTURE.md architecture/TESTING.md architecture/ROADMAP.md
```

Expected: no old process-ownership contract remains and all topology owners use
the approved direction.

- [ ] **Step 2: Verify scope counts and relative links**

Run:

```bash
test "$(find architecture/traffic_models -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 3
test "$(find architecture/similarity_methods -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 4
test "$(find architecture/genetic_models -maxdepth 1 -name '*.md' ! -name README.md | wc -l)" -eq 1
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
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
```

Expected: three models, four methods, one genetic strategy, seven phases, and no
broken internal Markdown link.

- [ ] **Step 3: Run final formatting and repository checks**

Run:

```bash
git diff --check
git status --short
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest -q
```

Expected: documentation formatting and available project checks pass. If the
repository still contains no tests, record pytest's exit code 5 and `no tests
ran` as the current scaffold state rather than claiming a passing test suite.

- [ ] **Step 4: Review the complete architecture diff**

Run:

```bash
git diff HEAD~4 -- architecture/CAPTURE.md architecture/SYSTEM.md \
  architecture/TESTING.md architecture/ROADMAP.md
```

Expected: the diff implements only the approved process-ownership design and
contains no unrelated preflight, model, similarity, genetic, artifact, or
security change.
