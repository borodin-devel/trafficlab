# Poisson Uniform Traffic Model

## Purpose and Interface

`poisson_uniform` generates independent Poisson arrival times and independent
uniform integer Ethernet frame lengths. It is a mature selectable Layer 2 model.

## Inputs, Outputs, Dependencies, and Configuration

Input is its validated generation-ready self-describing TOML. Output is
timestamp/frame-length events for traffic generation. It depends on
[Poisson family](../POISSON.md); trainable fields are arrival rate and
inclusive length bounds.

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) publishes immutable
builders, [genetic training](../../apps/30_genetic_training/README.md)
finalizes candidates, and [traffic generation](../../apps/50_traffic_generation/README.md)
executes generation-ready models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[Model definition](00_MODEL_DEFINITION.md) · [Roadmap](ROADMAP.md)
