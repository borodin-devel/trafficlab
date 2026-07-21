# Frame-Size KS Similarity Method

## Purpose and Interface

`frame_size_ks` compares marginal original Ethernet frame-length distributions
with two-sided KS distance and similarity `1-D`. It is mature/selectable.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are reference/generated Ethernet PCAPNG. Output is similarity, raw
distance, and counts. It depends on [KS family](../KS.md); no settings exist.

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
