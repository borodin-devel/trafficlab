# Backup Script Software Requirements Specification

## Requirements

- **SBK-FR-001:** The script shall read only durable values explicitly named by the approved setup plan.
- **SBK-FR-002:** It shall display original and proposed public values and shall never display protected values or restoration arguments.
- **SBK-FR-003:** It shall atomically store protected originals/restoration data in a dedicated owner-only private file and record only its relative reference, digest, names, reader identity, and outcome in the ordinary manifest.
- **SBK-IF-001:** Input shall identify one unambiguous workspace and exact value list.
- **SBK-IF-002:** Every requested value shall be classified as public or protected before any read.
- **SBK-NFR-001:** Repeated reads of unchanged values shall produce deterministic records.
- **SBK-SEC-001:** It shall perform no system mutation and shall reject unspecified values.
- **SBK-SEC-002:** Protected storage shall use no-follow contained path resolution, directory mode at most `0700`, exact file mode `0600`, verified ownership, and no protected plaintext in any other persisted or operator-facing sink.
- **SBK-ERR-001:** Read or identity failure shall prevent a valid backup record.
- **SBK-TST-001:** Automated tests shall use temporary fixtures, fake readers, and protected sentinel values.

## Acceptance Criteria

- **SBK-AC-001:** Scope, public displayed diff, protected private record, manifest metadata, restoration output, and no-mutation spies match fixtures.
- **SBK-AC-002:** Ambiguous identity or unspecified value fails before any protected read outside scope.
- **SBK-AC-003:** No protected sentinel appears in console, logs, launch data, or ordinary manifest; wrong private path/type/owner/mode/digest fails.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md)
