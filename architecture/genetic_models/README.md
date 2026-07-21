# Genetic Models

## Role

This directory contains named genetic strategies that describe how genetic
training evolves traffic-model candidates.

## Layout

Each strategy belongs in `genetic_models/<name>/`. Its `README.md` owns
population creation, selection, mutation, same-model crossover,
model-replacement rules, stopping conditions, compatibility requirements, and
validation criteria.

## Strategy Status

| Name | Status | Owner |
| --- | --- | --- |
| `basic_generational` | Selectable | [Basic generational](basic_generational/README.md) |
| `island_nsga2_novelty` | Planned; unselectable until configuration and winner-policy gate passes | [Island NSGA-II novelty](island_nsga2_novelty/README.md) |

[30 genetic training](../apps/30_genetic_training/README.md) implements the
selected mature strategy and selects one named strategy for each run. Strategies do
not own traffic-model equations, PCAPNG generation, or similarity formulas.

## Reading

Follow the [architecture governance](../README.md). Read the selected strategy
owner before changing genetic-training behavior.
