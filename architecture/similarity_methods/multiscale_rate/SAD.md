# Multi-Scale Rate Software Architecture Document

## Context, Structure, and Boundaries

This method measures burst/idle rate shape through packet-count and original-
byte-count bins at configured scales. It loses order/exact timing within bins,
timing-size association, and all frames after horizon.

Canonical aligned timestamps fill every half-open bin, including zeros. Pure
vector logic computes normalized L1 packet/byte distances, combines exact
unit-sum feature weights per scale, then exact unit-sum scale weights and `1-D`.

## Errors, Performance, Security, and Testing

Positive finite horizon/widths and valid weights are required. Empty input or
undefined denominator fails. Complexity is linear in frames plus total bins;
configuration/resource validation bounds bin explosion. Offline untrusted input
uses temporal rules. Tests cover boundaries, empty bins, horizon exclusion,
vectors, weights, score bounds, and diagnostics.
