# Basic Generational Configuration

## Authority

Exact TOML paths, types, defaults, and override rules are defined by
[genetic-training configuration](../../apps/30_genetic_training/CONFIGURATION_SCHEMA.md).

## Groups

The strategy uses seed, population size, elite count, tournament size,
crossover probability, parameter-mutation probability, model-replacement
probability, offspring-attempt limit, stopping conditions, and per-model
trainable search domains. Search entries use model-declared numeric or choice
types and must contain the immutable normal-builder baseline.

## Validation

Counts, probabilities, unit-sum mutation decision, model count for replacement,
score target, trainable paths, bounds, steps, baseline inclusion, and complete
model cross-constraints validate before work. Unknown values fail.
