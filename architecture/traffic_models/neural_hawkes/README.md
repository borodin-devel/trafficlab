# Neural Hawkes Traffic Model

## Purpose and Interface

`neural_hawkes` is a mature selectable causal Layer 2 marked temporal point
process generating one event at a time from finite earlier history.

## Inputs, Outputs, Dependencies, and Configuration

Fitting consumes prepared reference windows; generation consumes a
generation-ready trained model and outputs timestamp/frame-length events. Shared source,
split, and lineage rules are in [neural common rules](../NEURAL_MARKED_POINT_PROCESS.md).

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) publishes immutable
builders, [genetic training](../../apps/30_genetic_training/README.md) applies
candidate hyperparameters before fitting local weights, and
[traffic generation](../../apps/50_traffic_generation/README.md) executes
generation-ready models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents and Reading Order

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[General information](00_GENERAL_INFO.md) · [Representation and laws](01_REPRESENTATION_PROBABILITY.md) ·
[Fitting and selection](02_FITTING_SELECTION.md) · [Serialization and generation](03_SERIALIZATION_GENERATION.md) ·
[Testing and numerics](04_TESTING_NUMERICS.md) · [Roadmap](ROADMAP.md)
