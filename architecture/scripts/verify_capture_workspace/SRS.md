# Verify Script Software Requirements Specification

## Requirements

- **SVR-FR-001:** Verification shall compare identity, manifest, ownership, invoker health, state, and detectable resource drift.
- **SVR-FR-002:** It shall report ready, busy, unhealthy, missing, or indeterminate with findings.
- **SVR-IF-001:** Input shall identify one workspace and manifest explicitly.
- **SVR-NFR-001:** Identical observations shall produce deterministic status and finding order.
- **SVR-SEC-001:** Verification shall not repair, start, stop, install, configure, or remove anything.
- **SVR-ERR-001:** Missing mandatory observation shall yield indeterminate or documented non-ready state, never ready.
- **SVR-TST-001:** Tests shall use recorded manifests and fake observations.

## Acceptance Criteria

- **SVR-AC-001:** Ready, busy, unhealthy, missing, drift, and incomplete-observation fixtures match expected reports.
- **SVR-AC-002:** Side-effect spies record no mutation or child action beyond approved readers.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
