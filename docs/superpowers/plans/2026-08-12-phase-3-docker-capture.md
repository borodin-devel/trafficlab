# Trafficlab Phase 3 Docker Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a configured target in a temporary Docker Compose network
namespace, capture its real Ethernet traffic into a validated PCAPNG reference,
and always perform bounded project-scoped cleanup without changing host
networking.

**Architecture:** Keep Docker at one injected subprocess boundary. Pure modules
render the two-service Compose document, arbitrate simultaneous lifecycle
events, classify outcomes, and validate capture observations; one direct
orchestrator composes those pieces. The capture image has a tiny POSIX-shell
entrypoint that writes strict temporary metadata and then `exec`s `dumpcap` in
non-promiscuous mode. Readiness means the container remains running and dumpcap
has created a nonempty PCAPNG header; it needs no PID file or second supervisor.
Target argv remains the direct Compose command and is never evaluated by a shell.

**Tech Stack:** CPython 3.12, standard library (`dataclasses`, `enum`, `json`,
`ipaddress`, `subprocess`, `time`, `uuid`), Pydantic 2, Docker Engine, Docker
Compose v2, Debian bookworm-slim plus `dumpcap`, POSIX shell, pytest,
pytest-cov, pytest-xdist, Ruff, Pyright

## Global Constraints

- Use `src/trafficlab/`; keep Docker-free policy functions independent of
  subprocess and filesystem mutation.
- Production Compose services are exactly `capture` and `target`. A controlled
  endpoint exists only in test Compose overlays.
- Capture owns the ordinary Compose bridge network namespace. Target uses
  `network_mode: service:capture`, `init: true`, and its configured argv as the
  direct service command.
- Invoke `docker` directly. Do not use `sudo`, a shell command string, Compose
  `exec`, a PID file, or a target launcher protocol.
- Capture only `eth0`, with promiscuous mode disabled, into PCAPNG Enhanced
  Packet Blocks. Reject missing, zero, multicast, or malformed target MACs.
- At every wait boundary, choose visible events in this exact order: user
  interruption, natural target stop, unexpected capture stop, stage timeout,
  total timeout.
- The configured total deadline begins when the Compose project is created and
  bounds readiness, workload, flush, parsing, validation, and cleanup.
- Unexpected capture exit makes target kill the next Docker action. Signal and
  wait for capture flush only if capture is still alive.
- Preserve the first primary failure. Record induced statuses and every later
  failure as secondary details in `run.log`.
- Cleanup is unconditional, idempotent, unique-project scoped, and limited by
  remaining total budget. Zero budget launches no Docker process. An expired
  cleanup terminates its local CLI and permits no later Docker query.
- Publish validated `capture.json` before `reference.pcapng`; an incomplete pair
  is never reusable. Natural target failure may retain diagnostic files but may
  not publish the reusable pair.
- Expected boundary failures are actionable `TrafficlabError` values. No raw
  `OSError`, `TimeoutExpired`, JSON, or arithmetic error crosses a public stage.
- No new Python runtime dependency and no NodeJS package or tool.
- Keep lines at no more than 120 characters. Maintain at least 90%
  branch-aware non-Docker coverage.
- Write tests before implementation. Commit only after focused tests, Ruff,
  strict Pyright, and `git diff --check` pass.
- This host has no Docker CLI. Unit and injected in-process evidence is required
  here. Real `docker` and `internet` results must be recorded truthfully on a
  Docker-capable host before Phase 3 Roadmap closure; absence is not a skip or a
  reason to stop later implementation phases.

---

### Task 1: Deterministic production Compose contract

**Files:**
- Create: `src/trafficlab/compose.py`
- Create: `tests/unit/test_compose.py`
- Create: `docker/capture/Dockerfile`
- Create: `docker/capture/capture.sh`
- Modify: `examples/configs/minimal.toml`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `ExperimentConfig`, run-directory path, and project identity.
- Produces: immutable `ComposePaths`, `render_production_compose(config,
  paths) -> bytes`, and `write_production_compose(path, config, paths) -> None`.
- The JSON-form Compose document is accepted by Compose as YAML-compatible
  input and avoids a YAML dependency.

