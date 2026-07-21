# Capture Application

## Purpose and Responsibilities

`capture` launches one Linux target process tree in an exclusive prepared
workspace, records its traffic, and publishes canonical validated PCAPNG with
execution and network lineage.

## Inputs, Outputs, and Interface

Inputs are one explicit [capture request](../../contracts/00_capture_request/README.md)
and its matching fresh
[readiness decision](../../contracts/00_10_capture_readiness/README.md).
The request owns the target argument vector, working directory, allowed
environment, completion, interactive, bridge, and packet-retention policy.
Output contains `raw/target.pcapng` and metadata for
[convert](../20_convert/README.md).

```text
capture --capture-request PATH --readiness STATUS_PATH --output-dir DIR
```

## Configuration, Dependencies, and Execution

Capture runs normally without elevation through a prepared ordinary-user
invoker. Workspace setup is manual. Target and runtime policy comes only from
the immutable request; capture configuration cannot override it.

Related boundaries are [PCAP I/O](../../libs/pcap_io/README.md),
[artifact publication](../../libs/artifact_io/README.md), and the manual
[workspace scripts](../../scripts/README.md). Capture never invokes those scripts.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
