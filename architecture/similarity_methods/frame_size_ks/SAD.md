# Frame-Size KS Software Architecture Document

## Context, Structure, and Boundaries

This baseline measures only marginal original Ethernet frame-length distribution.
Extraction never substitutes captured length/bytes. A maintained two-sided KS
implementation returns `D`; score is `1-D`; p-value is discarded. It does not
measure timing, order, count, content, flow, or protocol.

## Errors, Performance, Security, and Testing

Each input needs one valid Ethernet frame. Valid lengths outside current model
generation support remain in sample to expose mismatch. Missing/inconsistent
original-length metadata or library failure fails. Tests cover truncation,
ties, unequal counts, known KS, unsupported links, and byte-invariance.
