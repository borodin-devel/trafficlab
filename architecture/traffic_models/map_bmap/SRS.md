# MAP/BMAP Software Requirements Specification

## Current Requirements

- **TMB-FR-001:** The model shall remain unselectable until complete MAP/BMAP mathematics and semantics are approved.
- **TMB-IF-001:** Current registries shall reject `map_bmap`.
- **TMB-NFR-001:** Future matrices, fitting, and sampling shall have deterministic canonical representation and numerical bounds.
- **TMB-ERR-001:** Invalid generator/batch semantics shall never receive silent repair.
- **TMB-TST-001:** Current registry tests shall verify selection rejection.

## Acceptance Criteria

- **TMB-AC-001:** All current model entry points reject `map_bmap` as unsupported.
- **TMB-AC-002:** Gate opens only after variables, matrix constraints, batch ordering, fitting/generation, schema, complexity, and correctness tests are complete.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
