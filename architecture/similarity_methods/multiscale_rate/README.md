# Multi-Scale Rate Similarity Method

## Purpose and Interface

`multiscale_rate` compares packet and original-byte count vectors across
configured time-bin scales using normalized L1 distance. It is mature/selectable.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are reference/generated Ethernet PCAPNG, horizon, widths, and unit-sum
feature/scale weights. Output is one score plus per-scale diagnostics.

## Related Components and Execution

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md) hosts the
method offline and unprivileged. [PCAP I/O](../../libs/pcap_io/README.md) owns
input mechanics. The
[similarity-result contract](../../contracts/60_30_similarity_result/README.md) owns
publication.
Unresolved methods remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[General information](00_GENERAL_INFO.md) ·
[Configuration and bins](01_CONFIGURATION_BINS.md) ·
[Distance and result](02_DISTANCE_RESULT_LIMITS.md) ·
[Testing](03_TESTING.md) · [Roadmap](ROADMAP.md)
