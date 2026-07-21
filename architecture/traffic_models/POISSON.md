# Poisson Traffic-Model Family

## Role

This document owns behavior shared by Trafficlab's Poisson traffic models. It
is not a selectable traffic-model name; selectable models reference these
rules and own their packet-size distributions.

## Arrival Process

The models use a homogeneous Poisson arrival process with a finite positive
`arrival_rate_pps`. Successive inter-arrival times and timestamps follow:

```text
IAT_i ~ Exponential(arrival_rate_pps)
timestamp_i = timestamp_(i-1) + IAT_i
```

Generation starts at time zero, but the first frame occurs only after the first
sampled IAT. IAT samples are independent of each other and of packet-size
samples. Emitted timestamps are strictly increasing.

## Stopping

A model file selects exactly one stopping mode:

- `packet_count`, with one positive integer `packet_count`; or
- `duration`, with one finite positive `duration_seconds`.

Providing both limit values, neither value, or a value inconsistent with the
selected mode is invalid. Count mode emits exactly the requested number of
frames. Duration mode emits a frame only when its cumulative timestamp is at
most `duration_seconds`; it does not emit a next frame beyond that boundary.

## Randomness and Repeatability

Every model file contains an integer seed. The seed and stopping settings are
generation controls, not trainable parameters. For one recorded generator
implementation version, the same validated model file produces the same IAT,
frame-size, and timestamp sequence. Generated artifact lineage records that
implementation version.

## Layer 2 Frame Length

Frame size means the original untagged Ethernet frame length, including the
Ethernet header and excluding the four-byte FCS. Accepted lengths are integers
from 60 through 1514 bytes inclusive.

Reference extraction uses each PCAPNG packet's original recorded length, not
its captured byte count. A packet may therefore retain its correct modeled
length when capture stored only its first 256 bytes.

This family does not support VLAN overhead, jumbo frames, non-Ethernet link
types, or missing or invalid original-length metadata. It models no protocol,
address, payload meaning, direction, flow, or session.

## Computational Complexity

For `N` emitted frames, seeded arrival and size sampling require `O(N)` time
and `O(1)` live model state beyond the caller-owned output. Duration mode has
the same per-proposal constant work and stops at the first proposal beyond the
boundary. Model-specific reference preparation states its own storage bound.

## Output Boundary

The models produce only timestamp and frame-length sequences.
[50 traffic generation](../apps/50_traffic_generation/README.md) owns creating
PCAPNG packets from those values with a fixed Ethernet frame template.

## Reading

Follow the [architecture governance](../README.md) and the
[traffic-model registry](README.md). Read the selected model owner before
changing a packet-size distribution or model-file schema.
