# Model Creation Software Architecture Document

## Role

This application creates one selected traffic model's immutable,
self-describing builder file with that model's normal starting parameter
values.

## Interface

The conceptual command is:

```text
model_creation --model=NAME --output-dir=DIR
```

It atomically creates the absent single-file destination `DIR/NAME.toml`. The
file records its builder state, selected traffic-model name, and complete
normal parameter values. The selected
[traffic model](../../traffic_models/README.md) owns its exact schema, normal
starting values, and validation.

## Boundaries

This application does not mutate parameters, prepare/finalize a candidate,
cross over candidates, select models, evaluate fitness, generate traffic, or
score similarity. The published builder path is immutable and is never a valid
traffic-generation input. Those
responsibilities belong to [30 genetic training](../30_genetic_training/README.md),
[50 traffic generation](../50_traffic_generation/README.md), and
[60 similarity evaluation](../60_similarity_evaluation/README.md).

## Diagnostics and Testing

Each attempt follows the shared
[startup record](../../CONFIGURATION.md#startup-record) and
[single-file publication](../../libs/artifact_io/SAD.md#structure-and-data-flow)
rules. `artifact-status.toml` binds the published file and immutable attempt
launch record; no status means no successful builder. No implementation exists
yet. Future tests are unprivileged and verify deterministic builder validation,
immutability, detached hashes, and publication with temporary directories.

## Reading

Follow the [architecture governance](../../README.md) and read the selected
traffic-model owner before changing this application.

## Cross-Cutting Architecture

Registry lookup, builder creation, schema validation, and canonical values form
the core; CLI, startup record, filesystem, hash, and atomic publication form the
shell. Resource use is small and bounded by model schema. Unknown or unresolved
models fail. Execution is offline and unprivileged with structured summary logs.
