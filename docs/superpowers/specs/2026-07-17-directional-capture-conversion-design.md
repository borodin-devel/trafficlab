# Directional Capture Conversion Design

## Goal

Define `20_convert` as a small, deterministic conversion application that
turns a complete capture into fixed target, uplink, and downlink PCAPNG
artifacts for later mathematical similarity comparison.

## Scope

`20_convert` does not build normalized datasets, tables, flow/session models,
CSV, JSON exports, or model features. It owns directional PCAPNG filtering,
provenance, and the report of omitted packets.

The design creates `architecture/apps/20_convert/README.md` and the file
contract `architecture/contracts/10_20_capture_directions/`. It also updates
the architecture reading path and the capture output reference.

## Inputs

The application accepts either:

- the canonical `10_capture` artifact, using its recorded workspace boundary
  and capture metadata; or
- an external PCAPNG file with a source description, source hash, and explicit
  app-side reference profile.

The reference profile identifies the monitored side with its capture interface
and one or more local IP addresses; local MAC addresses are included when
available. The exact configuration serialization remains a later decision.

## Direction Rules

Direction is determined only at the app-side observation boundary:

- a packet leaving the app side is uplink;
- a packet arriving at the app side is downlink.

The converter does not infer direction from client/server roles, ports, or
arbitrary address guesses. Only confidently classified IP packets enter the
directional files. Ambiguous, unknown, and non-IP packets are excluded and
reported with counts and reasons.

The terms uplink and downlink are Wi-Fi-like analytical labels. They do not
claim that the source contains Wi-Fi radio frames, channel data, signal
strength, retransmissions, or other physical-layer observations.

## Output Contract

Every successful conversion publishes this fixed layout:

```text
manifest.json
target.pcapng
uplink.pcapng
downlink.pcapng
direction-report.json
```

`target.pcapng` is the unchanged complete input. Uplink and downlink retain
each selected packet's original bytes, timestamp, lengths, and interface
metadata; only nonmatching packets are removed.

The manifest records source provenance and hashes of all artifacts. The report
records the reference profile, classification counts, skipped-packet counts,
and skipped reasons. All output is validated, hashed, and published atomically.

## Integration

`10_capture` remains the canonical producer for pipeline captures. The
`10_20_capture_directions` contract describes the handoff and output package;
external input follows the same published output package but supplies explicit
source provenance and a reference profile.

Future comparison uses target, uplink, and downlink as distinct similarity
targets. Its mathematical methods and generated counterparts are outside this
application's scope.

## Testing

Direction classification and PCAPNG filtering are deterministic, unprivileged
functional-core behavior. Tests use small fixtures and verify exact packet
preservation, direction assignment, exclusions, report counts, hashes, and
atomic-publication behavior. No test requires elevated access or a real capture
workspace.

## Validation

The documentation change is complete when app and contract documents have clear
ownership, their relative links resolve, the fixed layout is consistent, and
the active corpus does not claim physical Wi-Fi capture or introduce model data
into `20_convert`.

