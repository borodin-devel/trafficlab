# Docker Capture Environment

## Goal

Capture every Ethernet frame crossing the target container's main Docker
interface, `eth0`, during the workload window while leaving namespace creation,
Internet access, DNS, NAT, and teardown to Docker. Loopback and additional
interfaces are outside the MVP scope. Capture runs with promiscuous mode
disabled. Normal use requires access to the Docker daemon, not a
Trafficlab-owned sudo script.

## Topology

```text
Docker Compose default bridge (Docker-provided Internet, DNS, and NAT)
  capture service
    owns the network namespace and eth0
    capture tool is the service process and writes a temporary PCAPNG
  target service
    network_mode: service:capture
    configured workload argv is the service command
```

The target joins the capture service's network namespace, so both services
observe the same `eth0` without promiscuously capturing unrelated host or
neighboring-container unicast traffic. Ordinary bridge broadcasts, including
ARP, may still reach that interface. Capture remains alive after target stops
and can flush the completed workload window. This is interface-level capture:
another process in the shared network namespace could also produce a captured
frame. Trafficlab does not claim process-level attribution.

The capture service receives the Docker capabilities required by the capture
tool, normally `NET_RAW` and `NET_ADMIN`; these are Compose settings, not a
project security framework. Network attachment and any explicitly configured
published ports belong to capture because it owns the shared network namespace.

The target uses `init: true` and runs the configured argument vector directly.
The tiny init may be PID 1; the workload remains the container's single
supervised service command. The target image needs only the configured workload
and its ordinary runtime dependencies. The workload uses the configured
environment, working directory, mounts, and the image's normal user unless the
image explicitly says otherwise.

## Preflight

Before capture, Trafficlab checks:

1. `docker info` can reach a functioning daemon.
2. `docker compose version` is available.
3. Target and capture images exist locally or can be pulled.
4. The rendered Compose configuration applies the target image, argument vector,
   environment, working directory, mounts, `init: true`, and namespace sharing
   without shell evaluation.
5. The capture image can read `/sys/class/net/eth0/address`, disable promiscuous
   capture, and write PCAPNG.
6. Configured host mount sources exist and container destinations are absolute.
7. The run directory is writable and has the configured minimum free space.
8. A bounded probe container resolves DNS and reaches the configured network
   probe endpoint through the normal Compose network.
9. Workload, readiness, flush, and total timeouts are positive and ordered.

Preflight creates no persistent Trafficlab environment. Probe resources use a
unique temporary Compose project and are removed before the check returns.

## Reproducible capture environment

