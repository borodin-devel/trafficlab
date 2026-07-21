# Wavelet-Scaling Software Requirements Specification

## Current Requirements

- **SWV-FR-001:** The method shall remain unselectable until series, transform, scale statistic, distance, and score are complete.
- **SWV-IF-001:** Current registries shall reject `wavelet_scaling`.
- **SWV-NFR-001:** Future transform/boundary behavior shall be deterministic with explicit resource bounds.
- **SWV-ERR-001:** Unsupported lengths/scales or invalid coefficients shall fail without silent padding policy.
- **SWV-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **SWV-AC-001:** Current evaluation rejects the method as unsupported.
- **SWV-AC-002:** Gate opens only after transform variables, boundary rules, statistic, schema, score, complexity, and independent tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
