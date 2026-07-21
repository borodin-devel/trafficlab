# Markov-Modulated Poisson Process — Unresolved

## Purpose and Status

The planned `mmpp` component would vary Poisson arrival rate through hidden
Markov states. It is a research stub, not registered or selectable.

## Inputs, Outputs, and Dependencies

State process, initial law, rate/transition fitting, size marks, schema,
sampling, and resource limits are unresolved.

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) prepares model
files, [genetic training](../../apps/30_genetic_training/README.md) may fit
candidates, and [traffic generation](../../apps/50_traffic_generation/README.md)
executes validated models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
