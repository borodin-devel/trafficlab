# Similarity Methods

## Role

This directory contains named systems that rank the similarity of one reference
PCAPNG file and one generated PCAPNG file.

## Score Convention

Every similarity method declares its score range, direction, and meaning. Most
methods should use a normalized score in `[0, 1]`, with higher values meaning
more similarity, when that representation is mathematically correct.

Genetic algorithms do not require every fitness input to use `[0, 1]`.
Therefore, a method must retain and clearly document another range when
normalization would introduce an arbitrary scale or a misleading
interpretation.

## Implementation Policy

A similarity method should use an established, maintained, and well-tested
Python library when one provides the required mathematical behavior. A
handwritten implementation is allowed only when no suitable library exists or
available libraries cannot satisfy the method's documented requirements.

The method owner records the reason for any handwritten mathematics and
requires direct correctness tests against authoritative examples or an
independent implementation.

## Registered Methods

| Method | Owner |
| --- | --- |
| `iat_ks` | [IAT KS](iat_ks/README.md) |
| `frame_size_ks` | [frame-size KS](frame_size_ks/README.md) |
| `l2_ks_weighted` | [weighted L2 KS](l2_ks_weighted/README.md) |
| `joint_sinkhorn_wasserstein` | [joint Sinkhorn/Wasserstein](joint_sinkhorn_wasserstein/README.md) |
| `multiscale_rate` | [multi-scale rate](multiscale_rate/README.md) |
| `neighbor_transition` | [neighbour transition](neighbor_transition/README.md) |
| `autocorrelation` | [autocorrelation](autocorrelation/README.md) |
| `sequence_kernel_mmd` | [sequence-kernel MMD](sequence_kernel_mmd/README.md) |

## Unresolved, Unselectable Methods

These planned research methods have component documentation but no approved
formula/configuration. Similarity evaluation must reject them until their
roadmap specification gate passes:

| Planned name | Owner |
| --- | --- |
| `cross_correlation_rate` | [Rate cross-correlation](cross_correlation_rate/README.md) |
| `dtw_multiscale_rate` | [Multi-scale rate DTW](dtw_multiscale_rate/README.md) |
| `hurst_parameter` | [Hurst parameter](hurst_parameter/README.md) |
| `mutual_information_lag` | [Lagged mutual information](mutual_information_lag/README.md) |
| `spectral_density` | [Spectral density](spectral_density/README.md) |
| `wavelet_scaling` | [Wavelet scaling](wavelet_scaling/README.md) |

The [KS family](KS.md) owns behavior shared by `iat_ks`, `frame_size_ks`, and
`l2_ks_weighted`. `ks` is not a selectable similarity-method name.

The [temporal Layer 2 rules](TEMPORAL_L2.md) own behavior shared by
`joint_sinkhorn_wasserstein`, `multiscale_rate`, `neighbor_transition`,
`autocorrelation`, and `sequence_kernel_mmd`. It is not a selectable
similarity-method name.

## Layout

Each system belongs in `similarity_methods/<name>/`. Its `README.md` owns the
accepted PCAPNG inputs, configuration, ranking interpretation, validation, and
any combined scoring behavior.

[60 similarity evaluation](../apps/60_similarity_evaluation/README.md)
selects one named system for a comparison. It does not add an
application-specific fitness policy. A combined ranking system is registered
as another similarity method with its own name and owner document.

## Reading

Follow the [architecture governance](../README.md). Read the selected method
owner and any shared family document it references before changing similarity
evaluation or its result contract.

For any of those five advanced methods, [Temporal Layer 2
Similarity Rules](TEMPORAL_L2.md) must also be read.

Every method directory contains a concise README, SAD, testable SRS,
configuration status, and implementation/testing roadmap. Complex methods use
ordered supporting documents for mathematical stages and numerical validation.
