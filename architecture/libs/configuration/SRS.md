# Configuration Software Requirements Specification

## Requirements

- **CFG-FR-001:** The library shall accept at most one explicit file or directory selector.
- **CFG-FR-002:** It shall resolve defaults, selected TOML, then matching CLI overrides.
- **CFG-FR-003:** It shall write a startup record before application work.
- **CFG-FR-004:** It shall validate a managed `--attempt-dir` or securely create the documented direct-attempt directory before configuration resolution.
- **CFG-IF-001:** Applications shall provide owned schema and default definitions.
- **CFG-IF-002:** Every application CLI shall accept `--attempt-dir PATH` as a non-TOML infrastructure argument required for managed invocation.
- **CFG-NFR-001:** Resolution and serialization shall be deterministic.
- **CFG-ERR-001:** Missing, malformed, unknown, or invalid configuration shall fail without partial application.
- **CFG-ERR-002:** Invalid attempt type, path, ownership, mode, emptiness, aliasing, or managed containment shall fail before any application write or launch.
- **CFG-SEC-001:** Secret-bearing fields shall require explicit safe recording rules.
- **CFG-TST-001:** Tests shall cover all precedence and selector combinations.

## Acceptance Criteria

- **CFG-AC-001:** A precedence matrix yields exactly the documented effective values.
- **CFG-AC-002:** Every resolution failure retains a valid failure startup record.
- **CFG-AC-003:** Managed-attempt boundary fixtures accept only the assigned empty private directory, while direct fixtures create collision-resistant private attempts under `run/`.

## Traceability

[Shared rules](../../CONFIGURATION.md) · [SAD](SAD.md) · [Roadmap](ROADMAP.md)
