# Packet-Train Traffic Model — Unresolved

## Purpose and Status

This planned component would emit bounded packet bursts separated by gaps. It
is a research stub, not registered or selectable.

## Inputs, Outputs, and Dependencies

Train arrival, packet-count, intratrain timing, gaps, size marks, overlap,
fitting, schema, and stop semantics are unresolved.

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) prepares model
files, [genetic training](../../apps/30_genetic_training/README.md) may fit
candidates, and [traffic generation](../../apps/50_traffic_generation/README.md)
executes validated models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
