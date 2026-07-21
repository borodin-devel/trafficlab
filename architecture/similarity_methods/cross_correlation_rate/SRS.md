# Rate Cross-Correlation Software Requirements Specification

## Current Requirements

- **SXC-FR-001:** The method shall remain unselectable until rate/lag/correlation/score semantics are complete.
- **SXC-IF-001:** Current registries shall reject `cross_correlation_rate`.
- **SXC-NFR-001:** Future numerical algorithm and tie handling shall be deterministic and resource-bounded.
- **SXC-ERR-001:** Constant/undefined series shall not receive an invented score.
- **SXC-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **SXC-AC-001:** Current evaluation rejects the method as unsupported.
- **SXC-AC-002:** Gate opens only after variables, formulas, boundaries, score, configuration, complexity, and correctness tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
