# 00 Representation, Boundaries, Fitness, and Determinism

## Role

`basic_generational` is a selectable [genetic strategy](../README.md) for
[30 genetic training](../../apps/30_genetic_training/README.md). It evaluates a
whole population before it checks stopping or forms the whole next population.
Different allowed traffic-model names compete in the same population.

This document owns the genetic algorithm, deterministic ordering, stopping,
failure behavior, and strategy validation. The exact TOML field names, types,
defaults, and command-line overrides belong to the [30 genetic-training
configuration](../../apps/30_genetic_training/CONFIGURATION_SCHEMA.md).

## Component Boundaries

30 genetic training owns files, child-process orchestration, diagnostics, and
publication. [40 model creation](../../apps/40_model_creation/README.md) owns
creation of a model's normal file, [50 traffic
generation](../../apps/50_traffic_generation/README.md) owns synthetic PCAPNG
generation, and [60 similarity
evaluation](../../apps/60_similarity_evaluation/README.md) owns comparison of
one reference PCAPNG with one generated PCAPNG.

The selected [traffic model](../../traffic_models/README.md) owns its schema,
trainable fields, preparation, and complete-model validation. The selected
[similarity method](../../similarity_methods/README.md) owns its primary score
range, direction, and meaning. This strategy does not redefine traffic or
similarity mathematics.

## Candidates and Run Baselines

One candidate is one allowed traffic-model name with that model's current
parameter values. For every candidate, 30 follows its existing orchestration:
it obtains one immutable normal builder through 40, copies its values into an
in-memory candidate configuration, applies and validates all genetic parameter
values, and only then invokes model-owned preparation or finalization. For
example, [`poisson_empirical`](../../traffic_models/poisson_empirical/README.md)
extracts its reference-derived frame-size table after the candidate arrival
rate validates. The immutable builder values are that model's run baseline;
prepared generation-ready values are per-candidate evaluation results, never a
baseline that genetic operations edit.

Same-model crossover and local parameter mutation never alter non-trainable
settings. Model replacement deliberately adopts the replacement model's whole
immutable builder baseline and reference context, translates no setting from
the old model, applies the new candidate values, and prepares only afterward.

## Fitness and Stable Identity

A candidate is eligible only when traffic generation and similarity evaluation
succeed and the published [similarity
result](../../contracts/60_30_similarity_result/README.md) validates. A failed
candidate remains in diagnostics but cannot parent, become an elite, or win.

Fitness comparison uses the selected similarity method's declared direction.
The strategy neither renormalizes scores nor assumes that higher is always
better. A strict improvement requires a genuinely better primary score under
that direction.

Candidate identity is the numeric tuple `(generation, slot)`. Numeric tuple
ordering, independent of the textual ID serialization, is the deterministic
tie-break for elite ordering, reports, and final-winner selection: the lower ID
wins. This tie-break is not a fitness improvement and does not reset the
no-improvement counter. Tournament score ties use seeded random selection
instead, as defined below.

## Randomness and Canonical Order

Initialization, tournaments, crossover, mutation, and model replacement use
one pseudo-random stream initialized from `strategy_seed`. For one recorded
strategy implementation and random-generator version, the same validated
configuration and child results produce the same genetic decisions.

Random draws occur in generation, population-slot, whole-attempt, and operator
order. Before a random operation:

- candidate collections use ascending stable-ID order;
- model collections retain `allowed_traffic_models` order;
- choice values retain their configured order; and
- parameter collections use ascending dotted parameter-path order.

Mapping iteration and child-process completion order never control draws. 30
waits for the entire generation and restores stable-ID order before checking
stopping or drawing the next population. Run diagnostics identify the strategy
implementation and random-generator name and version.
