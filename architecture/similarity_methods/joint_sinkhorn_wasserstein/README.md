# Joint Sinkhorn/Wasserstein Similarity Method

## Purpose and Interface

`joint_sinkhorn_wasserstein` compares joint transformed IAT/frame-length clouds
with debiased entropic Sinkhorn divergence and exponential score mapping.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are reference/generated Ethernet PCAPNG and explicit scales/solver
settings. Output is similarity, raw divergence, convergence, and library lineage.

## Related Components and Execution

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md) hosts the
method offline and unprivileged. [PCAP I/O](../../libs/pcap_io/README.md) owns
input mechanics. The
[similarity-result contract](../../contracts/60_30_similarity_result/README.md) owns
publication.
Unresolved methods remain unselectable.

## Documents and Reading Order

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[General information](00_GENERAL_INFO.md) · [Transport mathematics](01_TRANSPORT_MATHEMATICS.md) ·
[Numerics and testing](02_NUMERICS_TESTING.md) · [Roadmap](ROADMAP.md)
