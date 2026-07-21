# Inspection Dataset Contract Software Architecture Document

## Context, Goals, and Boundaries

The package provides two equivalent derived views without replacing or
modifying PCAPNG. `schema.json` owns field types, units, nulls, and source
mapping; Parquet supports typed ML access and JSONL bounded textual inspection.
Default schema excludes payload bytes and addresses.

## Data Flow, Validation, and Security

One source packet order maps to deterministic rows. `manifest.json` binds exact
source hash, decoder/schema versions, counts/exclusions, exact allowed member
names, and the SHA-256 of every non-manifest member, including frozen
`launch.toml`. It excludes itself and rejects absolute or traversing member
paths. After canonical serialization, external `artifact-status.toml` binds
the manifest and attempt launch digests under the shared
[artifact protocol](../../libs/artifact_io/SAD.md#successful-status-envelope).
Consumers validate status and manifest before member data. The package
publishes atomically after schema and cross-encoding validation; status follows
publication, and a package without valid status is an orphan, not success.
PCAPNG and consumer paths are untrusted. Privacy allowlist, row/file limits,
and no raw packet leakage are mandatory.

## Performance, Logging, Testing, Risks, and Limits

Streaming Arrow batches and JSON lines bound memory. Logs contain counts only.
Golden cross-encoding, PyArrow, schema, privacy, hash, mutation, and large-stream
tests are required. Exact columns/types/compression/batch/file limits are
unresolved; the contract cannot be implemented before those decisions.
