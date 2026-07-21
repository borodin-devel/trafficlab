# Markov Renewal Traffic Model

## Purpose and Interface

`markov_renewal` learns first-order transitions between joint IAT/frame-length
states and emits observed pairs. It is a mature selectable Layer 2 model.

## Inputs, Outputs, Dependencies, and Configuration

Preparation consumes one fully configured candidate plus one ordered Ethernet
PCAPNG; generation consumes one self-contained generation-ready model and
outputs timestamp/frame-length events.

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) publishes immutable
builders, [genetic training](../../apps/30_genetic_training/README.md) prepares
candidates, and [traffic generation](../../apps/50_traffic_generation/README.md)
executes generation-ready models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents and Reading Order

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[General information](00_GENERAL_INFO.md) · [Reference preparation](01_REFERENCE_PREPARATION.md) ·
[State construction](02_STATE_CONSTRUCTION.md) · [Generation and validation](03_GENERATION_VALIDATION.md) ·
[Roadmap](ROADMAP.md)
