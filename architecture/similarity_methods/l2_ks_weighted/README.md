# Weighted L2 KS Similarity Method

## Purpose and Interface

`l2_ks_weighted` combines IAT and original-frame-length KS similarities using
explicit nonnegative unit-sum weights. It is mature/selectable.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are reference/generated Ethernet PCAPNG and two weights. Output is one
primary weighted score plus both component results. It depends on both KS methods.

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
[Combined score](01_COMBINED_SCORE_VALIDATION.md) ·
[Limits and testing](02_LIMITS_TESTING.md) · [Roadmap](ROADMAP.md)
