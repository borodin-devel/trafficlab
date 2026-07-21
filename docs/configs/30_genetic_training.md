# 30 Genetic Training Configuration Guide

The authoritative setting names, TOML types, defaults, override rules, source
selectors, validation constraints, generation limits, and search-space grammar
are defined by the in-corpus [genetic training configuration
schema](../../architecture/apps/30_genetic_training/CONFIGURATION_SCHEMA.md).
This page is only a user workflow guide; if text here ever differs, the
architecture schema wins.

Copy the versioned [genetic-strategy
template](../../configs/30_genetic_training/genetic_strategy.toml) to
`.configs/genetic_strategy.toml`, fill in its run-specific reference,
components, generation resource envelope, and search ranges, then select it
explicitly:

```text
genetic_training --config-file .configs/genetic_strategy.toml \
  --output-dir /absolute/path/to/result
```

The application never searches for this file and does not accept
`--config-dir`. Repeatable `--set DOTTED.PATH=TOML_VALUE` overrides are parsed
as TOML values. A direct file, a neural-only directory, or an explicit
directional-package status/member pair may be selected as described by the
authoritative schema.

Every run must resolve positive
`resources.generation.max_packets`,
`resources.generation.max_output_bytes`, and
`resources.generation.max_proposals`; there are no implicit values. The
resolved configuration and source identity are recorded in `launch.toml`.
