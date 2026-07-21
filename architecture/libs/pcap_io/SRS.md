# PCAPNG I/O Software Requirements Specification

## Requirements

- **PCP-FR-001:** The library shall parse supported PCAPNG in recorded packet order.
- **PCP-FR-002:** The library shall expose interface, timestamp, captured-length, original-length, and packet-byte metadata.
- **PCP-FR-003:** Filtering shall preserve every retained packet's represented metadata and bytes.
- **PCP-IF-001:** Operations shall receive explicit paths or streams and explicit validation policies.
- **PCP-NFR-001:** Parsing and writing shall use bounded memory for configured input limits.
- **PCP-NFR-002:** Deterministic input and policy shall yield deterministic record ordering and output.
- **PCP-ERR-001:** Malformed structure or invalid required metadata shall fail without silent repair.
- **PCP-SEC-001:** PCAPNG shall be treated as untrusted input.
- **PCP-TST-001:** Fixture tests shall cover malformed blocks, interfaces, resolution, offsets, and truncation.

## Acceptance Criteria

- **PCP-AC-001:** Retained packet records round-trip with required metadata unchanged.
- **PCP-AC-002:** Every documented malformed fixture fails with its expected typed reason.

## Traceability

Architecture: [SAD.md](SAD.md). Delivery: [ROADMAP.md](ROADMAP.md).
