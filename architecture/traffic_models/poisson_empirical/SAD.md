# Poisson Empirical Software Architecture Document

## Context, Goals, and Boundaries

This baseline preserves an observed marginal frame-length distribution while
using independent homogeneous Poisson timing. It does not preserve timing-size
association, sequence, direction, protocol, or flow behavior.

## Structure and Data Flow

Preparation streams one validated Ethernet PCAPNG, counts original frame
lengths, sorts unique sizes, and embeds positive counts in model TOML. Sampling
uses a seeded categorical law independent of exponential IATs.

## Configuration, Errors, Performance, and Security

Invalid link type, original length, empty table, ordering, weight, rate, or stop
fails. Extraction is linear and bounded by at most the supported size-domain
cardinality. Input PCAPNG is untrusted; core sampling is offline/unprivileged.

## Testing, Decisions, Risks, and Limits

Fixture tests prove original rather than captured length. Golden categorical
samples and empirical proportions verify correctness. Main risk is overfitting
the marginal table while missing all conditional structure.
