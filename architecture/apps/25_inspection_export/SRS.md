# Inspection Export Software Requirements Specification

## Scope and Assumptions

Inspection export serves developers and researchers needing typed packet
metadata and bounded text inspection. Source PCAPNG remains authoritative.

## Requirements

- **IEX-FR-001:** The application shall read one explicit validated PCAPNG source.
- **IEX-FR-002:** It shall publish one Parquet row and one JSONL object per approved packet observation.
- **IEX-FR-003:** It shall publish a schema defining field meaning, units, nulls, and source mapping.
- **IEX-FR-004:** It shall exclude payload bytes and addresses by default.
- **IEX-IF-001:** Input shall include source description and verified hash when no source contract supplies them.
- **IEX-IF-002:** Output shall contain `manifest.json`, `packets.parquet`, `inspection.jsonl`, `schema.json`, and `launch.toml`.
- **IEX-IF-003:** Directional-package input shall require explicit detached status and exactly one validated `target`, `uplink`, or `downlink` member with no default.
- **IEX-CFG-001:** Field-selection or privacy overrides shall remain unsupported until explicitly specified.
- **IEX-NFR-001:** Row order shall match source packet order and serialization shall be deterministic.
- **IEX-NFR-002:** Conversion shall use bounded memory suitable for large captures.
- **IEX-ERR-001:** Invalid source, row, schema, manifest membership, detached status, or hash shall prevent successful publication.
- **IEX-TST-001:** Fixture tests shall compare exact Parquet and JSONL logical values.

## Acceptance Criteria

- **IEX-AC-001:** Supported fixtures round-trip through PyArrow with exact field values, row order, nulls, and source lineage.
- **IEX-AC-002:** Privacy scan finds no payload bytes or address fields in default output.
- **IEX-AC-003:** Failure injection, ambiguous directional selection, manifest self-entry, or orphan output exposes no partial successful package.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Dataset contract](../../contracts/25_inspection_dataset/README.md)
