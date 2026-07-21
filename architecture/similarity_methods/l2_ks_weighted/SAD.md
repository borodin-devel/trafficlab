# Weighted L2 KS Software Architecture Document

## Context, Structure, and Boundaries

This combined method invokes complete IAT and frame-size KS components then
forms their weighted sum. Both components must succeed even at zero weight.
It preserves component diagnostics and does not create a joint distribution or
measure count, sequence, flows, or protocol.

## Configuration, Errors, Performance, and Testing

Two finite nonnegative exact-decimal weights sum to one without silent
normalization. Score remains `[0,1]`, higher better. Component/config/numerical
failure fails entire result. Runtime is sum of component costs. Tests cover
boundary weights, exact sums, both components, zero-weight failures, and details.
