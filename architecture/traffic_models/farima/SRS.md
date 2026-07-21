# FARIMA Software Requirements Specification

## Current Requirements

- **TFA-FR-001:** The model shall remain unselectable until event domain, equations, fitting, generation, and schema are approved.
- **TFA-IF-001:** Registries shall reject `farima` as unsupported in the current version.
- **TFA-NFR-001:** Future design shall define deterministic numerical behavior and bounded resource use.
- **TFA-ERR-001:** No application shall substitute a generic time-series implementation for this stub.
- **TFA-TST-001:** A registry test shall prove current selection rejection.

## Acceptance Criteria

- **TFA-AC-001:** Model creation, training, and generation reject `farima` with documented unsupported status.
- **TFA-AC-002:** Selection gate opens only after SAD/SRS/configuration define all mathematical variables, constraints, algorithms, and correctness tests.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
