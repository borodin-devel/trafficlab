# Neighbour-Transition Similarity Method

## Purpose and Interface

`neighbor_transition` compares adjacent joint timing/size state transitions
using base-2 Jensen-Shannon distance and `1-distance` similarity.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are reference/generated Ethernet PCAPNG and a complete joint-state
partition. Output is similarity plus state/transition diagnostics.

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
