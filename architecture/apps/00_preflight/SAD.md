# Preflight Software Architecture Document

## Role

This application assesses whether the current system and prepared capture
workspace are ready for a requested traffic capture. It publishes a readiness
decision for the capture application.

## Boundary

### Input

- One immutable
  [capture-request.toml](../../contracts/00_capture_request/README.md), including
  exact workspace/manifest identity, target argument vector, completion,
  interactive, bridge, environment, packet-retention, and readiness-lifetime
  policy.

### Output

- A [capture-readiness package](../../contracts/00_10_capture_readiness/README.md)
  with the exact request SHA-256, matching workspace/manifest identity,
  creation time, findings, supported capabilities, and blockers for capture.

### Assessment Scope

- Workspace identity, ownership, manifest consistency, invoker health,
  exclusivity, and ready/busy/unhealthy state.
- Unprivileged access required by the selected capture backend and recorder.
- System requirements, installed required software, resources, and relevant
  existing configuration.
- Supported network address families, DNS/network reachability, and every
  requested host-service bridge.
- Packet-length policy and available storage.

### Invariants

- The assessment is read-only.
- The readiness decision distinguishes blockers from successful readiness.
- It does not invoke operator scripts, install software, repair the workspace,
  change configuration, create capture resources, or request elevation.

## Configuration

This application's optional configuration file is `00_preflight.toml`. Its
settings belong only to preflight; preflight does not read `10_capture`
configuration. Shared selection, resolution, validation, and `launch.toml`
rules belong to [application configuration](../../CONFIGURATION.md).

Each attempt follows the shared
[startup record](../../CONFIGURATION.md#startup-record) rule. A successful
readiness package includes a frozen `launch.toml`, non-self-referential
manifest, and detached successful status.

## Deferred Decisions

This document does not select required programs, recorder implementation,
optional probe-specific configuration, or remediation mechanisms. Request and
readiness fields/formats belong to their contracts and are not deferred here.

## Platform Reference

Shared platform and development-toolchain constraints belong to the
[development environment](../../DEVELOPMENT.md). This application owns the
actual readiness checks for a requested capture.

## Reading

Follow the [architecture governance](../../README.md) before changing this
document. A successful decision is required by the
[10 capture application](../10_capture/README.md).

## Cross-Cutting Architecture

The pure assessment core accepts typed requests and observations; Linux probes,
files, clocks, and logging are injected shell boundaries. It uses bounded
observations, structured diagnostics, no secrets, and no mutation. Risks are
stale observations, request substitution, and an incomplete capability
assessment; all block rather than claim readiness. Deployment is one Python
3.12 command in normal user context.
