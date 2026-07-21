# Multi-Scale Rate DTW Similarity — Unresolved

## Purpose and Status

The planned `dtw_multiscale_rate` method would use dynamic time warping over
packet/byte rate series at multiple scales. It is a research stub, not
registered or selectable.

## Inputs, Outputs, and Dependencies

Binning, local cost, path constraints, normalization, scale aggregation, score,
and complexity controls are unresolved.

## Related Components and Execution

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md) hosts the
method offline and unprivileged. [PCAP I/O](../../libs/pcap_io/README.md) owns
input mechanics. The
[similarity-result contract](../../contracts/60_30_similarity_result/README.md) owns
publication.
Unresolved methods remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
