# Neighbour-Transition Software Requirements Specification

## Requirements

- **MNT-FR-001:** Each noninitial frame shall map to `(log1p(IAT), original_length)`.
- **MNT-FR-002:** Configured joint categories shall form a nonempty gap-free nonoverlapping partition of the complete valid domain.
- **MNT-FR-003:** Canonical category order shall assign stable state IDs.
- **MNT-FR-004:** Method shall count every adjacent ordered state pair and include every possible pair in vectors.
- **MNT-FR-005:** Transition probabilities shall divide counts by total adjacent transitions.
- **MNT-FR-006:** Similarity shall be one minus base-2 square-root Jensen-Shannon divergence.
- **MNT-IF-001:** Result shall record categories/IDs, counts, probabilities, observations, transitions, raw distance, and configuration.
- **MNT-CFG-001:** Categories shall validate against complete domain, not only observed samples.
- **MNT-NFR-001:** Score shall be deterministic in `[0,1]`, higher better.
- **MNT-ERR-001:** Invalid partition/assignment/input/numerics shall fail without dropping observations.
- **MNT-TST-001:** Tests shall cover boundaries, partitions, zero pairs, canonical order, and hand JSD.

## Acceptance Criteria

- **MNT-AC-001:** Hand state sequences reproduce exact counts, vectors, JSD, score, and ordering.
- **MNT-AC-002:** Gap/overlap/out-of-domain/uncovered/insufficient cases fail.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Definition](00_METHOD_DEFINITION.md) · [Roadmap](ROADMAP.md)
