# Lagged Mutual-Information Software Requirements Specification

## Current Requirements

- **SMI-FR-001:** The method shall remain unselectable until variables, estimator, lag, aggregation, and score are complete.
- **SMI-IF-001:** Current registries shall reject `mutual_information_lag`.
- **SMI-NFR-001:** Future estimation and tie handling shall be deterministic with explicit sample/resource bounds.
- **SMI-ERR-001:** Undefined or statistically inadequate inputs shall fail without invented dependence.
- **SMI-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **SMI-AC-001:** Current evaluation rejects the method as unsupported.
- **SMI-AC-002:** Gate opens only after estimator equations/assumptions, schema, score, complexity, and independent statistical tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
