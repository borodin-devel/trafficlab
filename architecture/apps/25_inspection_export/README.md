# Inspection Export Application

## Purpose and Responsibilities

`inspection_export` converts one validated PCAPNG source into bounded Apache
Parquet and JSON Lines views for ML development and LLM inspection.

## Inputs, Outputs, and Interface

Input is either an explicit PCAPNG plus source lineage or an explicit
directional-package status plus one `target`, `uplink`, or `downlink` member;
no member defaults. Output is a package containing manifest, Parquet, JSONL,
schema, and startup record under the
[inspection dataset contract](../../contracts/25_inspection_dataset/README.md).
It is auxiliary and never
replaces canonical PCAPNG.

## Configuration, Dependencies, and Execution

The application runs offline and unprivileged using PCAPNG, artifact, lineage,
and PyArrow capabilities. Concrete optional settings are unresolved.

Sources may come from [capture](../10_capture/README.md) or
[convert](../20_convert/README.md). Shared boundaries are
[PCAP I/O](../../libs/pcap_io/README.md),
[artifact publication](../../libs/artifact_io/README.md), and
[lineage](../../libs/lineage/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
