# Multi-Scale Rate DTW Software Requirements Specification

## Current Requirements

- **SDW-FR-001:** The method shall remain unselectable until binning, DTW, aggregation, and score semantics are complete.
- **SDW-IF-001:** Current registries shall reject `dtw_multiscale_rate`.
- **SDW-NFR-001:** Future warping shall be deterministic with explicit time/memory bounds.
- **SDW-ERR-001:** Invalid/unbounded paths shall not receive fallback scores.
- **SDW-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **SDW-AC-001:** Current evaluation rejects the method as unsupported.
- **SDW-AC-002:** Gate opens only after formulas, constraints, normalization, configuration, complexity, and independent tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
