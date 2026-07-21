# 00 Representation, Objectives, and NSGA-II

## Role

`island_nsga2_novelty` evolves several populations, ranks candidates by target
similarity and behavioral novelty, and periodically migrates candidates between
islands. It is a planned alternative for 30 genetic training and does not replace
the basic generational strategy. It remains unselectable until the unresolved
mathematical, configuration, migration-failure, and winner policies are approved.

## Islands and Candidates

Island count and population size are positive configuration values. Stable
candidate identity is `(generation, island, slot)`. Islands may mix traffic
model types. Crossover is only between compatible candidates of the same model
type; migration keeps model type unchanged.

## Objectives and Selection

Similarity and novelty are both maximized. Failed generation, evaluation, or
probe candidates are ineligible. NSGA-II non-dominated sorting ranks eligible
candidates: one dominates another when it is no worse on both objectives and
better on one. Larger crowding distance wins within a front, then lower stable
candidate ID. Similarity direction remains owned by its selected method.


## Unresolved Objective Decisions

A lower-is-better similarity method requires an explicit deterministic ranking transform; the published primary value must remain unchanged. The final Pareto-front-to-winner policy is also unresolved. No implementation may infer either policy.
