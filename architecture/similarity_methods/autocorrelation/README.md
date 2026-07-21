# Autocorrelation Similarity Method

## Purpose and Interface

`autocorrelation` compares configured-lag autocorrelations of transformed IAT
and original frame-length sequences. It is mature/selectable.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are reference/generated Ethernet PCAPNG, positive lags, and unit-sum
feature/lag weights. Output is one score plus each correlation/distance.

## Related Components and Execution

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md) hosts the
method offline and unprivileged. [PCAP I/O](../../libs/pcap_io/README.md) owns
input mechanics. The
[similarity-result contract](../../contracts/60_30_similarity_result/README.md) owns
publication.
Unresolved methods remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[Definition](00_METHOD_DEFINITION.md) · [Roadmap](ROADMAP.md)
