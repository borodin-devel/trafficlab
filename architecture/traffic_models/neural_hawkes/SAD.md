# Neural Hawkes Software Architecture Document

## Context, Goals, and Boundaries

This model learns causal finite-history dependence between Layer 2 IAT and
frame length. It emits one event at a time and cannot inspect target/future
events, packet bytes, protocols, addresses, flows, direction, or live state.

## Internal Structure and Data Flow

Event embeddings combine `log1p(IAT)`, normalized length, and position. Strict
causal self-attention encodes prior events. A zero-inflated log-normal-mixture
time law and conditional truncated-normal-mixture mark law define the next
event. Candidate fitting uses deterministic windows, teacher forcing, validation
checkpoint selection, and canonical weight serialization.

## Configuration, Extension, Errors, and Resources

Versioned builder/trained schemas own architecture, optimizer, fitting,
validation, runtime, seed, and stop fields. Genetic search varies only declared
high-level hyperparameters, never learned weights. Finite history/window/epochs
bound work; CPU deterministic runtime uses one thread in schema version 1.
Non-finite likelihoods/gradients/weights/samples or missing data fail candidates.

## Security, Logging, Testing, Risks, and Limits

Fitting/generation are offline unprivileged. Reference files and weight paths
are untrusted; external weights must be internal relative hash-verified files.
Logs contain metrics and lineage, not events. Risks are overfit, numerical
underflow/overflow, dependency nondeterminism, finite-context blind spots, and
high fitting cost. Direct likelihood and normalization tests are mandatory.
