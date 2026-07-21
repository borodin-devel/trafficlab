# Similarity Result Contract

## Purpose and Responsibilities

This contract defines `similarity.toml`, published by similarity evaluation
and consumed as candidate evidence by genetic training. Its documentation
identifier is `60_30_similarity_result`.

## Producers, Consumers, Inputs, and Outputs

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md)
produces one result for exact reference/generated inputs and one method.
[Genetic training](../../apps/30_genetic_training/README.md) validates and
interprets the method-declared ranking result.

## Dependencies and Interface

The contract uses TOML, SHA-256, method identity/version, dependency lineage,
input hashes, primary result, validation state, and optional method diagnostics.
The result never hashes itself; detached successful status carries its exact
content digest.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
