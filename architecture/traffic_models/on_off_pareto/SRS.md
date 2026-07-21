# ON/OFF Pareto Software Requirements Specification

## Current Requirements

- **TOP-FR-001:** The model shall remain unselectable until complete period, packet, fitting, and generation rules exist.
- **TOP-IF-001:** Current registries shall reject `on_off_pareto`.
- **TOP-NFR-001:** Future tail and resource behavior shall be explicit, deterministic, and bounded by stop policy.
- **TOP-ERR-001:** Invalid/infinite parameter regions shall not be silently truncated unless a future schema explicitly defines it.
- **TOP-TST-001:** Registry tests shall verify current rejection.

## Acceptance Criteria

- **TOP-AC-001:** Current entry points reject `on_off_pareto` as unsupported.
- **TOP-AC-002:** Gate opens only after distribution conventions, constraints, fitting, schema, generation, complexity, and statistical tests are complete.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
