# Neighbour-Transition Software Architecture Document

## Context, Structure, and Boundaries

This method measures one-step order in joint transformed-IAT/frame-length
states. Configuration defines a complete deterministic partition. Canonical
state sequences yield all ordered transition counts, including zero-count
pairs. Base-2 Jensen-Shannon distance maps to `1-distance` similarity.

It cannot distinguish longer-order sequences sharing transition distribution
and ignores content, flows, directions, and protocols.

## Errors, Performance, Security, and Testing

Partition gaps/overlaps/out-of-domain cells, uncovered observations, fewer than
one transition, or non-finite calculations fail. Complexity is linear in frames
plus quadratic in state count; configuration must bound states. Tests verify
domain partition, boundaries, canonical ordering, zero pairs, JSD, score bounds,
and insufficient/invalid input. Execution is offline/unprivileged.
