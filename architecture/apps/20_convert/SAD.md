# Convert Software Architecture Document

## Role

This application converts one complete PCAPNG capture into target, uplink, and
downlink PCAPNG artifacts for later mathematical similarity comparison.

## Inputs

The application accepts either a pipeline capture or an external PCAPNG file.
A pipeline capture is the canonical `raw/target.pcapng` from the
[10 capture application](../10_capture/README.md). An external file requires a
source description, source hash, and app-side reference profile.

The reference profile identifies the observation boundary with a capture
interface and one or more app-side local IP addresses. It includes local MAC
addresses when available. The profile's configuration serialization is a later
decision.

## Configuration

This application's optional configuration file is `20_convert.toml`. Its
settings belong only to conversion. Shared selection, resolution, validation,
and `launch.toml` rules belong to
[application configuration](../../CONFIGURATION.md).

Each attempt follows the shared
[startup record](../../CONFIGURATION.md#startup-record) rule. A successful
conversion artifact includes that `launch.toml` record. The reference profile's
field serialization remains a separate decision.

## Direction Model

Direction is determined only at the app-side observation boundary. A packet
leaving that side is uplink; a packet arriving at that side is downlink. The
application does not infer direction from client/server roles, ports, or
arbitrary address guesses.

Only confidently classified IP packets enter directional artifacts. Ambiguous,
unknown, and non-IP packets are excluded and recorded with counts and reasons.

## Output

The [capture directions contract](../../contracts/10_20_capture_directions/README.md)
owns the fixed published package:

```text
manifest.json
target.pcapng
uplink.pcapng
downlink.pcapng
direction-report.json
launch.toml
```

`target.pcapng` is the unchanged complete input. The directional files retain
each selected packet's original bytes, timestamps, lengths, and interface
metadata; only nonmatching packets are removed.

## Publication

The manifest records source provenance, exact membership, and hashes for every
non-manifest member; it excludes itself. The direction report records the
reference profile, classification counts, excluded packet counts, and exclusion
reasons. The complete package is validated, hashed, and published atomically;
detached `artifact-status.toml` then binds its manifest and frozen launch
record. A partial or orphaned package is never successful.

## Wi-Fi-like Terminology

Uplink and downlink are Wi-Fi-like analytical labels for later simulation and
comparison. They do not represent radio frames, channels, signal strength,
wireless retries, or other physical-layer observations.

## Scope Boundary

This application does not build normalized datasets, tables, flows, sessions,
CSV, JSON exports, or model features. Future mathematical comparison owns how
target, uplink, and downlink artifacts are measured against generated traffic.

## Testing

Direction classification and PCAPNG filtering are deterministic unprivileged
functional-core behavior. Tests use small fixtures to verify exact packet
preservation, direction assignment, exclusions, reports, hashes, and atomic
publication. No test requires a real capture workspace or elevated access.

## Reading

Follow the [architecture governance](../../README.md) and
[development environment](../../DEVELOPMENT.md) before changing this document.
Read the [capture application](../10_capture/README.md) and the
[capture directions contract](../../contracts/10_20_capture_directions/README.md)
before changing their owned facts.

## Cross-Cutting Architecture

Profile validation, direction classification, counts, and report construction
are pure. PCAPNG reads/writes, hashes, and atomic publication are injected
shells. Processing streams packets with bounded memory and summary logging.
Untrusted input, path traversal, unsupported link metadata, and ambiguous
identity are security/error boundaries. The application runs offline without elevation.
