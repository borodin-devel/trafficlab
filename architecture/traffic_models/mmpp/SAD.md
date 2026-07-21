# MMPP Software Architecture Document

## Context, Goals, and Boundaries

Goal is future Markov-modulated Poisson timing. Current architecture does not
choose continuous/discrete state transition convention, observation likelihood,
stationary initialization, fitting method, label canonicalization, frame-length
law, generation algorithm, or schema.

## Architecture Status and Risks

The model remains unsupported. Research must address identifiability, matrix
constraints, zero/near-equal rates, numerical stability, state explosion,
deterministic fitting and sampling, complexity, and independent reference tests.