- [ ] **Step 1: Write the failing Compose contract tests**

Assert deterministic sorted compact JSON, top-level `services`, service keys
exactly `{capture, target}`, capture capabilities `NET_RAW` and `NET_ADMIN`, one
output bind mount, target `network_mode: service:capture`, `init: true`, direct
argv list, image, environment, working directory, mounts, and no `command`
string, `entrypoint`, privileged mode, host network, or test endpoint. Assert
Docker-facing bind sources are absolute resolved host paths and container
destinations remain absolute POSIX paths.

- [ ] **Step 2: Confirm the focused red state**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_compose.py
```

Expected: collection fails because `trafficlab.compose` does not exist.

- [ ] **Step 3: Implement the smallest renderer and capture image**

Use standard `json.dumps(sort_keys=True, separators=(",", ":"))`. The capture
service command is the image default. `capture.sh` must use `set -eu`, require
`eth0`, normalize and validate its sysfs MAC, atomically rename temporary
`capture.json`, remove stale readiness/output files, and then:

```sh
exec dumpcap -i eth0 -p -q -w /trafficlab/reference.pcapng.tmp
```

The Dockerfile installs only `dumpcap`, `curl`, and certificate/runtime necessities,
copies the script, and sets it as `ENTRYPOINT`. Pin the base image by an explicit
bookworm-slim tag in the plan implementation; record the resolved digest in the
task report when a Docker-capable host is available.

- [ ] **Step 4: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_compose.py
uv run --locked ruff format --check src/trafficlab/compose.py tests/unit/test_compose.py
uv run --locked ruff check src/trafficlab/compose.py tests/unit/test_compose.py
uv run --locked pyright
git diff --check
```

Commit: `feat(capture): render production Compose topology`

---

### Task 2: Bounded Docker CLI boundary

**Files:**
- Create: `src/trafficlab/docker_cli.py`
- Create: `tests/unit/test_docker_cli.py`

**Interfaces:**
- Produces: immutable `CommandResult`, `ProcessHandle` protocol,
  `CommandBoundary` protocol, standard-library `SubprocessBoundary`, and
  `DockerCompose` methods for `info`, `compose_version`, `image_inspect`,
  `image_pull`, `config`, `create_capture`, `start_capture`, `start_target`, `service_state`,
  `service_logs`, `kill_target`, `signal_capture`, `kill_capture`,
  `project_inventory`, and `start_down`.
- Every method receives an absolute Compose path, project name where relevant,
  and a positive timeout or absolute deadline. Argv always begins with
  `docker`, never `sudo` or a shell.

- [ ] **Step 1: Write command-shape and failure tests first**

Use a recording fake boundary to assert exact argv arrays, text/UTF-8 capture,
environment handling, Compose `--project-name` and `--file` placement, service
names, `kill --signal SIGINT capture`, whole-container `kill target`, and
`down --volumes --remove-orphans`. Cover nonzero status, deadline before launch,
timeout termination followed by bounded kill, invalid JSON returned by `ps`,
and `FileNotFoundError`/`OSError` translation. Require no `shell=True` path.

