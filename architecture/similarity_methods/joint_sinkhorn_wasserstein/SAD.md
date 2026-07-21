# Joint Sinkhorn/Wasserstein Software Architecture Document

## Context, Goals, and Boundaries

This method measures joint association between transformed IAT and original
frame length using debiased entropic transport. It discards packet order after
forming observations and does not measure count, flow, content, or protocol.

## Structure and Data Flow

Canonical temporal extraction builds uniform empirical clouds. Positive feature
scales define Euclidean ground cost. A deterministic maintained optimal-
transport solver computes reference/generated and two self terms. Debiased
divergence maps monotonically through `exp(-D/scale)`.

## Configuration, Errors, Resources, and Security

All scales, epsilon, tolerances, iteration limit, solver/backend/algorithm,
thread/device/seed/determinism controls are explicit. Non-convergence or invalid
numerics fails; no partial approximation/clamp is published. Cost is generally
quadratic in observation counts and resource admission is required. PCAPNG is
untrusted and processed offline without privilege.

## Testing, Decisions, Risks, and Limits

Tests compare independent transport examples, unequal clouds, convergence, and
determinism. Library/backend selection remains an implementation-stage decision
constrained by documented behavior. Risks are memory/time growth, regularization
bias, tolerance sensitivity, and small negative numerical divergence.
