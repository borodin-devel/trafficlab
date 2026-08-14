# Capture Process Ownership Design

## Problem

The current Docker design starts an idle target container and launches the real
workload later with `docker compose exec`. Trafficlab directly owns only the
local `exec` client. A workload that starts background children can outlive that
client, so target completion and workload timeout do not define a reliable end
to the capture window.

The MVP needs one Docker-owned workload lifetime, exact target status, bounded
failure handling, and unconditional cleanup. It must not add a process manager,
shell protocol, PID-file protocol, service, or security layer.

## Topology

One normal capture uses exactly two services:

```text
Docker Compose default bridge
  capture service
    owns the network namespace and eth0
    runs the capture tool as its service process
  target service
    network_mode: service:capture
    runs the configured workload as its service command
```

The target and capture services therefore observe the same `eth0`, but Docker
manages their process lifetimes separately. The capture service remains alive
when the target stops, so Trafficlab can finish and flush the PCAPNG after the
workload window closes.

The target uses `init: true` and runs the configured argument vector directly.
The tiny container init may be PID 1; the workload is still the container's
single supervised service command. There is no idle command, POSIX-shell
requirement, wrapper, `docker compose exec`, or Trafficlab PID file.

Target environment, working directory, mounts, and image remain experiment
settings. Because the capture service owns the shared network namespace,
network attachment and any explicitly configured published ports belong to
that service. The target retains normal Docker-provided Internet access.

## Lifecycle

Trafficlab starts the total-run deadline when it creates the Compose project.
Every later wait, parser or validator step, and cleanup action is capped by the
remaining total-run budget. It performs one capture in this order:

1. Validate the experiment and complete preflight.
2. Create a uniquely named Compose project without starting the target.
3. Start the capture service, read and validate the shared `eth0` MAC, start
   non-promiscuous capture, and wait for its readiness signal.
4. Start the target service only after capture is ready.
5. Use one event arbiter to monitor target, capture, interruption, workload
   timeout, and total-run timeout together. A natural target stop closes the
   workload window. An unexpected capture stop while target is running makes
   target kill the next orchestration action.
6. After a natural target stop, read its exit status. Signal capture with
   `SIGINT` only if it remains alive, then wait up to the flush timeout and
   remaining total-run budget. If capture already exited unexpectedly, reject
   its output without a signal or flush wait.
7. Pass the monotonic total-run deadline into PCAPNG parsing and validation.
   Check it before work starts and after every frame; expiry aborts processing.
8. Publish the validated artifact pair only for a successful target run.
9. Enter cleanup unconditionally. If budget remains, run Compose removal within
   it. If no budget remains, make no blocking Docker call. On cleanup expiry,
   terminate the local Compose process and make no further Docker query. Report
   the last known project inventory as possibly remaining.

A normal target exit closes its process namespace. Background descendants
cannot remain as a hidden workload after the target container stops. The
capture network namespace stays available because the capture service owns it.

## Failure Behavior

At each wait boundary, the event arbiter reads the monotonic clock once, collects
all events visible in that observation, and applies this fixed priority:

1. user interruption;
2. natural target stop;
3. unexpected capture stop;
4. stage-specific timeout, such as workload or flush timeout;
5. total-run timeout.

The chosen event is processed before another wait or action. Once a primary
failure exists, no later failure replaces it.

- **Readiness failure:** do not start the target; retain logs and clean up.
- **Target nonzero exit:** finish and validate capture when possible, preserve
  it under a diagnostic name, return the target status as the primary error,
  and do not publish a reusable reference pair.
- **Workload timeout:** immediately kill the entire target container, then
  attempt the normal bounded capture flush and clean up.
- **Capture failure:** detect the unexpected capture exit while monitoring both
  services, immediately kill the target container, reject the capture, retain
  logs, and clean up without sending `SIGINT` to the stopped capture process.
- **Flush timeout:** kill the capture container, reject the incomplete output,
  and clean up.
- **Total-run timeout:** make it primary when no more specific event or earlier
  primary failure exists; kill any running target, stop waiting, and enter
  bounded cleanup.