The capture Dockerfile uses the approved Debian tag plus an exact sha256 digest
in `FROM`. Its `apt` sources read one dated
[Debian Snapshot](https://snapshot.debian.org/) archive state, and every
directly installed Dockerfile package has an exact Debian version.

`docker/capture/image-lock.json` is the small checked canonical record of the
base digest, snapshot timestamp, direct package versions, capture-tool version,
and expected resolved capture-image content ID. A successful build must equal
that checked expected content ID; merely recording a newly resolved ID is
insufficient. This follows Docker's
[digest-pinning guidance](https://docs.docker.com/build/building/best-practices/)
when image identity must be reproducible.

Preflight records target and capture references with their resolved content
IDs. Capture reuse rejects any mismatch in the exact capture-reuse fields
defined by the shared [stage-compatibility table](SYSTEM.md#stage-compatibility).
Unavailable snapshot, package, or image inputs fail rather than silently
updating.

## Capture lifecycle

For one capture, Trafficlab performs these steps in order. The configured
total-run deadline starts when the Compose project is created. Every later wait,
parser or validator step, and cleanup action is capped by its stage timeout where
applicable and by the remaining total-run budget.

1. Parse and validate the experiment.
2. Derive a unique Compose project name from the run identity plus a random
   suffix; record it in `run.log`.
3. Create the Compose project without starting target, start the total-run
   deadline, then start capture as the network-namespace owner with a bind mount
   for temporary outputs.
4. Require `eth0`, read and normalize `/sys/class/net/eth0/address`, reject a
   missing, zero, multicast, or malformed MAC, start non-promiscuous Ethernet
   capture, and write temporary `capture.json` containing only
   `interface: "eth0"` and the normalized `target_mac`. The capture command must
   emit packet data as Enhanced Packet Blocks; Simple Packet Blocks and obsolete
   Packet Blocks are not accepted because skipping them would silently lose
   traffic.
5. Poll capture state and its readiness signal until ready or the readiness
   timeout expires.
6. Start target only after readiness. Its configured argument vector is the
   service command under `init: true`; no shell or launcher protocol is used.
7. One event arbiter monitors target, capture, user interruption, the current
   stage-specific timeout, and total-run timeout. A natural target stop closes
   the workload window. An unexpected capture stop while target is still running
   makes target kill the next orchestration action.
8. After a natural target stop, read its exit status. Send `SIGINT` and wait for
   bounded flush only if capture remains alive. If capture already exited
   unexpectedly, reject its output without a signal or flush wait.
9. Before publication, require successful capture exit, a nonempty Ethernet
   PCAPNG, and valid temporary `capture.json` and PCAPNG files. Parse every frame
   using the target MAC. Pass the monotonic total-run deadline into parsing and
   validation; check it before work starts and after every frame. Expiry aborts
   before another frame is accepted.
10. After target success, exclusively publish `capture.json` first, then
    `reference.pcapng`, and fsync the containing directory after both links.
    Stage reuse requires both files to be present and valid. A target failure may
    retain validated output only as diagnostic data, not as a reusable reference
    pair.
11. Enter cleanup unconditionally using the last known project resource
    inventory. With positive remaining budget, run
    `docker compose down --volumes --remove-orphans` within it. With zero budget,
    make no blocking Docker call and record cleanup timeout. If a running cleanup
    expires, terminate the local Compose CLI and make no further Docker query.
    Report the last known inventory as possibly remaining.

When the target container stops, its process namespace closes, so a background
descendant cannot remain as a hidden workload. Capture and the shared network
namespace remain available for flushing because capture owns that namespace.

The captured target may make normal Internet requests through Docker's default
bridge. Trafficlab does not publish ports on the shared network namespace unless
an experiment explicitly needs an inbound service; configured mappings belong
to capture as the namespace owner.

## Reliability behavior

At each wait boundary, the event arbiter reads the monotonic clock once, collects
all events visible in that observation, and applies this fixed priority:

1. user interruption;
2. natural target stop;
3. unexpected capture stop;
4. stage-specific timeout, such as workload or flush timeout;
5. total-run timeout.

The selected event is processed before another wait or orchestration action.
Once a primary failure exists, no later failure replaces it.

Each listed outcome records the canonical failure fields from
[Failure policy](SYSTEM.md#failure-policy), including `affected_evidence` and
`evidence_state`. Readiness, target, capture, flush, and timeout failures record
the capture pair state. Metadata and malformed-output failures record the
diagnostic pair or `not_published`. Cleanup failures record the
`possibly_remaining` project inventory. A successful validated capture records
the preserved reusable pair. These records describe the existing outcomes; they
introduce no event, timeout, or lifecycle branch.

- **Readiness timeout:** target never starts; remove the project and report
  capture logs.
- **Target failure:** if capture remains alive, attempt the bounded flush and
  then validate any resulting closed PCAPNG. Preserve valid output for diagnosis,
  do not publish a reusable pair, and return the exact target status as the
  primary error. If capture already exited unexpectedly, reject its output
  directly.
- **Workload timeout:** immediately kill the entire target container, attempt
  the bounded capture flush only while capture remains alive, and clean up. The
  failed run gets no additional graceful-stop protocol that could extend the
  timeout.
- **Capture failure:** immediately kill the target container, retain logs,
  reject the capture, and clean up. Target kill is the next orchestration action;
  Trafficlab sends no `SIGINT` and performs no flush wait because capture is
  already stopped.
- **Flush timeout:** kill the capture container, reject the incomplete output,
  and clean up.
- **Total-run timeout:** make it primary only when no higher-priority event or
  earlier primary failure exists; kill any running target, stop work, and enter
  cleanup.
- **Empty or malformed output:** keep metadata and PCAPNG under the stable
  `diagnostic-capture.json` and `diagnostic-reference.pcapng` names as
  applicable; do not publish `reference.pcapng`, and fail validation.
- **Invalid interface metadata:** do not publish either capture artifact; report
  the missing `eth0` or invalid MAC and clean up.
- **User interruption:** kill the target container, attempt one bounded capture
  flush only if capture remains alive, then clean up.
- **Stale resources:** unique project names prevent collisions. A capture retry
  may remove only its recorded project using bounded cleanup before relaunch;
  validated reuse does not launch Docker. `genetic.resume` applies only to the
  fit checkpoint and is not a capture control.
- **Cleanup failure:** report it after the primary failure, or as the failure if
  the run otherwise succeeded. Cleanup is bounded by the remaining total-run
  budget, reports last known inventory as possibly remaining, and is safe to
  repeat.

After event arbitration, primary error precedence follows the event that caused
termination:

1. When the arbiter selects natural target stop, a nonzero exit is primary.
   Later flush, validation, and cleanup failures are secondary.
2. Workload timeout, unexpected capture exit, or user interruption is primary
   when it causes Trafficlab to kill target. The induced target exit status is
   secondary.
3. After a natural successful target exit, capture flush or validation failure
   is primary.
4. Total-run expiry during flush, parsing, or validation is primary after natural
   target success and secondary after natural target nonzero.
5. Cleanup failure is primary only when no earlier operation failed.

The fixed order means natural target stop wins when target stop, capture stop,
and a deadline are visible in the same observation. After target zero,
unexpected capture stop is primary; when capture remains healthy, a
stage-specific timeout wins a same-observation race with total-run timeout.
Every secondary failure and exit status is recorded in `run.log`.

Trafficlab records the project name and observed resource names or IDs as the
lifecycle progresses. Cleanup uses this last known inventory. With zero budget,
the cleanup branch records timeout without launching Docker. When a running
cleanup expires, Trafficlab terminates the local CLI, performs no post-deadline
Docker query, and reports the inventory as possibly remaining.
Cleanup reserves the final one second of positive total-run budget for
terminating, killing when necessary, and reaping its local Compose CLI. When
less budget remains, it starts this local stop sequence immediately instead of
using the whole remainder for the ordinary cleanup wait.

Trafficlab never edits host routes, forwarding flags, firewall rules, network
namespaces, DNS files, users, groups, or sudo configuration. Consequently it
needs no backup manifest or host rollback procedure.

A crash between the two publication renames may leave `capture.json` without
`reference.pcapng`. This is not a reusable capture. A later capture retry or
strict reuse check validates the pair and safely replaces the incomplete stage.

## Integration-test topology

The deterministic Docker integration test uses the production topology: capture
owns the default-bridge network namespace and target joins with
`network_mode: service:capture`. A target client contacts the existing
controlled endpoint fixture on the Compose bridge, producing known TCP or UDP
traffic. Tests cover readiness before direct service-command launch, exact target
status, normal and timed-out child cleanup, bounded capture flush, and complete
project teardown. A target fixture without a shell or idle command proves that
no wrapper, PID file, or Compose `exec` dependency exists.

Reliability cases also cover prompt capture-failure detection before workload
timeout, natural versus kill-induced target status, a capture process that
ignores `SIGINT`, malformed-output cleanup, and a controlled hanging-cleanup
fixture stopped by the remaining total-run deadline. The rendered production
service set must be exactly `{capture, target}`; the endpoint remains only a test
fixture. Normal launch invokes Docker without `sudo`.

Focused evidence covers every simultaneous event pair, a target/capture/total
triple observation, a fake-clock parser deadline after every frame, live versus
already-stopped capture signalling, zero-budget cleanup, and no Docker query
after cleanup expiry. After unexpected capture exit, target kill must be the next
command and target must stop within five seconds independently of workload
timeout.

A separate opt-in smoke test makes an external request to prove real Internet
access without making normal CI depend on the public Internet.

## References

- [Docker Compose services and `network_mode`](https://docs.docker.com/reference/compose-file/services/)
- [Docker Compose networking](https://docs.docker.com/compose/how-tos/networking/)
- [IETF PCAPNG format, active work in progress](https://datatracker.ietf.org/doc/draft-ietf-opsawg-pcapng/)
