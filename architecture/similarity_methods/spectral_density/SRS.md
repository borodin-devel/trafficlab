# Spectral-Density Software Requirements Specification

## Current Requirements

- **SPS-FR-001:** The method shall remain unselectable until series, spectrum, distance, and score semantics are complete.
- **SPS-IF-001:** Current registries shall reject `spectral_density`.
- **SPS-NFR-001:** Future frequency grid and numerical transform shall be deterministic and resource-bounded.
- **SPS-ERR-001:** Undefined/zero-power or inadequate records shall not receive invented spectra/scores.
- **SPS-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **SPS-AC-001:** Current evaluation rejects the method as unsupported.
- **SPS-AC-002:** Gate opens only after signal construction, estimator, frequency alignment, distance, schema, and reference tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
