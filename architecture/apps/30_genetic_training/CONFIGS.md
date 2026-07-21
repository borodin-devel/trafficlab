# Genetic Training Configuration

## Authoritative Schema

Exact setting names, types, defaults, override semantics, and cross-field
validation are owned by the architecture's
[authoritative configuration schema](CONFIGURATION_SCHEMA.md). The external
[configuration guide](../../../docs/configs/30_genetic_training.md) and TOML
template are derived material and must defer to that owner.

## Selection and Precedence

`--config-file` and `--output-dir` are required; `--config-dir` is unsupported.
Effective values resolve built-in defaults, required TOML, then repeatable
`--set DOTTED.PATH=TOML_VALUE` overrides. Unknown/repeated paths, table
replacement, coercion, and invalid final configuration fail.

## Setting Groups

- schema and exactly one valid reference source mode;
- ordered unique allowed traffic-model names;
- one similarity method and one genetic strategy;
- neural validation policy for neural-only runs;
- strategy population, operator, retry, stopping, and seed controls;
- model-specific trainable search-space entries.

## Resource Settings

Every generation child receives the three required positive limits under
`resources.generation`; they have no defaults. Candidate CPU, memory, storage,
and maximum-worker setting paths are not defined in schema version 1, so
candidate evaluation is serial. Parallel execution requires adding and
validating those paths in the authoritative schema first.
