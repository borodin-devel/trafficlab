# 01 Novelty and Archives

## Novelty

Each candidate creates a deterministic probe PCAPNG from explicit probe seed
and stop settings. Its configured Layer 2 descriptor contains IAT and
frame-length statistics, packet/byte rates, burst measures, and lag
correlations; it never reads bytes or higher protocols. Explicit scales
normalize descriptor fields. Novelty is mean configured-distance to nearest
descriptors in the island plus a bounded archive of prior descriptors.


## Unresolved Novelty Decisions

Exact descriptor formulas, distance, neighbor count, archive insertion threshold, eviction rule, and minimum-data behavior remain unresolved. Each must define symbols, bounds, numerical behavior, complexity, and independent correctness fixtures before selection.
