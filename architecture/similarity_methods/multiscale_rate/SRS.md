# Multi-Scale Rate Software Requirements Specification

## Requirements

- **MMR-FR-001:** For each positive width, method shall create `ceil(H/w)` half-open bins from zero to positive horizon.
- **MMR-FR-002:** Every frame in `[0,H)` shall contribute to exactly one packet and original-byte bin; later frames shall be counted as excluded.
- **MMR-FR-003:** Zero-count bins shall remain in both vectors.
- **MMR-FR-004:** Component distance shall be `sum|a-b|/sum(a+b)`.
- **MMR-FR-005:** Feature and scale weighted distance shall map to similarity `1-D`.
- **MMR-IF-001:** Result shall record each scale/vector distance, weights, bin count, horizon, and exclusions.
- **MMR-CFG-001:** Horizon/widths shall be finite positive/distinct; feature and scale weights positive exact unit sums.
- **MMR-NFR-001:** Score shall be deterministic in `[0,1]`, higher better.
- **MMR-ERR-001:** Empty input, invalid bins/weights, undefined denominator, or non-finite calculation shall fail.
- **MMR-TST-001:** Tests shall cover every bin boundary, zero bin, exclusion, and exact vector/distance.

## Acceptance Criteria

- **MMR-AC-001:** Hand-binned fixtures reproduce all vectors, component distances, combined score, and exclusions.
- **MMR-AC-002:** Invalid/degenerate configurations and empty/malformed inputs produce no successful result.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Binning](01_CONFIGURATION_BINS.md) ·
[Distance and result](02_DISTANCE_RESULT_LIMITS.md) · [Roadmap](ROADMAP.md)
