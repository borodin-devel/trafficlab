# Inspection Dataset Contract Software Requirements Specification

## Requirements

- **IXD-FR-001:** Package shall contain exactly `manifest.json`, `packets.parquet`, `inspection.jsonl`, `schema.json`, and `launch.toml`.
- **IXD-FR-002:** Parquet and JSONL shall represent the same approved logical rows in source packet order.
- **IXD-FR-003:** `schema.json` shall define schema version, fields, Arrow/JSON types, units, null semantics, and source mapping.
- **IXD-FR-004:** Default approved fields shall exclude payload bytes and addresses.
- **IXD-IF-001:** Manifest shall bind source hash/description, decoder/schema identity, row/exclusion counts, exact membership, and every non-manifest member hash while excluding itself; detached status shall bind the manifest and frozen launch record.
- **IXD-IF-002:** Exact columns, encodings, timestamp units, compression, batching, and size limits shall be resolved before implementation.
- **IXD-NFR-001:** Conversion and validation shall use bounded memory and deterministic ordering.
- **IXD-ERR-001:** Schema mismatch, cross-encoding mismatch, privacy violation, partial/extra member, orphaned status, or hash mismatch shall invalidate package.
- **IXD-TST-001:** Golden rows shall be read through PyArrow and JSON parser and compared logically.

## Acceptance Criteria

- **IXD-AC-001:** Contract remains blocked until every IXD-IF-002 item is explicit.
- **IXD-AC-002:** Golden package has exact equivalent rows, valid hashes/lineage, and no forbidden field/content.
- **IXD-AC-003:** Any member/schema/row/hash/status/privacy mutation fails validation, and no manifest self-hash is required.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md) · [Inspection export](../../apps/25_inspection_export/SRS.md)
