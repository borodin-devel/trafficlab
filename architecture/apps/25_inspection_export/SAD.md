# Inspection Export Software Architecture Document

## Role

This auxiliary application converts one validated PCAPNG source into a
read-only inspection dataset package for ML development and LLM-assisted
inspection. It never changes, replaces, or becomes a prerequisite for the
canonical PCAPNG pipeline.

## Interface

```text
inspection_export --input-pcapng PATH --output-dir DIR
inspection_export --input-contract STATUS_PATH --input-member MEMBER \
  --output-dir DIR
```

Exactly one input mode is required. Direct-file mode accepts a capture PCAPNG;
the caller supplies an explicit source description and source hash because no
input contract supplies lineage. Contract mode requires the successful
capture-directions package's detached status and `MEMBER` exactly `target`,
`uplink`, or `downlink`. It validates status, exact membership, selected-member
digest, and PCAPNG before row decoding; no member defaults or is inferred.

## Output

The successful package contains:

```text
manifest.json
packets.parquet
inspection.jsonl
schema.json
launch.toml
```

The [inspection dataset contract](../../contracts/25_inspection_dataset/README.md)
owns package interoperability and its unresolved schema gate.

`packets.parquet` uses an Apache Arrow schema and stores one observation per
packet: capture timestamp, relative timestamp, captured and original lengths,
interface identity, direction when known, Ethernet and Internet Protocol
classification, and selected Transmission Control Protocol flags. Payload
bytes and addresses are excluded by default. `inspection.jsonl` renders the
same approved fields as one UTF-8 JSON object per line for bounded LLM review.
`schema.json` declares schema version, field meanings, null semantics, units,
and source-to-column mapping.

## Publication and Validation

The application validates PCAPNG closure and source hash, validates every
derived row against `schema.json`, hashes every non-manifest package member,
and atomically publishes the complete package. `manifest.json` excludes itself
and records source path/hash, decoder identity, schema version, exact
membership, row/exclusion counts, and member hashes. Detached
`artifact-status.toml` then binds the manifest and frozen launch record. A
partial, orphaned, unvalidated, or hash-mismatched package is not successful.

## Boundaries

This application does not classify direction, create a traffic model, fit a
model, generate traffic, score similarity, or modify PCAPNG. Direction facts
come only from [20 convert](../20_convert/README.md).

## Testing

Functional-core tests use small PCAPNG fixtures to verify exact field values,
null handling, deterministic ordering, schema validation, JSONL validity,
hashes/status, manifest self-entry rejection, and atomic publication.
Integration tests read produced Parquet using PyArrow. No test requires
elevation or a real capture workspace.

## Reading

Follow [architecture governance](../../README.md), [project implementation
structure](../../project/IMPLEMENTATION_STRUCTURE.md), and the relevant source
application owner before changing this application.

## Cross-Cutting Architecture

Schema mapping and row validation form the functional core; PCAPNG decoding,
PyArrow batching, JSONL writing, hashing, and publication form the shell.
Batch sizes bound memory, and logs contain counts rather than packet rows.
Untrusted PCAPNG, decompression/resource exhaustion, schema drift, and accidental
identifier disclosure are primary risks. Execution is offline and unprivileged.