- **User interruption:** kill the target container, attempt one bounded capture
  flush only if capture remains alive, and clean up.
- **Cleanup failure:** report it after the primary error, or as the primary
  error if the run otherwise succeeded. Cleanup remains safe to repeat.

Trafficlab does not try to gracefully stop a timed-out target. At that point the
run has already failed; killing the whole container is simpler and prevents a
signal-ignoring workload or child from extending the capture indefinitely.

After arbitration, error precedence follows the event that caused termination:

1. When the arbiter selects natural target stop, a nonzero exit is primary;
   later flush, validation, and cleanup failures are secondary diagnostics.
2. Workload timeout, unexpected capture exit, or user interruption is primary
   when it causes Trafficlab to kill target. The resulting target exit status is
   induced and never replaces that cause.
3. After a natural successful target exit, capture flush or validation failure
   is primary.
4. Total-run expiry during flush, parsing, or validation is primary after natural
   target success and secondary after natural target nonzero.
5. Cleanup failure is primary only when no earlier operation failed.

The fixed event order means a natural target stop wins when target stop, capture
stop, and a deadline are visible in the same observation. A nonzero target status
remains primary. After target zero, an unexpected capture stop is primary; if
capture remains healthy, a later stage timeout wins over total-run timeout in
the same observation. Every secondary failure and exit status is still recorded
in `run.log`.

Trafficlab records the project name and observed resource names or IDs as the
lifecycle progresses. Cleanup uses this last known inventory. If its remaining
budget is zero, the cleanup branch records a cleanup timeout without launching a
Docker command. If a running cleanup reaches the deadline, Trafficlab terminates
the local CLI, performs no post-deadline Docker query, and reports the inventory
as possibly remaining.

## Required Evidence

Unit and integration evidence covers:

- table-driven event-arbiter cases for every simultaneous pair in the fixed
  priority order;
- a natural target stop, capture stop, and total-run expiry in one observation,
  with natural target status winning;
- a fake monotonic clock making multi-frame parsing cross its deadline and stop
  before the next frame is accepted;
- zero-budget cleanup making no Docker call and reporting the last known
  inventory;
- a live capture receiving `SIGINT` while an already-stopped capture does not;
- a controlled hanging cleanup process stopping at the remaining total-run
  deadline without a later Docker query and reporting last known resources as
  possibly remaining;

Docker integration tests cover:

- readiness completing before the target service command starts;
- a successful target producing a valid capture;
- exact propagation of a nonzero target status with diagnostic capture;
- a target with background children leaving no workload process after normal
  exit;
- a timed-out target and its children being killed;
- an unexpected capture exit stopping a still-running target before the
  workload timeout;
- target kill being the next orchestration action after unexpected capture exit,
  with target stopped within five seconds independently of workload timeout;
- a natural nonzero target status remaining primary while a kill-induced status
  remains secondary to timeout, capture failure, or interruption;
- a capture process that ignores `SIGINT` being killed at the flush timeout,
  with incomplete output rejected;
- malformed output still receiving complete project cleanup;
- user interruption performing bounded flush and cleanup;
- real Internet access in the existing opt-in smoke test;
- no project container, network, volume, or orphan remaining after each Docker
  case; the controlled cleanup-timeout fixture instead reports its last known
  inventory as possibly remaining;
- the rendered production service set being exactly `{capture, target}` while
  the controlled endpoint remains only a test fixture;
- normal launch through Docker access without `sudo`;
- no dependency on a target shell, idle command, wrapper, or Compose `exec`.

The deterministic packet test may keep its existing controlled endpoint
fixture. Production capture still uses only the target and capture services.

## Documentation Scope

Implementation updates only:

- `architecture/CAPTURE.md` for topology, target contract, lifecycle, and
  failure behavior;
- `architecture/SYSTEM.md` for capture orchestration and command ownership;
- `architecture/TESTING.md` for Docker lifecycle integration cases;
- `architecture/ROADMAP.md` for the corresponding implementation tasks.

No command, experiment setting, persistent service, artifact schema, traffic
model, similarity method, genetic behavior, host-network mutation, security
feature, or additional production container is added.
