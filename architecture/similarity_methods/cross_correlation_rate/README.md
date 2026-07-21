# Rate Cross-Correlation Similarity — Unresolved

## Purpose and Status

The planned `cross_correlation_rate` method would compare rate-series shape
under bounded time shifts.
It is a research stub, not registered or selectable.

## Inputs, Outputs, and Dependencies

Rate construction, lag domain, normalization, score mapping, configuration,
numerical handling, and minimum input are unresolved.

## Related Components and Execution

[Similarity evaluation](../../apps/60_similarity_evaluation/README.md) hosts the
method offline and unprivileged. [PCAP I/O](../../libs/pcap_io/README.md) owns
input mechanics. The
[similarity-result contract](../../contracts/60_30_similarity_result/README.md) owns
publication.
Unresolved methods remain unselectable.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
