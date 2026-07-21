# Convert Application

## Purpose and Responsibilities

`convert` classifies supported Ethernet IP packets at an explicit app-side
observation boundary and publishes complete, uplink, and downlink PCAPNG.

## Inputs, Outputs, and Interface

Input is canonical or external PCAPNG plus explicit source lineage and
reference profile. Output follows the
[capture-directions contract](../../contracts/10_20_capture_directions/README.md).

## Configuration, Dependencies, and Execution

The application runs offline and unprivileged using PCAPNG and artifact
libraries. Reference-profile serialization remains unresolved.

It consumes output from [capture](../10_capture/README.md), may feed
[inspection export](../25_inspection_export/README.md), and depends on
[PCAP I/O](../../libs/pcap_io/README.md) and
[artifact publication](../../libs/artifact_io/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