- [ ] **Step 2: Confirm red, implement direct subprocess calls, and verify**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_docker_cli.py
uv run --locked ruff format --check src/trafficlab/docker_cli.py tests/unit/test_docker_cli.py
uv run --locked ruff check src/trafficlab/docker_cli.py tests/unit/test_docker_cli.py
uv run --locked pyright
```

Use `Popen` only where cleanup needs explicit termination at a deadline;
ordinary bounded commands use `subprocess.run`. Decode and validate Compose JSON
at this boundary, returning small typed service/resource values rather than raw
Docker dictionaries.

Commit: `feat(capture): add bounded Docker CLI boundary`

---

### Task 3: Full Docker preflight and disposable network probe

**Files:**
- Modify: `src/trafficlab/config.py`
- Modify: `tests/unit/test_config_validation.py`
- Modify: `src/trafficlab/preflight.py`
- Modify: `src/trafficlab/cli.py`
- Create: `tests/unit/test_docker_preflight.py`
- Create: `tests/integration/test_full_preflight.py`
- Modify: `tests/integration/test_preflight_cli.py`

**Interfaces:**
- Produces: `check_docker(config, compose, *, deadline, clock) ->
  PreflightReport`, `run_preflight(path, *, config_only, docker, clock) ->
  PreparedExperiment`, `open_or_prepare_experiment(path) -> PreparedExperiment`,
  and a plain `trafficlab preflight EXPERIMENT` path.
- Full preflight uses one unique probe project and the same production renderer
  plus a test-local probe override; cleanup occurs in `finally`.

- [ ] **Step 1: Write failing unit and in-process tests**

Cover strict local parsing of an HTTP/HTTPS probe URL with a hostname, daemon
and Compose availability, inspect-before-pull for both images,
pull fallback, post-pull inspect, rendered `docker compose config`, absolute
mount sources, capture-image ability to read `eth0` MAC and start dumpcap, DNS
resolution and bounded HTTP/TCP reachability of the configured probe URL,
unique probe project names, successful cleanup, primary probe failure with
secondary cleanup detail, and total-deadline exhaustion. Assert config-only
still makes zero Docker calls and plain preflight uses the injected boundary.
Require an absent run directory to be prepared once; an existing run is reused
only when its authoritative `experiment.toml` exactly equals the effective
configuration. A missing, malformed, or mismatched snapshot fails without
replacing any bytes. This permits config-only preflight, plain preflight, and
capture to run sequentially against one experiment.

- [ ] **Step 2: Confirm the red state**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_docker_preflight.py \
  tests/integration/test_full_preflight.py tests/integration/test_preflight_cli.py
```

- [ ] **Step 3: Implement sequential actionable findings**

Keep local and Docker findings in one report but do not contact Docker until
local validation succeeds or an identical prepared run is reopened. Use the
configured total timeout as the preflight bound. The probe overlay uses the
capture image with its entrypoint overridden by a direct `curl` argv, so Task 1
installs `curl` and CA certificates alongside dumpcap; it must not assume an
arbitrary target image contains a shell or probe client. The probe may create
only its unique Compose project and normal image cache entries. It must not
create the experiment's capture project or publish capture artifacts.

- [ ] **Step 4: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_preflight.py tests/unit/test_docker_preflight.py \
  tests/integration/test_preflight_cli.py tests/integration/test_full_preflight.py
uv run --locked ruff format --check src/trafficlab tests/unit tests/integration
uv run --locked ruff check src/trafficlab tests/unit tests/integration
uv run --locked pyright
git diff --check
```

Commit: `feat(preflight): verify disposable Docker topology`

---

### Task 4: Pure event arbitration and outcome precedence

**Files:**
- Create: `src/trafficlab/capture_policy.py`
- Create: `tests/unit/test_capture_policy.py`

**Interfaces:**
- Produces: `CaptureEvent` enum, immutable `EventObservation`,
  `choose_event(observation) -> CaptureEvent | None`, `FailureKind`,
  immutable `CaptureOutcome`, and pure transitions that preserve the first
  primary failure and append secondary details.

- [ ] **Step 1: Write the complete priority matrix**

Parametrize every one of the ten simultaneous event pairs and require the fixed
priority. Add the target-stop/capture-stop/total-timeout triple, no-event state,
natural target zero/nonzero, capture stop before and after target stop, stage
versus total timeout, interruption, induced target status, flush/validation
failure after target zero and nonzero, and cleanup failure with and without an
earlier primary.

- [ ] **Step 2: Confirm red and implement immutable direct policy functions**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_capture_policy.py
```

One observation contains booleans plus optional natural target status. Read the
clock outside this pure module. Never infer “natural” from a status observed
after Trafficlab requested kill.

