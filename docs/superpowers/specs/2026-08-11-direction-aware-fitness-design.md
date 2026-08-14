# Direction-Aware Fitness Design

**Date:** 2026-08-11

## Purpose

Trafficlab currently carries packet direction in its canonical trace but does not
use direction in any similarity score. A model can therefore reverse every
packet direction without reducing fitness. This amendment makes direction
unambiguous and visible to the existing multiscale-rate method without adding a
fifth similarity method, new weights, or new experiment settings.

## Capture Scope and Direction

Trafficlab captures only the target container's main Docker Ethernet interface,
named `eth0`, with promiscuous mode disabled. Loopback and additional interfaces
are outside the MVP capture scope.

Before capture begins, Trafficlab reads the target namespace's `eth0` MAC address
and normalizes it to lowercase colon-separated form. Captured Ethernet frames are
classified as:

```text
source MAC equals target MAC  -> outbound
otherwise                     -> inbound
```

Because capture is limited to the target's non-promiscuous `eth0`, a frame not
sent by the target is a frame delivered to it. This also classifies inbound
broadcast frames correctly. A missing `eth0`, invalid target MAC, unsupported
non-Ethernet capture link type, or malformed Ethernet frame is a capture/parser
error rather than an unknown direction or low model fitness.

This definition describes traffic crossing the target network interface. It does
not claim to identify which process inside the container produced a packet; that
separate capture-scope issue is not changed here.

## Capture Metadata

Docker resources disappear after teardown, so direction cannot depend on later
container inspection. Capture therefore publishes `capture.json` beside
`reference.pcapng`:

```json
{
  "interface": "eth0",
  "target_mac": "02:42:ac:11:00:02"
}
```

`interface` is the literal `eth0`. `target_mac` is a normalized six-octet MAC
address. Unknown fields, a missing field, or invalid values are errors.

`capture.json` is scientific input metadata, not a detached status, launch, or
lineage subsystem. It is written atomically before `reference.pcapng` is
published. Reference parsing requires it. Model and similarity results include
its SHA-256 identity wherever they already record direct input identities.

The generated PCAPNG uses the same target MAC. Its synthetic peer MAC is
`02:00:00:00:00:01`, or `02:00:00:00:00:02` when the first value equals the
target MAC. For an outbound event the target MAC is the source; for an inbound
event it is the destination. Consequently, the same direction classifier and
`capture.json` can parse both reference and generated PCAPNG files during a
standalone `compare` command.

## Direction-Aware Multiscale Vectors

The MVP retains its four similarity methods. Frame-size KS, IAT KS, and ACF are
unchanged. Multiscale rate becomes direction-aware.

For width \(h\), feature \(f\in\{packet,byte\}\), and \(B\) time bins, construct
separate outbound and inbound bin counts. Flatten them into one vector:

\[
r_{h,f}=(r_{out,1},\ldots,r_{out,B},r_{in,1},\ldots,r_{in,B}),
\]

and construct \(g_{h,f}\) identically. Packet cells contain packet counts; byte
cells contain sums of captured frame lengths.

The existing normalized L1 discrepancy is unchanged:

\[
D(r,g)=\frac{\sum_i|r_i-g_i|}{\sum_i r_i+\sum_i g_i},
\]

with \(D=0\) when both vectors sum to zero. Existing packet/byte feature weights,
scale weights, and `1 - D` score conversion are unchanged.

If the configured cap is \(C_{max}\), validation requires
\(2\sum_h B_h\le C_{max}\). The factor two represents outbound and inbound cells;
the same cell layout is used independently for the packet and byte feature
vectors. This changes no configuration field and keeps memory explicitly
bounded.

Diagnostics add outbound and inbound packet/byte totals at every scale. They
retain the combined per-feature discrepancy used by fitness.

## Behavior Examples

- Identical timestamps, lengths, and directions score `1` under multiscale rate.
- A wholly outbound trace compared with an otherwise identical wholly inbound
  trace has discrepancy `1` and score `0` for both packet and byte features.
- A mixed trace loses similarity only in the direction-bin cells that differ.
- Two empty direction-bin vectors use the existing zero-denominator convention.
- Reversing direction can still score `1` only when the reference is itself
  exactly direction-symmetric at every configured scale; the method then has no
  observable difference to distinguish.

## Failure Handling

Direction metadata and Ethernet decoding are validated before model fitting. A
missing or invalid `capture.json`, unsupported link type, or unclassifiable frame
aborts parsing with a direct diagnostic. These shared-input failures never become
candidate fitness `0`.

Generated canonical events must contain exactly `outbound` or `inbound`.
Rendering any other value fails before publishing `generated.pcapng`.

## Tests

Unit tests cover:

- normalized target MAC parsing;
- outbound frames whose source is the target MAC;
- inbound unicast and broadcast frames whose source is not the target MAC;
- malformed metadata, Ethernet frames, and direction values;
- reference and generated PCAPNG direction round trips;
- direction-aware packet and byte vectors at more than one scale;
- identical, wholly reversed, mixed, and empty-vector calculations;
- the doubled direction-bin cell cap.

The Docker integration workload must produce at least one outbound and one
inbound frame. Parsed directions must agree with the controlled target and
endpoint roles. The in-process model/similarity integration test compares a trace
with its direction-reversed copy and requires multiscale similarity below `1`.

## Architecture Documents Changed

Implementation updates only the documents that own this behavior:

- `architecture/SYSTEM.md` for the canonical direction and `capture.json`
  artifact;
- `architecture/CAPTURE.md` for `eth0`, non-promiscuous capture, MAC discovery,
  and publication order;
- `architecture/similarity_methods/README.md` and `multiscale_rate.md` for the
  direction-aware score;
- `architecture/TESTING.md` for mathematical, round-trip, and Docker evidence;
- `architecture/ROADMAP.md` for the corrected Phase 2 and Phase 3 deliverables.

No traffic-model interface, genetic chromosome, method registry entry, method
weight, or experiment configuration field changes in this amendment.
