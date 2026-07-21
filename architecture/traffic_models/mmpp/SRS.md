# MMPP Software Requirements Specification

## Current Requirements

- **TMM-FR-001:** The model shall remain unselectable until state, rate, fitting, mark, and generation semantics are complete.
- **TMM-IF-001:** Current registries shall reject `mmpp`.
- **TMM-NFR-001:** Future hidden-state canonicalization and numerical behavior shall be deterministic and bounded.
- **TMM-ERR-001:** No generic Markov/Poisson behavior shall be inferred as implementation.
- **TMM-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **TMM-AC-001:** All current model entry points reject `mmpp` as unsupported.
- **TMM-AC-002:** Gate opens only after full equations, constraints, fitting, schema, generation, complexity, and correctness tests exist.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
