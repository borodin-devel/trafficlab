# State-Space Software Requirements Specification

## Current Requirements

- **TSS-FR-001:** The model shall remain unselectable until state/observation equations, inference, fitting, and generation are approved.
- **TSS-IF-001:** Current registries shall reject `state_space`.
- **TSS-NFR-001:** Future inference and sampling shall define deterministic numerical controls and bounded resources.
- **TSS-ERR-001:** No generic state-space library default shall substitute for missing architecture.
- **TSS-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **TSS-AC-001:** Current entry points reject `state_space` as unsupported.
- **TSS-AC-002:** Gate opens only after all equations, variables, constraints, inference/fitting, schema, generation, complexity, and tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
