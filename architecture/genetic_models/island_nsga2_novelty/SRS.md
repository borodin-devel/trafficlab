# Island NSGA-II Novelty Software Requirements Specification

## Requirements

- **GIN-FR-001:** The strategy shall maintain positive configured islands and fixed-size populations with stable `(generation,island,slot)` identities.
- **GIN-FR-002:** It shall maximize normalized target similarity and novelty and exclude failed candidates/probes.
- **GIN-FR-003:** NSGA-II dominance shall require no worse on both objectives and better on at least one.
- **GIN-FR-004:** Within a front, larger crowding distance then lower stable ID shall rank first.
- **GIN-FR-005:** Novelty shall be mean configured distance to nearest island/archive descriptors.
- **GIN-FR-006:** Archive size shall remain within configured bound under deterministic insertion/eviction.
- **GIN-FR-007:** Migration shall occur at configured positive intervals around a fixed ring and preserve model type.
- **GIN-FR-008:** Crossover shall use compatible same-model candidates only.
- **GIN-IF-001:** Probe input shall be deterministic PCAPNG generated with explicit seed/stop settings.
- **GIN-CFG-001:** Counts, intervals, descriptor fields/scales, distances, archive, seeds, and failure policy shall validate before work.
- **GIN-NFR-001:** Seed substreams and canonical ordering shall make all decisions reproducible.
- **GIN-ERR-001:** No compatible migration slot or failed migration shall follow explicit policy and never silently convert a model.
- **GIN-TST-001:** Tests shall independently verify dominance, crowding, novelty, archive, and migration.

## Acceptance Criteria

- **GIN-AC-001:** Golden objective matrices reproduce fronts, crowding, archive, migration, and seeded operators.
- **GIN-AC-002:** Mixed-model and failure fixtures preserve compatibility, island size, archive bound, and deterministic lineage.
- **GIN-AC-003:** Strategy cannot be selected until winner policy and all unresolved configuration fields are defined and tested.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Algorithm](00_REPRESENTATION_OBJECTIVES.md) · [Roadmap](ROADMAP.md)
