# Autocorrelation Software Architecture Document

## Context, Structure, and Boundaries

This method measures configured linear repetition in transformed IAT and
original frame-length sequences. Pure extraction computes sample
autocorrelations at positive lags, normalized absolute cross-capture differences,
unit-sum feature/lag weighting, then `1-D`.

It cannot capture arbitrary nonlinear dependence, individual transitions,
content, flows, direction, or protocol.

## Errors, Performance, Security, and Testing

Each sequence must exceed maximum lag and have nonzero variance for both
features. Invalid weights/lags, constant series, insufficient data, or non-finite
correlation fails without dropping lags. Complexity is frames times lag count;
configuration must bound both. Tests cover positive/negative correlation,
constant/short series, exact weights, score bounds, and diagnostics.
