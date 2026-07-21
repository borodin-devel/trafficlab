# Poisson Empirical Traffic Model

## Purpose and Interface

`poisson_empirical` generates Poisson arrivals and frame lengths sampled from
an exact reference-derived categorical frequency table. It is mature/selectable.

## Inputs, Outputs, Dependencies, and Configuration

Input is generation-ready TOML containing arrival rate, fitted frequency table,
seed, stop policy, and lineage. Output is timestamp/frame-length events.
Preparation consumes one reference Ethernet PCAPNG and depends on
[Poisson family](../POISSON.md).

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) publishes immutable
builders, [genetic training](../../apps/30_genetic_training/README.md) prepares
candidates, and [traffic generation](../../apps/50_traffic_generation/README.md)
executes generation-ready models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[Model definition](00_MODEL_DEFINITION.md) · [Roadmap](ROADMAP.md)
