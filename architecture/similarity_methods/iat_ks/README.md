# IAT KS Similarity Method

## Purpose and Interface

`iat_ks` compares marginal frame inter-arrival-time distributions using
two-sided Kolmogorov-Smirnov distance and similarity `1-D`. It is mature/selectable.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are one reference and generated Ethernet PCAPNG. Output is primary
similarity plus raw distance/count diagnostics. It depends on
[KS family](../KS.md) and a maintained statistic library; no method settings exist.

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
