# Poisson Empirical Configuration

## Normal Builder

The exact version-1 builder is in
[model definition](00_MODEL_DEFINITION.md#normal-builder-and-prepared-model). Defaults are
arrival rate `10.0`, seed `0`, one `(60, weight 1)` placeholder entry, and
packet-count stop 1000, with common `model_state = "builder"`. Preparation
leaves the builder unchanged and publishes a distinct file with
`model_state = "generation_ready"` and a reference-derived table.

## Validation and Training

Only arrival rate is trainable. Sizes must be unique ascending integers 60–1514
and weights positive integers. Seed and stop are fixed controls. Unknown fields,
invalid types, empty table, duplicate sizes, and invalid stop fail.
