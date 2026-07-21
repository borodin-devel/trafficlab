# Basic Generational Genetic Strategy

## Purpose and Responsibilities

`basic_generational` evolves a complete population per generation using global
tournaments, elitism, same-model uniform crossover, local mutation, optional
model replacement, and bounded retries.

## Inputs, Outputs, Dependencies, and Interface

Input is validated strategy configuration plus immutable normal-builder
baselines and child similarity results. Output is deterministic genetic
decisions, stopping state, winner, and lineage consumed by genetic training.

## Related Components and Execution

[Genetic training](../../apps/30_genetic_training/README.md) hosts the strategy
offline and coordinates [traffic models](../../traffic_models/README.md),
[similarity methods](../../similarity_methods/README.md), and the
[similarity-result contract](../../contracts/60_30_similarity_result/README.md).

## Documents and Reading Order

1. [SAD](SAD.md)
2. [SRS](SRS.md)
3. [Configuration](CONFIGS.md)
4. [Representation and determinism](00_REPRESENTATION.md)
5. [Population and evaluation](01_POPULATION_EVALUATION.md)
6. [Selection and elitism](02_SELECTION_ELITISM.md)
7. [Crossover and mutation](03_OPERATORS.md)
8. [Stopping, lineage, and validation](04_STOPPING_LINEAGE_VALIDATION.md)
9. [Testing](05_TESTING.md)
10. [Roadmap](ROADMAP.md)
