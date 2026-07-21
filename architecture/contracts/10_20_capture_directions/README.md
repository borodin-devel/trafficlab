# Capture Directions Contract

## Purpose and Responsibilities

This contract defines the canonical PCAPNG package exchanged by capture and
conversion and later consumed as directional reference traffic. Its
documentation identifier is `10_20_capture_directions`.

## Producers, Consumers, Inputs, and Outputs

[Capture](../../apps/10_capture/README.md) supplies canonical source and
metadata. [Convert](../../apps/20_convert/README.md) publishes a manifest,
complete target, uplink, downlink, direction report, and frozen startup record;
detached status binds the manifest.

## Dependencies and Interface

The package depends on PCAPNG, JSON, TOML, SHA-256, explicit app-side direction
evidence, and the shared [artifact protocol](../../libs/artifact_io/README.md).
It has no independent runtime configuration.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
