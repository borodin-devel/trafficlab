# Packet-Train Software Requirements Specification

## Current Requirements

- **TPT-FR-001:** The model shall remain unselectable until train, gap, size, fitting, and stop semantics are complete.
- **TPT-IF-001:** Current registries shall reject `packet_train`.
- **TPT-NFR-001:** Future trains and output shall have explicit deterministic ordering and resource bounds.
- **TPT-ERR-001:** Duration boundaries shall not silently trim trains without approved policy.
- **TPT-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **TPT-AC-001:** Current entry points reject `packet_train` as unsupported.
- **TPT-AC-002:** Gate opens only after complete variables, distributions, fitting, schema, stops, complexity, and tests.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
