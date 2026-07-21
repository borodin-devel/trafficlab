# Basic Poisson Traffic Models Design

**Date:** 2026-07-20<br>
**Status:** approved design, pending architecture documentation

## Purpose

Define the first two interchangeable Trafficlab traffic models for synthetic
Layer 2 traffic. Both models describe only inter-arrival time and original
Ethernet frame length. They intentionally do not model protocols, addresses,
payload meaning, directions, flows, or sessions.

## Scope

Create architecture owner documents for:

- `poisson_uniform`; and
- `poisson_empirical`.

Update the traffic-model registry to list both models. This work does not
implement model creation, extraction from PCAPNG, random sampling, Ethernet
frame construction, or traffic generation.

## Alternatives Considered

1. **Poisson arrivals with one fixed packet size.** Rejected because it cannot
   train or compare packet-size behavior.
2. **Poisson arrivals with uniformly distributed packet sizes.** Selected as
   the smallest stochastic model with trainable timing and size behavior.
3. **Poisson arrivals with an empirical packet-size table.** Also selected as
   a separate model because its parameter schema and crossover compatibility
   differ from the uniform model.

The uniform and empirical variants are separate traffic models, not modes of
one model. A candidate may change between them through model-replacement
mutation, but crossover remains restricted to candidates with the same model
name.

## Shared Arrival Process

Both models use a homogeneous Poisson arrival process with rate
`arrival_rate_pps = lambda`, where `lambda` is finite and greater than zero.
Successive inter-arrival times are independent samples:

```text
IAT_i ~ Exponential(lambda)
timestamp_i = timestamp_(i-1) + IAT_i
```

Generation starts at time zero, but no packet is forced at time zero. The
first packet timestamp is the first sampled IAT. Packet-size samples are
independent of IAT samples and of other packet-size samples.

## Generation Limit

Every model file selects exactly one stopping mode:

- `packet_count`, with a positive integer `packet_count`; or
- `duration`, with a finite positive `duration_seconds`.

Providing both limit values, neither value, or a value that does not match the
selected mode is invalid. Count mode emits exactly the requested number of
packets. Duration mode emits a packet only when its cumulative timestamp is at
most the duration limit; a next packet beyond the limit is not emitted.

## Randomness and Reproducibility

Every model file contains an integer `seed`. The seed and stopping settings are
generation controls, not trainable parameters. Within one recorded generator
implementation version, the same validated model file produces the same IAT,
size, and timestamp sequence. Generated artifact lineage records that
implementation version so a later sampling change cannot silently claim the
same deterministic sequence.

## Layer 2 Size Definition

`frame_size_bytes` means the original untagged Ethernet frame length including
the Ethernet header and excluding the four-byte Ethernet FCS. Version 1 accepts
integer lengths from 60 through 1514 bytes inclusive. It does not support VLAN
overhead, jumbo frames, non-Ethernet link types, or invalid/truncated original
length metadata.

For a reference PCAPNG, size extraction uses the packet's original recorded
length, not its captured byte count. This preserves the correct size when a
capture contains only the first 256 bytes of a longer frame.

The traffic models produce timestamp and frame-length sequences. The
`50_traffic_generation` application owns turning those values into PCAPNG
packets using a fixed Ethernet frame template. MAC addresses, EtherType,
payload contents, and payload meaning are outside these models.

## `poisson_uniform`

Packet sizes are independent samples from the inclusive discrete uniform
distribution:

```text
frame_size_bytes ~ DiscreteUniform(minimum_bytes, maximum_bytes)
```

`arrival_rate_pps`, `minimum_bytes`, and `maximum_bytes` are trainable.
Validation requires:

```text
arrival_rate_pps > 0
60 <= minimum_bytes <= maximum_bytes <= 1514
```

The normal model file created by `40_model_creation` is:

```toml
model = "poisson_uniform"
schema_version = 1
arrival_rate_pps = 10.0
seed = 0

[packet_size]
minimum_bytes = 60
maximum_bytes = 1514

[stop]
mode = "packet_count"
packet_count = 1000
```

## `poisson_empirical`

Packet sizes are independent categorical samples from a discrete table. Each
entry contains one exact original L2 frame length and a positive integer
weight. Entries have unique sizes and canonical ascending size order. Sampling
probabilities are the normalized weights.

During genetic training, `30_genetic_training` extracts the exact size
frequency table from the one reference Ethernet PCAPNG and replaces the
starting table. The extracted weights are observed counts. The table remains
empirical rather than genetically mutated; `arrival_rate_pps` is the trainable
parameter.

Extraction fails for a non-Ethernet reference, missing or invalid original
length metadata, or any original frame length outside 60 through 1514 bytes.
It never silently excludes, clamps, or rewrites an observed size.

The normal valid model file created by `40_model_creation` is:

```toml
model = "poisson_empirical"
schema_version = 1
arrival_rate_pps = 10.0
seed = 0

[[packet_size.entries]]
size_bytes = 60
weight = 1

[stop]
mode = "packet_count"
packet_count = 1000
```

The single 60-byte entry is only the valid standalone starting table. Training
replaces it with the reference capture's frequency table before generation.

## Validation and Failure

Model parsing rejects unknown keys, unsupported schema versions, non-finite
numbers, invalid types, invalid bounds, invalid weights, duplicate empirical
sizes, noncanonical empirical ordering, and inconsistent stopping settings.
Validation occurs before random sampling or output creation. Invalid input is
never partially applied.

## Testing

Future tests are deterministic and unprivileged. For both models they cover
schema validation, both stopping modes, no forced packet at time zero,
duration-boundary behavior, fixed-seed repeatability, timestamp monotonicity,
and frame-size bounds.

Uniform-model tests cover inclusive bounds and the one-value range case.
Empirical-model tests cover exact frequency extraction using original PCAPNG
lengths, canonical table ordering, weighted sampling, and strict rejection of
unsupported reference frames. No test requires network access, live capture,
or elevation.