- [ ] **Step 3: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_capture_policy.py
uv run --locked ruff format --check src/trafficlab/capture_policy.py tests/unit/test_capture_policy.py
uv run --locked ruff check src/trafficlab/capture_policy.py tests/unit/test_capture_policy.py
uv run --locked pyright
```

Commit: `feat(capture): define lifecycle event policy`

---

### Task 5: Bounded, inventory-aware cleanup

**Files:**
- Create: `src/trafficlab/cleanup.py`
- Create: `tests/unit/test_cleanup.py`
- Create: `tests/integration/test_cleanup_boundary.py`

**Interfaces:**
- Consumes: `DockerCompose.start_down`, project name, immutable last-known
  `ProjectInventory`, absolute total deadline, and monotonic clock.
- Produces: immutable `CleanupResult` with status, detail, and
  `possibly_remaining`; `cleanup_project(...) -> CleanupResult`.

- [ ] **Step 1: Write zero-budget and process-deadline regressions first**

Require positive-budget successful/nonzero cleanup, idempotent already-absent
cleanup, zero remaining budget with no Docker call, a hanging fake cleanup whose
local process receives terminate then kill within a small bound, no Docker query
after expiry, and the exact last-known inventory reported as possibly remaining.
Prove cleanup never names or touches a different project.

- [ ] **Step 2: Confirm red and implement one bounded wait loop**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_cleanup.py tests/integration/test_cleanup_boundary.py
```

Read the clock before launch and after each process wait. Once the deadline is
reached, stop the local CLI and return without calling inventory or any other
Docker method. Do not claim daemon resources were removed after a timeout.

- [ ] **Step 3: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_cleanup.py tests/integration/test_cleanup_boundary.py
uv run --locked ruff format --check src/trafficlab/cleanup.py tests
uv run --locked ruff check src/trafficlab/cleanup.py tests
uv run --locked pyright
```

Commit: `feat(capture): bound project cleanup`

---

### Task 6: Capture inspection and exclusive artifact publication

**Files:**
- Create: `src/trafficlab/capture_validation.py`
- Create: `tests/unit/test_capture_validation.py`
- Modify: `src/trafficlab/pcapng.py`
- Modify: `tests/unit/test_pcapng.py`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `tests/unit/test_artifacts.py`

**Interfaces:**
- Produces: immutable `PacketObservation` and `CaptureInspection`,
  `inspect_capture(metadata_path, pcapng_path, *, deadline, clock)`,
  `validate_capture_pair(...)`, and `publish_capture_pair(...)`.
- PCAPNG adds a visitor/packet API that uses the existing streaming parser and
  returns Ethernet header bytes needed for validation without a second parse.

- [ ] **Step 1: Write protocol, direction, and deadline tests**

Construct Ethernet fixtures for outbound IPv4 TCP, inbound IPv4 UDP, inbound
broadcast ARP or UDP, IPv6 TCP/UDP, unrelated addresses, truncated network
headers, unknown EtherType, empty capture, only one direction, and decreasing
or malformed PCAPNG. Require source-MAC direction classification, protocol and
source/destination address counts, nonempty packet totals, and fake-clock expiry
before accepting the next frame. Valid Ethernet with an unsupported or truncated
network payload is counted as `other`, not rejected: real traffic can contain
ARP, discovery, or malformed network-layer data. One-direction captures are
valid; controlled Docker and Internet tests separately require both directions
from their known bidirectional workloads.

- [ ] **Step 2: Write publication failure tests**

Require strict temporary `capture.json`, nonempty PCAPNG, metadata publication
before reference publication, exclusive no-replace publication, cleanup of only
the creator's temporary links, no reusable pair after target failure, and
incomplete `capture.json`-only state treated as invalid on reuse. A complete,
valid existing pair is reusable and never replaced. An incomplete or invalid
pair is classified as failed stage output, removed only by its exact two known
artifact paths, and replaced by the new validated pair. Inject failure between
publications and prove no code reports a reusable pair.

- [ ] **Step 3: Confirm red and implement a single streaming inspection path**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_capture_validation.py \
  tests/unit/test_pcapng.py tests/unit/test_artifacts.py
```

Parse Ethernet and minimal ARP/IPv4/IPv6 headers with `struct` and `ipaddress`;
do not add Scapy. Use `os.link` plus creator-owned temporary names for exclusive
publication, matching the existing similarity artifact pattern. Translate all
parse and publication failures at the capture stage boundary.

- [ ] **Step 4: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_capture_validation.py \
  tests/unit/test_pcapng.py tests/unit/test_artifacts.py
