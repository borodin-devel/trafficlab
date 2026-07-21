# Capture Directions Contract Software Architecture Document

## Purpose

This contract owns the directional PCAPNG package handed from capture to
conversion and published by conversion for later comparison.

## Inputs

For pipeline input, conversion receives the canonical `raw/target.pcapng` and
capture metadata from the [10 capture application](../../apps/10_capture/README.md).
For external input, it receives one PCAPNG file, a source description, its
content hash, and an explicit app-side reference profile.

The profile identifies the observation boundary with an interface and one or
more local IP addresses; it includes local MAC addresses when available. This
contract does not prescribe the profile's configuration serialization.

## Published Package

Every successful conversion has exactly this layout:

```text
manifest.json
target.pcapng
uplink.pcapng
downlink.pcapng
direction-report.json
launch.toml
```

`target.pcapng` is the unchanged complete input. `uplink.pcapng` and
`downlink.pcapng` are filtered subsets: selected packets retain their original
bytes, timestamps, lengths, and interface metadata. No packet is rewritten.

## Direction Rules

The app-side observation boundary is the only direction authority. A packet
leaving it is uplink; a packet arriving at it is downlink. Client/server roles,
ports, and guesses do not determine direction.

Only confidently classified IP packets may appear in directional files.
Ambiguous, unknown, and non-IP packets are excluded rather than assigned a
direction.

## Manifest and Report

`manifest.json` records schema version, source provenance, exact allowed member
names, and the SHA-256 of every non-manifest member. It never lists or hashes
itself. Relative member paths are canonical and reject absolute paths or
traversal. `direction-report.json` records the reference profile, packet counts
for each artifact, excluded-packet counts, and exclusion reasons.

`launch.toml` is the frozen package copy of the conversion attempt startup
record and is hashed like every other non-manifest member. After canonical
manifest serialization, detached `artifact-status.toml` outside the package
binds its manifest digest, published path, and launch digest under the shared
[artifact protocol](../../libs/artifact_io/SAD.md#successful-status-envelope).
A consumer validates that status and manifest digest before trusting member
names or hashes. [Application configuration](../../CONFIGURATION.md#startup-record)
owns startup-record rules.

## Publication Invariants

One input and reference profile produce deterministic directional artifacts.
The complete package is validated and hashed before atomic publication; status
is published only afterward. A missing, partial, orphaned, extra, or
hash-mismatched package is not a successful artifact.

## Consumer Selection

[Genetic training](../../apps/30_genetic_training/README.md) and
[inspection export](../../apps/25_inspection_export/README.md) receive the
explicit detached status path and exactly one requested member name: `target`,
`uplink`, or `downlink`. Each validates status, manifest membership, member
hash, and PCAPNG before use. No member is implicitly preferred and `target` is
not a default because choosing a direction changes experiment meaning.

## Wi-Fi-like Terminology

Uplink and downlink are Wi-Fi-like analytical labels. They do not represent
radio frames, channels, signal strength, wireless retries, or other
physical-layer observations.

## Scope

This contract defines PCAPNG directional artifacts only. It does not define
datasets, tables, model features, generated traffic, or mathematical metrics.

## Reading

Follow the [architecture governance](../../README.md) and read the
[20 convert application](../../apps/20_convert/README.md) before changing this
contract. Read [genetic training](../../apps/30_genetic_training/SAD.md) and
[inspection export](../../apps/25_inspection_export/SAD.md) before changing
consumer selection.

## Architecture Qualities, Risks, and Testing

Schema and validators are shared by producer and consumer; JSON/TOML ordering is
canonical and PCAPNG validation streams bounded records. Package paths, hashes,
and untrusted PCAPNG are security boundaries. Validators log member-level
reasons without packet contents. Golden/mutation fixtures verify compatibility,
atomic completeness, direction exclusions, and exact packet preservation.
Schema evolution requires explicit version compatibility; no migration policy
is currently defined beyond rejecting unsupported versions.
