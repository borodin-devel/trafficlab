# Marked Point-Process Diffusion Configuration

## Version 1 Builder

Complete TOML, defaults, and validation are authoritative in
[model file and generation](04_GENERATION_VALIDATION.md). Groups are
representation, attention architecture, diffusion, fitting/optimizer/loss,
seed, and exactly one window-count or duration stop.

Version 1 accepts linear beta schedule with every beta in `(0,1)`, epsilon
predictor, DDPM sampler, all steps, stochasticity exactly `1.0`, deterministic
runtime, positive finite dimensions/scales, compatible heads, bounded events,
finite nonnegative regularization/dropout, and positive loss weight total.

## Trained Extension and Genetic Fields

Generation-ready trained files add builder digest, candidate identity and
effective configuration, canonical weights/reference, hash, count-head
parameters, checkpoint metrics, and full lineage. Candidate values apply
before preparation/fitting and never mutate the builder. Eligible genetic fields are listed in
[fitting and hyperparameters](03_FITTING_SELECTION.md#candidate-hyperparameters); learned
weights and undeclared external conditions are forbidden.
