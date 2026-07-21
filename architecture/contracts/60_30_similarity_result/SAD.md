# Similarity Result Contract Software Architecture Document

## Purpose

This contract owns `similarity.toml`, the result published by
[60 similarity evaluation](../../apps/60_similarity_evaluation/README.md) and
consumed by [30 genetic training](../../apps/30_genetic_training/README.md).

## Input Lineage

The result identifies the selected similarity-method name, one reference
PCAPNG input, and one generated PCAPNG input. It records lineage sufficient to
identify those exact input files.

## Method Lineage

The result identifies the selected method implementation and its version. It
also records the name and version of every mathematical library whose behavior
can affect the ranking result. A method without such a dependency records no
invented library identity.

## Result

Every successful evaluation publishes one `similarity.toml`. It records one
primary method-defined ranking result, the selected similarity-method identity,
method and input lineage, and validation status. It contains no digest of its
own bytes. External `artifact-status.toml` carries the SHA-256 of the final
canonical file and the immutable attempt `launch.toml` digest under the shared
[artifact protocol](../../libs/artifact_io/SAD.md#successful-status-envelope).
A consumer validates detached status and hashes before parsing the result.

The result may also contain method-defined component distances, component
scores, weights, and sample counts needed to explain the primary result. The
selected [similarity-method owner](../../similarity_methods/README.md) defines
their meanings. This contract defines no similarity formula, universal scoring
scale, or genetic fitness policy.

## Publication Invariants

The complete result, including every method-defined detail, is canonically
serialized, validated, hashed, and atomically published to an absent
destination. Successful detached status is published afterward. A missing,
partial, invalid, orphaned, or hash-mismatched result is not successful.
`launch.toml` follows the shared
[startup record](../../CONFIGURATION.md#startup-record) rules and remains
available even when this result is unsuccessful.

## Reading

Follow the [architecture governance](../../README.md). Read both application
owners and the selected [similarity method](../../similarity_methods/README.md)
before changing this contract.

## Architecture Qualities, Risks, and Testing

A shared envelope validator dispatches method details to the named registered
method schema. Canonical TOML and bounded diagnostics preserve determinism.
Paths, hashes, non-finite values, and untrusted method details are security and
validation boundaries; logs contain identities and reasons, not capture data.
Golden/mutation fixtures cover every registered method, self-digest rejection,
detached status, and producer-consumer compatibility. Primary risk is schema
drift between method, evaluation, and training; shared versioned validators
prevent independent interpretation.
