# Neural Hawkes Configuration

## Version 1 Schema

The complete normal builder table, defaults, types, cross-field rules, and TOML
example are authoritative in
[serialization and generation](03_SERIALIZATION_GENERATION.md#normal-builder-schema). Required
groups are model/schema/seed, architecture, IAT law, mark law, AdamW optimizer,
fitting, validation, deterministic CPU runtime, and exactly one stop mode.

## Trained Extension

A generation-ready trained file retains the effective candidate fields and
adds builder digest, candidate identity, canonical weights or one internal
immutable relative weight reference, SHA-256, selected epoch/metrics, and
common source/split/seed/library lineage. Candidate values apply before
preparation/fitting and never mutate the builder. Generation rejects an
untrained builder, unknown field, dimension mismatch, traversal, or hash mismatch.

## Genetic Fields

Eligible fields are documented in
[fitting and selection](02_FITTING_SELECTION.md#candidate-hyperparameters). Event
domain, source/file boundaries, run-wide split assignment, law families, seed
policy, rounding, stop, and schema version remain fixed for one run; learned
weights are never genetic. Builder validation fields mirror the resolved
[genetic-training split controls](../../apps/30_genetic_training/CONFIGURATION_SCHEMA.md#neural-validation-fields)
but are not eligible search paths.
