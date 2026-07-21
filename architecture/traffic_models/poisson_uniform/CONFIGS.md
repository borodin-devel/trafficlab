# Poisson Uniform Configuration

## Normal Builder

The exact version-1 TOML and defaults are in
[model definition](00_MODEL_DEFINITION.md#normal-builder-and-finalization): model name
`poisson_uniform`, schema `1`, arrival rate `10.0`, seed `0`, packet-size bounds
60/1514, count stop 1000, and common `model_state = "builder"`.

## Trainable and Fixed Fields

`arrival_rate_pps`, `packet_size.minimum_bytes`, and
`packet_size.maximum_bytes` are trainable. Seed and stop are fixed generation
controls for one training run. A builder is finalized to a distinct
generation-ready file after candidate validation. Unknown keys/types and
non-finite values fail.
