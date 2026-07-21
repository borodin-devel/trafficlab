# Sequence-Kernel MMD Software Architecture Document

## Context, Goals, and Boundaries

This method preserves ordered timing/size paths inside fixed windows and
compares their empirical collection. It does not preserve ordering between
windows or inspect frames after horizon, bytes, addresses, flows, direction,
or protocols.

## Internal Structure and Data Flow

Canonical extraction forms anchored piecewise-linear paths in a Gaussian-RBF
RKHS from local time, scaled `log1p(IAT)`, and scaled original length. A
normalized truncated signature kernel compares paths. Biased empirical
MMD-squared compares equal window collections and maps `sqrt(MMD2)` through an
exponential score.

## Configuration, Errors, Resources, and Security

Horizon, width, feature scales, RBF bandwidth, positive degree/weights, score
scale, and numerical tolerance are explicit. Invalid kernels/denominators/
numerics fail except tiny negative MMD-squared within tolerance maps to exact
zero. Kernel matrix computation is quadratic in windows and signature cost
grows with path length/degree; explicit resource admission is mandatory.

## Testing, Decisions, Risks, and Limits

A maintained signature-kernel library is preferred; fallback needs independent
tests. Risks include high complexity, degree truncation, bandwidth/scale
sensitivity, and library nondeterminism. Tests cover path order, empty windows,
kernel symmetry/bounds, negative normalized kernels, MMD, and tolerance.
