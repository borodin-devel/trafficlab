# Sequence-Kernel MMD Similarity Method

## Purpose and Interface

`sequence_kernel_mmd` compares empirical collections of ordered within-window
timing/size paths using normalized truncated signature kernel MMD.

## Inputs, Outputs, Dependencies, and Configuration

Inputs are reference/generated Ethernet PCAPNG and explicit window, feature,
kernel, degree, numerical, and score scales. Output is mapped similarity plus
raw MMD/kernel diagnostics.

## Related Components and Execution

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md) hosts the
method offline and unprivileged. [PCAP I/O](../../libs/pcap_io/README.md) owns
input mechanics. The
[similarity-result contract](../../contracts/60_30_similarity_result/README.md) owns
publication.
Unresolved methods remain unselectable.

## Documents and Reading Order

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[General information](00_GENERAL_INFO.md) · [Windows and paths](01_WINDOWS_PATHS.md) ·
[Kernel and MMD](02_KERNEL_MMD.md) · [Numerics and testing](03_NUMERICS_TESTING.md) ·
[Roadmap](ROADMAP.md)
