# Configuration Library

## Purpose and Responsibilities

`configuration` resolves built-in defaults, one explicitly selected TOML file,
and explicit CLI overrides; validates schemas; and creates startup records.

## Inputs, Outputs, and Public Interface

Inputs are application-owned schemas, defaults, invocation arguments, and an
optional selected path. Output is validated effective configuration or a
recorded resolution failure. Exact Python signatures are unresolved.

## Dependencies, Configuration, and Execution

It depends on TOML parsing and [artifact I/O](../artifact_io/README.md). It has
no settings of its own. Detailed behavior is in
[application configuration](../../CONFIGURATION.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
