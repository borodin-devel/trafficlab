# Capture Directions Contract Software Requirements Specification

## Requirements

- **CDR-FR-001:** The package shall contain exactly `manifest.json`, `target.pcapng`, `uplink.pcapng`, `downlink.pcapng`, `direction-report.json`, and `launch.toml`.
- **CDR-FR-002:** `target.pcapng` shall be the unchanged complete input.
- **CDR-FR-003:** Directional files shall contain only confidently classified packets and preserve their bytes and metadata.
- **CDR-IF-001:** Pipeline source shall identify canonical capture; external source shall include description, hash, and reference profile.
- **CDR-IF-002:** Manifest shall identify source provenance, exact allowed membership, and the content hash of every non-manifest member while excluding itself; detached successful status shall bind the canonical manifest and frozen launch record.
- **CDR-IF-003:** Report shall contain profile, artifact counts, exclusion counts, and reasons.
- **CDR-IF-004:** A directional consumer shall receive explicit detached status and exactly one `target`, `uplink`, or `downlink` selection; no member shall default.
- **CDR-NFR-001:** Package membership, reports, and serialization shall be deterministic.
- **CDR-ERR-001:** Missing, extra, partial, unclosed, invalid, orphaned, or hash-mismatched content or status shall invalidate the package.
- **CDR-SEC-001:** Relative members shall reject path traversal and external substitution.
- **CDR-TST-001:** Contract fixtures shall cover exact preservation, exclusions, hash mutation, and package membership.

## Acceptance Criteria

- **CDR-AC-001:** Producer and consumer validators accept the same valid golden package.
- **CDR-AC-002:** Removing, adding, mutating, or swapping any member or detached status causes both validators to reject it; no fixture requires a manifest to hash itself.
- **CDR-AC-003:** Missing, unknown, repeated, or ambiguous directional selection fails before a consumer reads packet data.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md) · [Convert SRS](../../apps/20_convert/SRS.md) ·
[Genetic training SRS](../../apps/30_genetic_training/SRS.md) ·
[Inspection export SRS](../../apps/25_inspection_export/SRS.md)
