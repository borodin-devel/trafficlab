# Capture Readiness Contract Software Requirements Specification

## Requirements

- **RDN-FR-001:** The contract shall bind one readiness decision to exact request and workspace identity.
- **RDN-FR-002:** It shall distinguish supported capabilities/findings from blockers and successful readiness.
- **RDN-FR-003:** A successful decision shall contain no unresolved mandatory blocker.
- **RDN-IF-001:** It shall carry validation, producer identity/version, creation context, and source lineage.
- **RDN-IF-002:** The package shall contain exactly `manifest.json`, `capture-readiness.toml`, and `launch.toml` with detached successful status.
- **RDN-IF-003:** The decision shall use the closed version 1 TOML envelope, exact-byte request SHA-256, matching workspace/manifest identity, canonical UTC creation time, and bounded ordered capabilities/findings.
- **RDN-NFR-001:** Serialization and finding order shall be deterministic and bounded.
- **RDN-ERR-001:** Mismatch, stale/unknown mandatory evidence, blocker, malformed data, or invalid lineage shall prevent capture.
- **RDN-SEC-001:** The document shall not grant privilege and shall bind unambiguous workspace identity.
- **RDN-SEC-002:** Supported local use shall require same-user mode-`0600` request/status files and invoker peer credentials; cross-user or remote transport is unsupported.
- **RDN-TST-001:** Producer and consumer shall share golden/mutation/mismatch/freshness fixtures.

## Acceptance Criteria

- **RDN-AC-001:** Producer and consumer accept the same canonical ready/blocked fixtures and exact detached hashes.
- **RDN-AC-002:** Both validators reject every blocker/decision inconsistency, request/workspace mismatch, mutation, stale/future time, permission, ordering, and lineage fixture.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md) ·
[Capture request](../00_capture_request/SRS.md) ·
[Preflight](../../apps/00_preflight/SRS.md) · [Capture](../../apps/10_capture/SRS.md)
