# FARIMA Traffic Model — Unresolved

## Purpose and Status

This planned component would model long-range-dependent traffic with a
fractionally integrated autoregressive moving-average family. It is a research
stub, not registered or selectable.

## Inputs, Outputs, and Dependencies

Input event representation, fitting source, output events, equations,
parameters, dependencies, and resource limits are unresolved. It must remain
outside model creation, training, and generation registries.

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) prepares model
files, [genetic training](../../apps/30_genetic_training/README.md) may fit
candidates, and [traffic generation](../../apps/50_traffic_generation/README.md)
executes validated models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
