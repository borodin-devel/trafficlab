# Similarity Evaluation Configuration

## Interface

Known CLI arguments are `--reference`, `--generated`, `--method`, and
`--output-dir`. Shared application configuration and startup recording apply.

## Method Settings

Each method's `CONFIGS.md` owns its exact fields, defaults, and validation.
How the application receives a selected method configuration is unresolved;
implementation must define an explicit TOML namespace or file and may not rely
on environment discovery.
