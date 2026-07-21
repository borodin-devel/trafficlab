# Model Creation Application

## Purpose and Responsibilities

`model_creation` creates one selected traffic model's validated,
self-describing immutable normal builder.

## Inputs, Outputs, and Interface

`model_creation --model NAME --output-dir DIR` atomically produces the absent
single-file destination `DIR/NAME.toml` under the selected model's builder
schema. Detached successful status carries its content digest.

## Configuration, Dependencies, and Execution

The application runs offline and unprivileged. It depends on the traffic-model
registry, configuration, artifact, and lineage libraries. No settings are
defined beyond explicit command arguments. The output is not generation-ready;
the common [model lifecycle](../../traffic_models/README.md#model-lifecycle)
requires a distinct preparation/finalization result.

The [traffic-model registry](../../traffic_models/README.md) owns schemas and
normal values. [Genetic training](../30_genetic_training/README.md) is a caller;
shared mechanics come from [configuration](../../libs/configuration/README.md),
[artifact publication](../../libs/artifact_io/README.md), and
[lineage](../../libs/lineage/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
