# Capture Workspace Architecture Design

## Goal

Replace the `10_capture` boundary stub with a complete architecture for
capturing one target application tree without requiring `sudo`, root execution,
or privileged tests during normal Trafficlab use.

Privileged setup and teardown are manual operator actions. The active capture
architecture is a dedicated network workspace; host-network capture is a
documented, inactive alternative.

## Scope

The design covers the capture application, its read-only preflight dependency,
the manually run workspace scripts, the default dedicated-workspace backend,
the host-network alternative, capture artifacts, lifecycle, privilege boundary,
and unprivileged script tests.

It does not select a Linux distribution, a specific packet-recording program,
an exact topology, BPF program, packet filter, artifact schema, command-line
interface, or configuration schema. Those choices belong to later owner
documents and implementations.

## Decision Precedence

The user's current approved decisions define this design and take precedence
over the prior architecture draft. The draft is reference material only: an
idea from it is adopted only when this design or a later authoritative
architecture document explicitly chooses it.

## Decisions

### Privilege Model

The only privileged operations are manual workspace administration:

- `scripts/setup_capture_workspace.sh` prepares the workspace;
- `scripts/rollback_capture_workspace.sh` tears it down; and
- `scripts/workspace_orchestration.sh` is a manual operator entry point that
  may invoke those privileged operations.

Trafficlab commands, the capture application, the workspace invoker, target
applications, child processes, unit tests, integration tests, and script tests
never invoke `sudo` or run as root. Setup starts the invoker as the designated
ordinary workspace user; the invoker starts each target application tree with
that same identity.

Temporary and reboot-cleared changes are preferred. If setup finds that a
durable configuration change is genuinely necessary, it must show the current
and proposed values before mutation, run
`scripts/backup_system_configuration.sh`, record the backup in the workspace
manifest, and make rollback restore the recorded original value.

### Workspace Invoker

The prepared workspace contains one persistent, non-root invoker. The normal
Trafficlab capture command contacts it through a user-owned local endpoint and
submits one capture request. The invoker enforces exclusive ownership: one
target application tree, including its descendants, can be captured at a time.

For interactive targets, the invoker creates a pseudoterminal in the workspace
and bridges it to the caller's terminal. It forwards terminal input, output,
resize events, and interrupts; the target owns the pseudoterminal's foreground
process group. An interactive request without an attached terminal is rejected
before starting the target. Non-interactive requests use ordinary configured
streams.

### Dedicated Workspace: Active Backend

The default backend gives the target application tree a dedicated network
workspace and a dedicated external capture point. The application may make
normal outbound connections; inbound access is blocked unless a configuration
explicitly declares it.

Workspace loopback is always a separate, observable capture point. Thus,
`localhost` means the workspace itself, and loopback traffic is captured.
Host-local services are unreachable by default. A requested host-local service
is exposed only through an explicit configured bridge; preflight verifies each
requested bridge before capture.

The backend captures bidirectional traffic and distinguishes external traffic
from workspace-internal loopback traffic in metadata. It supports only address
families, DNS modes, network paths, and bridges confirmed by preflight.

### Host-Network Backend: Documented Alternative

Host-network capture leaves the target on the host network so that host
`localhost`, addresses, VPNs, and existing services retain their normal
behavior. It is not the active backend.

It requires a pre-installed per-process traffic attribution mechanism, such as
a delegated process group and kernel packet/socket collector, to exclude
unrelated host traffic. It must capture both physical and loopback paths and
prove that only flows belonging to the target tree appear in the result. This
backend is a later implementation decision because its correctness and privacy
boundary are more complex than the dedicated workspace's interface boundary.

## Capture Application

### Inputs

`10_capture` requires:

- a successful read-only preflight readiness decision;
- a target command as an argument vector, a working directory, and an allowed
  environment;
- capture settings, including duration policy, interactive mode, requested
  network bridges, and packet-length policy; and
- a ready workspace identity and invoker endpoint.

Commands receive no shell interpretation. Target exit status and capture-stage
status are independent results.

### Lifecycle

1. Preflight confirms the prepared workspace is ready and validates requested
   configuration without changing it.
2. Capture reserves the sole workspace slot and creates a private temporary
   result directory.
