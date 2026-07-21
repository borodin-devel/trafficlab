# Rate Cross-Correlation Software Architecture Document

## Context and Status

Goal is future shift-tolerant rate-shape comparison. Architecture does not yet
define packet/byte feature, bins, normalization, correlation estimator, lag
selection/aggregation, constant-series behavior, score range, or complexity.
Current registries must reject it. Research must address edge effects, lag bias,
multiple maxima, unequal duration, zero series, and deterministic FFT/direct computation.
