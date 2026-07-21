# Lineage Library

## Purpose and Responsibilities

`lineage` computes content hashes and builds deterministic provenance records
linking outputs to exact inputs, configuration, seeds, and implementations.

## Inputs, Outputs, and Public Interface

Inputs are explicit artifact paths and typed provenance values. Outputs are
canonical hash and lineage values for component-owned manifests and detached
artifact status. Exact Python signatures are unresolved; contract owners select
JSON or TOML while applying the canonical ordering they define.

## Dependencies, Configuration, and Execution

It uses SHA-256 and bounded file reads. It owns no command or independent
configuration and depends on no model or method.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
