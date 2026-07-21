# 00 Poisson Uniform Model Definition

## Role

`poisson_uniform` is a selectable Layer 2 traffic model with Poisson packet
arrivals and uniformly distributed integer frame lengths.

## Shared Behavior

The [Poisson family](../POISSON.md) owns arrival timing, stopping, seed
semantics, repeatability, original L2 frame-length meaning, and the output
boundary. This document owns only the uniform size rule and this model's file
schema.

## Packet-Size Distribution

Each frame length is sampled independently from the inclusive discrete uniform
distribution:

```text
frame_size_bytes ~ DiscreteUniform(minimum_bytes, maximum_bytes)
```

The trainable parameters are `arrival_rate_pps`, `minimum_bytes`, and
`maximum_bytes`. Size validation requires:

```text
60 <= minimum_bytes <= maximum_bytes <= 1514
```

The seed and stopping settings are generation controls and are not genetically
trained.

## Normal Builder and Finalization

[40 model creation](../../apps/40_model_creation/README.md) creates the
immutable `poisson_uniform.toml` builder with these normal values:

```toml
model = "poisson_uniform"
schema_version = 1
model_state = "builder"
arrival_rate_pps = 10.0
seed = 0

[packet_size]
minimum_bytes = 60
maximum_bytes = 1514

[stop]
mode = "packet_count"
packet_count = 1000
```

[30 genetic training](../../apps/30_genetic_training/README.md) may change only
the trainable parameters. The normal values are a pipeline baseline, not a
claim about captured traffic. It applies candidate values to an in-memory copy
and validates the complete configuration; it never changes the builder.

This model requires no reference fit. Its model-owned preparation is a
deterministic finalize step that publishes a distinct generation-ready file
with builder digest, candidate identity, effective parameters, seed,
implementation version, and finalization state. Traffic generation rejects the
builder and accepts only that generation-ready descendant.

## Validation and Failure

The builder validator requires `model_state = "builder"` and exactly the
builder fields above. The generation-input validator instead requires
`model_state = "generation_ready"` plus complete finalization and
builder/candidate lineage, and rejects a builder. Both reject unknown keys,
unsupported schema versions, invalid types, a non-finite or nonpositive arrival
rate, invalid size bounds, and invalid shared stopping controls. Generation
validation completes before
[50 traffic generation](../../apps/50_traffic_generation/README.md) samples or
publishes output; invalid input is never partially applied.

## Testing

Future deterministic, unprivileged tests cover fixed-seed repeatability,
inclusive minimum and maximum values, a one-value size range, both stopping
modes, first-arrival timing, monotonically increasing timestamps, and every
validation failure. No test requires network access, live capture, or
elevation.

## Reading

Follow the [architecture governance](../../README.md), the shared Poisson
family rules, and the [traffic-model registry](../README.md) before changing
this model.