uv run --locked ruff format --check src/trafficlab tests/unit
uv run --locked ruff check src/trafficlab tests/unit
uv run --locked pyright
```

Commit: `feat(capture): validate and publish references`

---

### Task 7: Capture lifecycle orchestrator

**Files:**
- Create: `src/trafficlab/capture.py`
- Create: `tests/unit/test_capture.py`
- Create: `tests/integration/test_capture_pipeline.py`
- Modify: `src/trafficlab/artifacts.py`

**Interfaces:**
- Produces: immutable `CaptureResult` and `capture_experiment(path, *, docker,
  clock, interruption) -> CaptureResult`.
- Consumes Tasks 1–6 without duplicating their policy, command rendering,
  parsing, publication, or cleanup logic.

- [ ] **Step 1: Write the successful command-order test**

Using a scripted fake Docker boundary and real temporary files, require: local
and full preflight; unique project record; Compose file write; `compose create
capture`; start the monotonic total deadline immediately after successful
creation and before another Docker command; `compose start capture`;
container-running plus metadata and nonempty PCAPNG-header readiness; target
start; natural target status;
live capture `SIGINT`; bounded successful flush; shared-deadline validation;
`capture.json` then reference publication; unconditional down. Assert one
monotonic total deadline begins immediately after project creation.

- [ ] **Step 2: Write the complete failure-order suite**

Cover readiness timeout with no target start, natural target nonzero preserved
exactly, workload timeout, user interruption, unexpected capture stop, flush
timeout with capture kill, malformed/empty output, validation deadline, and
cleanup error. Require target kill as the immediate next Docker action after
unexpected capture stop; live capture gets exactly one SIGINT/flush wait;
already-stopped capture gets neither; induced target status remains secondary;
target nonzero remains primary over later failures; target zero allows
flush/validation failure primary; no failed case publishes a reusable pair.

- [ ] **Step 3: Confirm red and implement one explicit state loop**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_capture.py \
  tests/integration/test_capture_pipeline.py
```

Poll target and capture state once per observation, read the clock once, build
one `EventObservation`, and call `choose_event`. Use small named functions for
readiness, workload arbitration, flush, validation, and finalization. A `finally`
block always calls Task 5 cleanup with the last-known inventory and remaining
deadline. Append structured JSONL events to the existing `run.log` boundary.

- [ ] **Step 4: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_capture.py \
  tests/integration/test_capture_pipeline.py tests/integration/test_cleanup_boundary.py
uv run --locked ruff format --check src/trafficlab tests
uv run --locked ruff check src/trafficlab tests
uv run --locked pyright
git diff --check
```

Commit: `feat(capture): orchestrate bounded reference capture`

---

### Task 8: Public capture CLI and installed-entry integration

**Files:**
- Modify: `src/trafficlab/cli.py`
- Modify: `tests/unit/test_package.py`
- Create: `tests/integration/test_capture_cli.py`
- Modify: `architecture/SYSTEM.md`

**Interfaces:**
- Produces: `trafficlab capture EXPERIMENT`; plain preflight is permanent full
  preflight, while only `preflight` accepts `--config-only`.

- [ ] **Step 1: Write CLI routing and error tests first**

Require injected in-process capture receives the exact experiment path, success
prints the reference path and packet count, `TrafficlabError` names `capture`
and corrective action, interruption returns the documented nonzero status, and
unknown options fail without starting capture. Run the installed entry point
with an injected fake `docker` executable that records argv; assert no internal
Python subprocess and no `sudo` command.

- [ ] **Step 2: Confirm red, add the thin command route, and verify**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_package.py \
  tests/integration/test_capture_cli.py tests/integration/test_preflight_cli.py
uv run --locked ruff format --check src/trafficlab/cli.py tests
uv run --locked ruff check src/trafficlab/cli.py tests
uv run --locked pyright
```

Keep all lifecycle behavior in `capture_experiment`; the CLI only parses,
dispatches, prints a short result, and maps expected errors.

Commit: `feat(capture): expose capture CLI`

---

### Task 9: Real Docker fixtures, Internet smoke, and Phase 3 evidence

