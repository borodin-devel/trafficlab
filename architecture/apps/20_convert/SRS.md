# Convert Software Requirements Specification

## Scope and Assumptions

Convert processes one supported closed PCAPNG offline. Direction is defined only
at an explicit app-side observation boundary.

## Requirements

- **CNV-FR-001:** Convert shall preserve the complete input as unchanged `target.pcapng`.
- **CNV-FR-002:** It shall classify confident IP packets as uplink or downlink using only the reference profile.
- **CNV-FR-003:** It shall exclude and count ambiguous, unknown, and non-IP packets with reasons.
- **CNV-FR-004:** It shall preserve bytes, timestamps, lengths, and interface metadata of retained packets.
- **CNV-IF-001:** Pipeline input shall use canonical capture and metadata; external input shall include source hash, description, and profile.
- **CNV-IF-002:** Output shall follow the capture-directions package contract exactly.
- **CNV-CFG-001:** Reference profile serialization shall be explicit and validated before conversion.
- **CNV-NFR-001:** Identical input and profile shall produce deterministic package members and report ordering.
- **CNV-ERR-001:** An unclassifiable packet shall never be guessed into a direction.
- **CNV-SEC-001:** Input paths and PCAPNG shall be treated as untrusted.
- **CNV-TST-001:** Fixtures shall prove exact packet preservation and classification boundaries.

## Acceptance Criteria

- **CNV-AC-001:** Known uplink/downlink, ambiguous, non-IP, truncated, and multi-interface fixtures produce exact expected packets and counts.
- **CNV-AC-002:** Hash/status mismatch, manifest self-entry, malformed PCAPNG, invalid profile, orphan, or partial publication yields no successful package.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Contract](../../contracts/10_20_capture_directions/README.md)
