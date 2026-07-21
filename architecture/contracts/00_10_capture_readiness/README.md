# Capture Readiness Contract

## Purpose and Status

This contract carries one read-only readiness decision from preflight to
capture. It binds one exact [capture request](../00_capture_request/README.md)
and prepared workspace and distinguishes supported findings from blockers. Its
documentation identifier is `00_10_capture_readiness`.

## Producer, Consumer, Inputs, and Outputs

[Preflight](../../apps/00_preflight/README.md) produces the decision from one
capture request. [Capture](../../apps/10_capture/README.md) consumes a matching
fresh successful decision before reservation/launch. The published package
contains exactly `manifest.json`, `capture-readiness.toml`, and `launch.toml`;
its detached status follows [artifact I/O](../../libs/artifact_io/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
