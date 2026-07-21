# Island NSGA-II Novelty Software Architecture Document

## Context, Goals, and Non-Goals

This strategy adds diversity-aware multi-island evolution while reusing
traffic-model and similarity owners. It does not read packet payloads, redefine
similarity direction, or allow cross-model crossover.

## Structure and Runtime Interaction

The pure core maintains stable `(generation,island,slot)` identity, eligible
objective vectors, non-dominated fronts, crowding, novelty archives, compatible
operators, and scheduled ring migration. Genetic training produces target and
probe artifacts, restores canonical order, and publishes lineage.

## Configuration, Errors, Performance, and Security

Positive bounded island/population/migration/archive/neighbor controls and
explicit descriptor scales validate before work. Failed candidates/probes are
ineligible. Empty compatible migration and archive cases use only documented
failure policy. Candidate/probe parallelism obeys resource budgets. Core runs
offline without files/network/privilege.

## Testing, Decisions, Risks, and Limits

Tests cover NSGA-II invariants, descriptors, novelty, archive bounds, seed
substreams, compatible migration, and failures. Risks are high evaluation cost,
descriptor scale bias, novelty gaming, and unclear single-winner extraction
from a Pareto set. Exact winner policy and complete configuration schema remain
unresolved and must be resolved before selection for a run.
