# PCAPNG I/O Library

## Purpose and Responsibilities

`pcap_io` reads, validates, filters, and writes supported PCAPNG while
preserving packet order, timestamps, original lengths, and interface metadata.

## Inputs, Outputs, and Public Interface

Inputs are explicit PCAPNG paths or byte streams and operation-specific
policies. Outputs are canonical observations, preserved packet records, or
validated PCAPNG. Exact Python signatures and backend choice are unresolved.

## Dependencies, Configuration, and Execution

It depends on maintained PCAPNG/packet libraries selected by infrastructure.
It performs offline file I/O, needs no privilege, and owns no standalone
configuration or command.

## Documents and Related Components

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md) ·
[Capture](../../apps/10_capture/README.md) · [Convert](../../apps/20_convert/README.md)
