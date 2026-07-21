# Similarity Result Contract Software Requirements Specification

## Requirements

- **SMR-FR-001:** Every successful evaluation shall publish exactly one `similarity.toml`.
- **SMR-FR-002:** The result shall identify exact reference and generated PCAPNG lineage.
- **SMR-FR-003:** It shall identify method implementation and every result-affecting mathematical dependency version.
- **SMR-FR-004:** It shall contain one method-defined primary ranking result and validation status.
- **SMR-IF-001:** Optional components, weights, counts, and diagnostics shall follow the selected method schema.
- **SMR-IF-002:** The result shall contain no self-digest; detached successful status shall bind the canonical result bytes and immutable launch record.
- **SMR-NFR-001:** TOML serialization and method detail ordering shall be deterministic.
- **SMR-ERR-001:** Missing, partial, invalid, orphaned, non-finite where forbidden, self-hashing, or hash-mismatched results shall be unsuccessful.
- **SMR-TST-001:** Every mature method shall provide valid and invalid contract fixtures.

## Acceptance Criteria

- **SMR-AC-001:** Evaluation and training validators accept every method's valid golden result and preserve its score direction/range metadata.
- **SMR-AC-002:** Input-hash, method, dependency, score, detail, file-content, launch, and detached-status mutations are detected.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md) ·
[Evaluation SRS](../../apps/60_similarity_evaluation/SRS.md) ·
[Training SRS](../../apps/30_genetic_training/SRS.md)
