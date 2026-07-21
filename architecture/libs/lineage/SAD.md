# Lineage Software Architecture Document

## Context, Goals, and Boundaries

Reproducible artifacts must identify exact source bytes and algorithm context.
The library owns generic hashing, canonical provenance values, and lineage
validation. Contract owners decide which fields are required.

## Structure and Data Flow

A streaming hasher returns lowercase SHA-256 identifiers. Pure builders order
paths, implementation identities, dependency versions, seeds, configuration
identities, and parent hashes canonically. Validators resolve each declared
local artifact and compare its actual hash.

A digest's hash domain never contains the field carrying that digest. No
artifact file or package manifest claims a digest of its own complete bytes.
Package manifests hash every non-manifest member; detached publication status
hashes the final manifest. A single-file artifact's complete-file digest is
likewise carried by detached status. This does not prohibit a file from
carrying a digest of a distinct, explicitly delimited payload. Consumers
validate a detached digest before trusting the artifact to resolve member or
parent lineage.

## Errors, Security, Performance, and Observability

Missing files, path traversal, mutation during hashing, invalid hashes, broken
parents, or unsupported lineage versions fail. Bounded reads avoid loading
artifacts in memory. Logs report identities and failures, never packet content.

## Testing, Decisions, Risks, and Limits

Known-vector, mutation, ordering, hash-domain, and graph tests establish
correctness. Each contract owns its canonical JSON/TOML serialization and
field order. External sources are snapshotted or checked for identity changes
during bounded hashing, then recorded by normalized path and exact-byte digest.
