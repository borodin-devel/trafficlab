# 02 Evolution, Migration, Lineage, and Testing

## Evolution and Migration

Initialization, compatible crossover, mutation, elitism, retries, and all
random choices use documented seed substreams and canonical order. At every
positive configured migration interval, each island sends its best configured
number of eligible candidates to the next island in a fixed ring. The recipient
replaces weakest compatible candidates by NSGA-II ordering. Failed migration or
no compatible slot is recorded and handled as configured failure; nothing is
silently converted or repaired.

## Lineage, Validation, and Testing

Lineage records objectives, descriptors, archive changes, Pareto fronts,
crowding distances, parents, operators, probe hashes, migrations, and seeds.
Configuration validates counts, intervals, scales, archive limits, and seeds
before work. Future offline tests cover dominance, crowding, novelty, archives,
ring migration, mixed-model islands, same-model crossover, determinism, and
failure behavior; they need no capture, network access, or elevated privilege.

## Implementation Gate

Seed derivation, archive-update ordering, migration failure policy, resource limits, and winner selection must be present in the versioned configuration and golden traces. Until that gate passes, registry selection fails as unsupported.
