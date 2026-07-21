# Artifact I/O Software Requirements Specification

## Requirements

- **ART-FR-001:** The library shall stage an artifact outside its successful destination.
- **ART-FR-002:** The library shall validate every required member before publication.
- **ART-FR-003:** The library shall publish a complete artifact with one atomic filesystem operation.
- **ART-FR-004:** A package manifest shall hash every non-manifest member and shall not list or hash itself.
- **ART-FR-005:** The library shall atomically publish one closed `<attempt>/artifact-status.toml` only after artifact and startup-record validation and shall never overwrite it.
- **ART-IF-001:** Callers shall supply explicit destination paths and validators.
- **ART-IF-002:** Callers shall provide distinct attempt and absent artifact-destination paths plus immutable startup-record identity.
- **ART-IF-003:** Successful status shall use the exact version 1 schema, path rules, and detached digest domains in the SAD.
- **ART-NFR-001:** Member ordering and manifest serialization shall be deterministic.
- **ART-NFR-002:** Hashing and copying shall use bounded memory.
- **ART-ERR-001:** Failure shall not expose a partial destination as successful.
- **ART-ERR-002:** A destination without valid detached status shall be rejected as an orphan and shall not be overwritten or resumed automatically.
- **ART-SEC-001:** Package members shall reject absolute paths and traversal.
- **ART-SEC-002:** Status shall be a bounded same-user mode-`0600` regular single-link file read without following symlinks.
- **ART-TST-001:** Every write, validation, hash, close, and publication failure point shall be injectable in tests.

## Acceptance Criteria

- **ART-AC-001:** Injected failures leave no successful partial artifact.
- **ART-AC-002:** Identical members yield identical manifest ordering and hashes.
- **ART-AC-003:** Golden package/file statuses validate detached artifact and launch digests; self-hash, mutation, path, permission, and orphan fixtures fail.

## Traceability

Architecture: [SAD.md](SAD.md). Implementation: [ROADMAP.md](ROADMAP.md).
