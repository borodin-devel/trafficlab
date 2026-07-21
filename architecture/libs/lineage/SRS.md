# Lineage Software Requirements Specification

## Requirements

- **LIN-FR-001:** The library shall compute SHA-256 over exact file bytes.
- **LIN-FR-002:** It shall build deterministic provenance records from explicit values.
- **LIN-FR-003:** It shall validate declared local parent and member hashes.
- **LIN-FR-004:** It shall reject a digest whose hash domain contains its carrying field, including any artifact or manifest claiming a digest of its own complete bytes; explicitly delimited payload digests remain valid.
- **LIN-FR-005:** It shall validate a package manifest digest before using that manifest to validate non-manifest members.
- **LIN-IF-001:** Contract owners shall supply required lineage fields and paths.
- **LIN-NFR-001:** Hashing shall use bounded memory and deterministic ordering.
- **LIN-ERR-001:** Missing, changed, mismatched, or cyclic invalid lineage shall fail.
- **LIN-SEC-001:** Relative lineage paths shall reject traversal and root escape.
- **LIN-TST-001:** Tests shall use published SHA-256 vectors and mutation fixtures.

## Acceptance Criteria

- **LIN-AC-001:** Exact known vectors and multi-file fixtures produce expected hashes.
- **LIN-AC-002:** One-byte mutation and broken-parent fixtures fail deterministically.
- **LIN-AC-003:** Package, single-file, detached-status, and forbidden-self-hash fixtures produce the documented validation results.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md) · [System SRS](../../project/SRS.md)
