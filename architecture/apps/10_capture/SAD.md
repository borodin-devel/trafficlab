# Capture Software Architecture Document

## Role

This application captures traffic produced by one requested target application
tree in a prepared workspace. It publishes a verified capture artifact for a
later consumer and records the execution metadata needed to establish its
lineage.

## Decision Scope

The dedicated workspace is the active capture backend. It provides one
exclusive slot for one target application and all of its descendants. A
host-network backend is documented separately but is not active or selectable
by this application.

## Platform Boundary

This application captures Linux target applications in a Linux-based
environment, with Windows Subsystem for Linux 2 as the primary environment.
Windows applications and native Windows traffic capture are unsupported. Shared
platform and development-toolchain constraints belong to the
[development environment](../../DEVELOPMENT.md).

## Inputs

- One immutable [capture request](../../contracts/00_capture_request/README.md)
  with the target argument vector, working directory, allowed environment,
  completion policy, interactive mode, requested bridge identifiers,
  packet-retention policy, and workspace identity.
- One successful, matching, and fresh
  [preflight readiness package](../../contracts/00_10_capture_readiness/README.md).
- A ready invoker endpoint whose workspace identity and state are revalidated
  after exclusive reservation.

Commands receive no shell interpretation. The target application's result and
the capture application's result are separate outcomes.

Direct invocation requires:

```text
capture --capture-request PATH --readiness STATUS_PATH --output-dir DIR
```

`STATUS_PATH` is the readiness attempt's explicit `artifact-status.toml`.
Managed invocation additionally receives the assigned `--attempt-dir`.

## Configuration

This application's optional configuration file is `10_capture.toml`. Its
settings may select implementation-specific recorder behavior only. It cannot
provide, default, or override request-owned execution or capture policy. Shared
selection, resolution, validation, and `launch.toml` rules belong to
[application configuration](../../CONFIGURATION.md).

Each attempt follows the shared
[startup record](../../CONFIGURATION.md#startup-record) rule. A successful
capture artifact includes that `launch.toml` record.

## Dedicated Workspace Backend

The prepared workspace gives the target tree a dedicated external capture
point. Outbound network access is allowed; inbound access is blocked unless a
request explicitly selects a preflight-approved bridge. Only address families,
DNS modes, network paths, and requested bridges confirmed by preflight may be
used.

Workspace loopback is separately observable and always captured. Therefore,
`localhost` means the workspace itself. Host-local services are unavailable by
default; each required host-local service is exposed only through an explicit
configured bridge.

## Workspace Invoker

The persistent invoker runs as the designated ordinary workspace user. The
capture application submits one request through its user-owned local endpoint;
the invoker rejects a request while the single slot is busy.

For an interactive target, the invoker creates a pseudoterminal inside the
workspace and bridges terminal input, output, resize events, and interrupts.
The target owns the pseudoterminal's foreground process group. An interactive
request without an attached terminal is rejected before target launch.

## Capture Lifecycle

1. Capture validates bounded snapshots of the explicit request and readiness
   package, their detached hashes, binding, freshness, and capabilities.
2. It reserves the sole slot, takes new bounded no-follow snapshots of request
   and readiness, and revalidates their identities, hashes, binding, current
   freshness, workspace identity, and ready state. Expiry or mutation while
   waiting releases the slot without recorder or target launch.
3. The invoker establishes the requested capture points and confirms the
   recorder is ready before it launches the target.
4. The invoker runs the exact target argument vector and applies the requested
   completion and timeout policy.
5. After the target tree ends, the recorder stops cleanly and all capture files
   close.
6. Trafficlab stages the closed recording in a private same-filesystem sibling
   of the absent artifact destination. The attempt records any retained staging
   location.
7. Unprivileged per-capture cleanup restores and verifies a known-ready
   workspace state while the slot remains reserved. On success, Trafficlab
   writes the cleanup outcome and terminal capture metadata into staging,
   closes every package file, validates PCAPNG and complete package contents,
   constructs the manifest, and computes hashes.
8. Only after that cleanup-bound validation succeeds, Trafficlab atomically
   publishes the result under the shared
   [artifact protocol](../../libs/artifact_io/SAD.md).
9. It atomically publishes detached status and then releases the
   verified-ready slot. If status publication fails after the artifact rename,
   the destination is a retained orphan, the capture is unsuccessful, and the
   still-ready slot is released after recording that outcome.

## Output and Publication

Every successful capture publishes one canonical, closed, validated packet
artifact at `raw/target.pcapng`. Execution, network, statistics, validation,
request/readiness binding, and log metadata accompany it. The package manifest
hashes every non-manifest member and excludes itself; detached
`artifact-status.toml` binds the manifest and frozen `launch.toml`. Later
applications consume only the canonical packet artifact.

The [20 convert application](../20_convert/README.md) consumes this artifact
under the [capture directions contract](../../contracts/10_20_capture_directions/README.md).

Incomplete, unclosed, unhashed, failed-validation, or pre-cleanup output is
never published as successful. The artifact destination remains absent until
cleanup and complete-result validation succeed.

## Data Handling

Packet retention is always explicit in the request: either a positive byte
prefix or full captured packets. Capture supplies no implicit retention
default. Metadata records the requested and actual packet-length policy,
capture points, route and DNS facts, workspace identity, and output lineage.

## Failure and Workspace State

The workspace is `ready`, `busy`, or `unhealthy`. A failed target does not by
itself make capture invalid if the recorder closes and the canonical result
passes validation and cleanup establishes a known-ready state. If unprivileged
cleanup fails or cannot verify that state, capture retains diagnostics in the
attempt directory, publishes no successful artifact or status, marks the
workspace unhealthy, and refuses it until manual verification, setup, or
rollback resolves the condition.

A later artifact/status publication failure does not undo verified cleanup or
make the workspace unhealthy. It retains an orphan or staging diagnostics,
publishes no success, and releases the verified-ready slot; retry uses a new
destination under the shared artifact rules.

## Security and Privilege Boundary

Normal capture execution does not create or remove the persistent workspace,
change global configuration, or request privilege elevation. The invoker and
target tree run as the ordinary workspace user. Manual setup and rollback
behavior belongs to the [workspace script architecture](../../scripts/README.md).

## Deferred Decisions

This document does not select a Linux distribution, packet recorder, exact
network topology, packet filter, capture-result metadata schema, or
implementation-specific recorder settings. The inactive host-network
alternative is described in the network-workspace design material. Request and
readiness interfaces are owned by their contracts; generic artifact publication
is owned by the artifact I/O library. The capture-result metadata schema must
be defined here before implementation.

## Reading

Follow the [architecture governance](../../README.md) before changing this
document. Read the [00 preflight application](../00_preflight/README.md) and
the [workspace script architecture](../../scripts/README.md) before changing
their owned facts.

## Cross-Cutting Architecture

Lifecycle and policy decisions form a deterministic core; invoker I/O,
recorder processes, pseudoterminals, signals, files, and clocks form the shell.
Packet and log buffering are bounded. Security review covers target arguments,
environment, paths, local endpoint authentication, terminal control, and cleanup.
Primary risks are recorder loss, target escape, interruption, and unknown
workspace state; each fails publication or marks the workspace unhealthy.
