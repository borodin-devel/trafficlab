# Lagged Mutual-Information Similarity — Unresolved

## Purpose and Status

The planned `mutual_information_lag` method would compare nonlinear dependence
at configured packet lags. It is a research stub, not registered or selectable.

## Inputs, Outputs, and Dependencies

Features, estimator, discretization/kernel, bias correction, lags, aggregation,
score mapping, and minimum sample are unresolved.

## Related Components and Execution

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md) hosts the
method offline and unprivileged. [PCAP I/O](../../libs/pcap_io/README.md) owns
input mechanics. The
[similarity-result contract](../../contracts/60_30_similarity_result/README.md) owns
publication.
Unresolved methods remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