3. The invoker establishes configured capture points and confirms the recorder
   is ready before launching the target.
4. The invoker runs the target application tree, monitors its descendants, and
   applies the configured exit or timeout policy.
5. After the target tree ends, the recorder is stopped cleanly and all capture
   files are closed.
6. Trafficlab builds and validates one canonical capture, hashes the complete
   result, and publishes it atomically.
7. The slot returns to ready only after unprivileged per-capture cleanup. If
   that state cannot be established, it becomes unhealthy and normal capture
   refuses to use it until manual verification, setup, or rollback occurs.

No normal capture run creates or removes the persistent workspace, changes
global configuration, or escalates privilege.

### Artifacts and Data Handling

Every successful capture publishes one canonical, closed, validated Packet
Capture Next Generation file at `raw/target.pcapng`, plus execution, network,
statistics, validation, and log metadata. Later pipeline applications consume
only this canonical packet artifact.

Capture records the first 256 bytes of each packet by default. Full packet
payload capture is an explicit configuration choice. Requested and actual
packet-length policy, capture points, route/DNS facts, and workspace identity
are recorded as lineage metadata. Incomplete, unclosed, unhashed, or failed
validation output is never published as a successful result.

## Preflight

`00_preflight` remains read-only. For capture it checks:

- workspace existence, ownership, invoker health, exclusivity, and state;
- unprivileged access needed by the selected backend and recorder;
- required software, system resources, and relevant existing configuration;
- supported address families, DNS/network reachability, and requested bridges;
- packet-length policy and available storage; and
- whether the workspace is ready, busy, or unhealthy.

It reports findings and blockers without repairing the environment, invoking
privileged scripts, installing software, or modifying configuration.

## Operator Scripts

The script source directory is `scripts/`. Their architecture owner documents
are in `architecture/scripts/`:

- `setup_capture_workspace.sh` plans, displays, and applies setup; it starts
  the normal-user invoker and writes the workspace manifest.
- `rollback_capture_workspace.sh` reads the manifest, reverses only resources
  belonging to the workspace, restores any recorded durable configuration, and
  is idempotent.
- `verify_capture_workspace.sh` is read-only and reports readiness, ownership,
  invoker health, manifest consistency, and detectable drift.
- `backup_system_configuration.sh` snapshots only durable values named by an
  approved setup plan and records their restoration instructions.
- `workspace_orchestration.sh` is manually invoked and may sequence backup,
  setup, verify, and rollback after displaying its plan and required privilege.

Scripts reject ambiguous workspace identities and paths, use argument vectors,
write a durable manifest before mutable work, and never perform broad global
cleanup. They retain diagnostics on failure.

## Documentation Structure

`architecture/apps/10_capture/README.md` becomes the owner document for the
active capture boundary and default backend. `architecture/apps/00_preflight/`
is updated only for its capture-readiness responsibility. The root architecture
README adds the `scripts/` documentation category and the reading order needed
to reach it.

`architecture/scripts/README.md` indexes one owner document per script:

```text
architecture/scripts/
├── README.md
├── setup_capture_workspace.md
├── rollback_capture_workspace.md
├── verify_capture_workspace.md
├── backup_system_configuration.md
└── workspace_orchestration.md
```

`docs/network_workspaces/` contains detailed, non-authoritative backend design
material that links to the owning capture architecture:

```text
docs/network_workspaces/
├── README.md
├── dedicated_workspace.md
└── host_network.md
```

The dedicated document describes the active design. The host-network document
describes its later requirements and explicitly does not enable it.

## Testing

All normal tests run without root and never invoke `sudo`. Tests for scripts
live in `tests/scripts/` when the scripts are implemented. They use temporary
directories, recorded manifests, and fake command tools to test planning,
validation, failure handling, idempotent rollback planning, and durable-change
backup/restore planning.

No unit, contract, integration, or system test may create a real workspace,
alter the host network, attach a kernel collector, change system configuration,
or require privileged access. A manually performed operator verification is
not part of the automated test suite.

## Validation

The documentation change is complete when every new owner document has one
clear responsibility, all relative links resolve, root authority remains in
`architecture/`, active documents state that the dedicated workspace is the
default, and no test requirement depends on `sudo` or root.