**Files:**
- Create: `tests/docker/compose.endpoint.json`
- Create: `tests/docker/images/endpoint/Dockerfile`
- Create: `tests/docker/images/endpoint/server.py`
- Create: `tests/docker/images/client/Dockerfile`
- Create: `tests/docker/images/client/client.py`
- Create: `tests/docker/images/no_shell/Dockerfile`
- Create: `tests/docker/test_capture_docker.py`
- Create: `tests/docker/test_capture_failures.py`
- Create: `tests/internet/test_capture_internet.py`
- Modify: `tests/conftest.py`
- Modify: `architecture/ROADMAP.md`
- Modify: `architecture/DEVELOPMENT.md`

**Interfaces:**
- Docker session fixtures fail with an actionable message, rather than skip, if
  explicitly selected without Engine/Compose or required build capability.
- `--internet-url URL` supplies the opt-in HTTPS endpoint.

- [ ] **Step 1: Add deterministic test-only images and topology**

The endpoint listens on TCP and UDP and emits one controlled inbound broadcast.
The direct client sends known payload counts and exits with a configured status.
The no-shell image contains only its executable workload contract. Test overlays
add endpoint without changing the production document, whose services remain
exactly `{capture, target}`. Use unique project names per test.

- [ ] **Step 2: Add marked serial real-Docker tests**

Cover full preflight; readiness before target; TCP/UDP endpoint addresses,
protocols, minimum counts, outbound/inbound/broadcast classifications; no
unrelated project traffic; target zero and exact nonzero; background child
closure; long-running target timeout; capture early exit with target killed next
and within five seconds; ignored SIGINT reaching flush timeout; malformed output;
readiness failure; interruption; and complete removal of labelled containers,
networks, volumes, and orphans after every case. Cleanup assertion failures must
print remaining resource names.

- [ ] **Step 3: Add the opt-in Internet smoke test**

Run a real HTTPS client through `trafficlab capture`; require DNS/TLS success,
target zero, bidirectional nonempty parseable capture, and complete teardown.
Never include this test in deterministic/default CI.

- [ ] **Step 4: Run all locally available mandatory evidence**

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest -q -n auto --dist worksteal \
  -m "not integration and not docker and not internet"
uv run --locked pytest -n auto --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing -m "not docker and not internet"
git diff --check
```

Expected on this host: all commands above pass; coverage is at least 90%.

- [ ] **Step 5: Record, but do not fabricate, external evidence**

On a Docker-capable host run:

```bash
uv run --locked pytest -vv -x -n 0 -m docker
uv run --locked pytest -vv -x -n 0 -m internet --internet-url URL
```

If Docker is unavailable, record the environment limitation in the SDD ledger
and task report, keep Phase 3 `(Current)`, leave external-evidence Roadmap boxes
unchecked, and continue implementing later phases. Mark Phase 3 complete only
after an actual Docker-capable run proves the deterministic topology and an
actual opt-in Internet run proves public access. Never convert explicit Docker
selection into a skip.

- [ ] **Step 6: Commit evidence and Roadmap state**

Check each Phase 3 box only when its implementation and named evidence exist.
Move `(Current)` to Phase 4 only if all Phase 3 Done-when evidence is real;
otherwise retain the marker and add a concise evidence note without changing
the dependency order.

Commit: `test(capture): verify Docker capture lifecycle`

---

## Phase Completion Gate

Before Phase 3 can be called complete:

1. Every task commit has an independent Critical/Important review and all such
   findings are fixed with regression tests.
2. A whole-phase review checks `CAPTURE.md`, `SYSTEM.md`, `TESTING.md`, and every
   Phase 3 Roadmap item against the implementation.
3. The locked Ruff, Pyright, parallel fast, and branch-coverage gates pass with
   at least 90% non-Docker branch coverage.
4. The Docker suite passes serially on a Docker-capable host and proves complete
   project cleanup after every ordinary case.
5. The opt-in Internet smoke passes against a supplied real HTTPS URL.
6. `trafficlab preflight` and `trafficlab capture` work through the installed
   entry point, while `preflight --config-only` remains permanently Docker-free.

The current host cannot satisfy items 4–5 because the Docker CLI is absent.
That is external evidence pending, not permission to weaken, skip, or falsely
complete those requirements.
