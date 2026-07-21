# Marked Point-Process Diffusion Software Architecture Document

## Context, Goals, and Boundaries

This model jointly generates bounded variable-count timing/length windows,
capturing within-window correlations. It conditions only on recorded seed and
bounded prior generated history; it receives no target window, reference event,
packet content, protocol, address, flow, direction, or live state.

## Internal Structure and Data Flow

Preparation creates nonoverlapping per-file fixed-duration windows represented
by prefix count mask and normalized event matrix. A categorical head samples
event count. A conditional epsilon-predicting DDPM denoises active continuous
rows. Complete decoded windows pass timing-support and length conversion before
all events emit atomically.

## Configuration, Extension, Errors, and Resources

Version 1 fixes linear noise schedule, epsilon predictor, all-step stochastic
DDPM, deterministic CPU runtime, and explicit bounded architecture/fitting/stop
controls. Genetic search varies only declared high-level fields, never weights.
Window/event/history/diffusion/epoch limits bound memory and time; resource
admission is mandatory. Invalid numerical/mask/support/schema states fail.

## Security, Logging, Testing, Risks, and Limits

Fitting/generation are offline and unprivileged. Reference and weight files are
untrusted and hash-validated. Logs record losses, counts, schedules, and lineage.
Risks are diffusion expense, invalid-window rejection rate, count imbalance,
numerical schedule error, dependency nondeterminism, and single-capture overfit.
