# 00 Poisson Empirical Model Definition

## Role

`poisson_empirical` is a selectable Layer 2 traffic model with Poisson packet
arrivals and packet lengths sampled from a reference capture's observed size
frequencies.

## Shared Behavior

The [Poisson family](../POISSON.md) owns arrival timing, stopping, seed
semantics, repeatability, original L2 frame-length meaning, and the output
boundary. This document owns only empirical size extraction, categorical
sampling, and this model's file schema.

## Packet-Size Distribution

The model stores a discrete categorical table. Each entry has one exact
`size_bytes` and one positive integer `weight`. Sizes are unique and stored in
canonical ascending order. A size's sampling probability is its weight divided
by the sum of all weights.

`arrival_rate_pps` is trainable. The extracted size-frequency table, seed, and
stopping controls are not genetically trained.

## Reference Extraction

After all candidate parameters are applied and validated,
[30 genetic training](../../apps/30_genetic_training/README.md) prepares a new
generation-ready file whose table contains exact observed counts from its one
reference Ethernet PCAPNG. It never replaces or edits the starting builder.
It uses each packet's original packet length, never the possibly truncated
captured byte count.

Extraction rejects a non-Ethernet link type, missing or invalid original-length
metadata, or any observed length outside 60 through 1514 bytes. It never
excludes, clamps, or rewrites an observation.

## Normal Builder and Prepared Model

[40 model creation](../../apps/40_model_creation/README.md) creates the
immutable `poisson_empirical.toml` builder with these normal values:

```toml
model = "poisson_empirical"
schema_version = 1
model_state = "builder"
arrival_rate_pps = 10.0
seed = 0

[[packet_size.entries]]
size_bytes = 60
weight = 1

[stop]
mode = "packet_count"
packet_count = 1000
```

The one-entry table is a valid candidate-fitting starting table, not a claim
about real traffic and not generation-ready. Genetic training copies it,
applies the candidate arrival rate, then prepares a distinct model with the
reference frequency table. That file records builder digest, candidate
identity, effective values, exact reference digest, seed, implementation
version, and generation-ready state.

## Validation and Failure

The builder validator requires `model_state = "builder"` and exactly the
builder fields above. The generation-input validator instead requires
`model_state = "generation_ready"`, the reference-derived table, and complete
builder/candidate/reference lineage, and rejects a builder. Both reject unknown
keys, unsupported schema versions, invalid types, a non-finite or nonpositive
arrival rate, an empty table, duplicate or noncanonical sizes, nonpositive or
noninteger weights, unsupported sizes, and invalid shared stopping controls.
Generation validation completes before
[50 traffic generation](../../apps/50_traffic_generation/README.md) samples or
publishes output; invalid input is never partially applied.

## Testing

Future deterministic, unprivileged tests cover fixed-seed repeatability, both
stopping modes, first-arrival timing, monotonically increasing timestamps,
weighted categorical sampling, exact frequency extraction, canonical ordering,
and strict rejection of unsupported frames and invalid model files. PCAPNG
fixtures prove that extraction uses original packet length rather than captured
byte count. No test requires network access, live capture, or elevation.

## Reading

Follow the [architecture governance](../../README.md), the shared Poisson
family rules, and the [traffic-model registry](../README.md) before changing
this model.
