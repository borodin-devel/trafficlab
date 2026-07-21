# Genetic Training Application

## Purpose and Responsibilities

`genetic_training` orchestrates candidate model creation, fitting, generation,
similarity evaluation, genetic selection, and winning-model publication.

## Inputs, Outputs, and Interface

Input is an explicit strategy configuration, exactly one effective reference
selector, and output directory. A capture-directions reference additionally
requires explicit `target`, `uplink`, or `downlink` selection. The successful
artifact contains a generation-ready winning model, ranking report, and
complete lineage; the attempt retains candidate diagnostics.

## Configuration, Dependencies, and Execution

The application runs offline, may schedule candidates in parallel under
resource budgets, and invokes stages 40, 50, and 60 by argument vector.
Its [architecture-owned configuration schema](CONFIGURATION_SCHEMA.md) defines
all paths, defaults, overrides, and generation limits.

It coordinates [model creation](../40_model_creation/README.md),
[traffic generation](../50_traffic_generation/README.md), and
[similarity evaluation](../60_similarity_evaluation/README.md) using the
[genetic strategy](../../genetic_models/README.md),
[traffic-model](../../traffic_models/README.md), and
[similarity-method](../../similarity_methods/README.md) registries. Candidate
scores cross the [similarity-result contract](../../contracts/60_30_similarity_result/README.md),
and parallel work uses [resource admission](../../libs/resource_management/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
