# Similarity Evaluation Software Architecture Document

## Role

This application compares one reference PCAPNG file with one generated PCAPNG
file using one named similarity method.

## Interface

The conceptual command is:

```text
similarity_evaluation --reference=PATH --generated=PATH --method=NAME --output-dir=DIR
```

It compares exactly the two supplied files and publishes `DIR/similarity.toml`
under the [similarity result contract](../../contracts/60_30_similarity_result/README.md).
The result contains no self-digest. After atomic publication to the absent
destination, detached `artifact-status.toml` binds its canonical bytes and the
immutable attempt launch record; a file without valid status is an orphan.
The selected [similarity method](../../similarity_methods/README.md) owns input
expectations, ranking interpretation, and any combined scoring behavior.

## Boundaries

This application does not combine results, define a genetic fitness policy,
select candidates, or mutate models. Those responsibilities belong to
[30 genetic training](../30_genetic_training/README.md).

## Diagnostics and Testing

Each attempt follows the shared
[startup record](../../CONFIGURATION.md#startup-record) rule. No
implementation exists yet. Future tests are unprivileged and verify
deterministic input validation, result validation, detached hashing, orphan
rejection, and atomic publication with temporary directories.

## Reading

Follow the [architecture governance](../../README.md), the selected
similarity-method owner, and the result contract before changing this
application.

## Cross-Cutting Architecture

Feature extraction and method scoring form deterministic pure behavior where
possible; PCAPNG input, library adapters, configuration, lineage, and publication
form the shell. Method owners specify complexity and numerical bounds; resource
admission guards expensive methods. Inputs and method configuration are untrusted.
Numerical/library failures never receive an invented fallback score.
