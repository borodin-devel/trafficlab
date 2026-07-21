# Similarity Evaluation Application

## Purpose and Responsibilities

`similarity_evaluation` validates one reference and one generated PCAPNG,
executes one named similarity method, and publishes one similarity result.

## Inputs, Outputs, and Interface

Its CLI accepts explicit reference, generated, method, and output-directory
arguments. Output follows the
[similarity-result contract](../../contracts/60_30_similarity_result/README.md).

## Configuration, Dependencies, and Execution

The application runs offline and unprivileged and depends on the selected
method and shared file libraries. Method settings remain method-owned.

The [similarity-method registry](../../similarity_methods/README.md) owns score
mathematics. [Genetic training](../30_genetic_training/README.md) consumes valid
results; [PCAP I/O](../../libs/pcap_io/README.md) and
[artifact publication](../../libs/artifact_io/README.md) provide shared mechanics.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
