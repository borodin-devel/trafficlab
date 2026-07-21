# Hurst-Parameter Software Requirements Specification

## Current Requirements

- **SHP-FR-001:** The method shall remain unselectable until series, estimator, assumptions, and score are complete.
- **SHP-IF-001:** Current registries shall reject `hurst_parameter`.
- **SHP-NFR-001:** Future estimator and scale selection shall be deterministic with stated complexity.
- **SHP-ERR-001:** Unidentifiable/invalid scaling shall fail rather than invent an exponent or score.
- **SHP-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **SHP-AC-001:** Current evaluation rejects the method as unsupported.
- **SHP-AC-002:** Gate opens only after estimator equations, assumptions, diagnostics, mapping, schema, and reference tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
