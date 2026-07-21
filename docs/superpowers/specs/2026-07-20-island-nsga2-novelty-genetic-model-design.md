# Island NSGA-II Novelty Genetic Model Design

## Goal

Add an advanced selectable genetic model, `island_nsga2_novelty`, that improves
diversity and resistance to local optima beyond the basic generational model.

## Scope

Create `architecture/genetic_models/island_nsga2_novelty/README.md` and register
the model in `architecture/genetic_models/README.md`. This is architecture
documentation only: no implementation, configuration template, or test fixture.

## Population Structure

The model has a configurable positive number of islands. Each island has a
configurable positive population size and may contain candidates of different
traffic-model types. Every island and candidate receives a stable deterministic
ID. Candidates retain their model type when migrated; crossover is allowed only
between candidates of the same traffic-model type.

## Objectives and Selection

Each candidate has two maximized objectives:

- target-traffic similarity from the configured similarity evaluation; and
- novelty score from its behavior descriptor.

Selection uses NSGA-II-style non-dominated sorting and crowding distance. A
candidate dominates another only when it is at least as good on both objectives
and strictly better on at least one. Deterministic tie-breaks use front rank,
larger crowding distance, then stable candidate ID.

## Behavior Descriptor and Novelty

Each candidate generates a small deterministic probe capture using an explicit
probe seed and stop rule. A fixed descriptor extracts Layer 2 behavior from
that artifact: IAT statistics, frame-length statistics, packet/byte rates,
burst measures, and configured lag correlations. It does not inspect packet
bytes or higher-layer protocols.

The descriptor is normalized by explicit fixed scales. Novelty is the mean
distance to the configured number of nearest descriptors in the island plus a
bounded archive of descriptors from prior generations. Distance, archive size,
probe configuration, descriptor fields, and all scales are explicit settings.

## Evolution and Migration

Within each island, parent choice, compatible crossover, mutation, elitism, and
new-population formation follow its own deterministic rules. A configured ring
migration occurs every positive number of generations: each island sends its
best configured number of candidates to the next island. Recipients replace
their weakest compatible candidates according to the deterministic NSGA-II
ordering. Migration never changes a candidate's model type.

## Determinism, Lineage, and Failure

All random choices derive from the run seed through documented substreams.
Canonical island, candidate, descriptor, archive, migration, and serialization
orders are required. Run lineage records objectives, descriptor settings and
values, Pareto front/crowding results, migration decisions, parents,
mutations, probe artifacts and hashes, and every seed.

Invalid configuration, failed probe generation, failed target evaluation,
non-finite objective/descriptor values, insufficient compatible candidates, or
failed migration is explicit candidate or run failure as defined by the owner;
there is no silent fallback. Future tests use offline fixtures only and require
no live capture, network access, sudo, root, or elevated privilege.
