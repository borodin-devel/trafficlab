# IAT KS Software Architecture Document

## Context, Structure, and Boundaries

This baseline measures only marginal IAT distribution. Canonical extraction
retains PCAPNG order, interprets interface time resolution/offset, and subtracts
consecutive timestamps. A maintained two-sided KS implementation returns `D`;
score is `1-D`. P-values, packet count, order beyond extraction, lengths,
contents, flows, and protocols are outside scope.

## Errors, Performance, Security, and Testing

Each input needs two valid Ethernet frames. Zero IAT is valid; decreasing or
non-finite time fails without sorting/skip. Complexity follows library empirical
CDF computation and uses bounded extraction where supported. PCAPNG is untrusted
and offline. Tests cover ties, resolutions, offsets, unequal counts, known KS,
malformed data, score bounds, and absent p-value.
