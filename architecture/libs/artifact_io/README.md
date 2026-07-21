# Artifact I/O Library

## Purpose and Responsibilities

`artifact_io` validates package layouts and publishes complete file or package
artifacts atomically. It owns temporary publication mechanics, the generic
successful `artifact-status.toml` envelope, and the distinction between an
attempt directory and its absent-until-success artifact destination. Component
owners still own artifact schemas.

## Inputs, Outputs, and Public Interface

Inputs are an explicit attempt directory, artifact destination, expected
relative files, component validators, and startup-record identity. Output is
either one atomically visible validated artifact plus detached status or a
typed failure with retained diagnostics. Exact Python signatures are unresolved.

## Dependencies, Configuration, and Execution

It depends on [lineage](../lineage/README.md) for hashes and uses filesystem
operations only in its shell. It has no independent configuration or command.

## Documents and Related Components

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md) ·
[System artifact requirements](../../project/SRS.md)
