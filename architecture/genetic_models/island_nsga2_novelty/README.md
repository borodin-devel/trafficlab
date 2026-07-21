# Island NSGA-II Novelty Genetic Strategy

## Purpose and Responsibilities

`island_nsga2_novelty` is a planned strategy that evolves multiple mixed-model
islands against target similarity and Layer 2 behavioral novelty using NSGA-II
ranking, bounded archives, and fixed-ring migration. It remains unselectable
until its explicit configuration and winner-policy gate passes.

## Inputs, Outputs, Dependencies, and Interface

Input is validated strategy configuration, eligible candidate results, and
deterministic probe descriptors. Output is population/archive/migration
decisions, Pareto rankings, winner policy result, and lineage for training.

## Related Components and Execution

[Genetic training](../../apps/30_genetic_training/README.md) hosts the strategy
offline and coordinates [traffic models](../../traffic_models/README.md),
[similarity methods](../../similarity_methods/README.md), and the
[similarity-result contract](../../contracts/60_30_similarity_result/README.md).

## Documents and Reading Order

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[Representation and objectives](00_REPRESENTATION_OBJECTIVES.md) ·
[Novelty and archives](01_NOVELTY_ARCHIVES.md) ·
[Evolution and migration](02_EVOLUTION_MIGRATION.md) · [Roadmap](ROADMAP.md)
