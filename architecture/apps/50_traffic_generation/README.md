# Traffic Generation Application

## Purpose and Responsibilities

`traffic_generation` validates one immutable generation-ready model file, asks
its named traffic model for events, and writes one synthetic PCAPNG within
explicit packet, byte, and proposal limits.

## Inputs, Outputs, and Interface

`traffic_generation --model-file PATH --output PATH --max-packets N
--max-output-bytes N --max-proposals N` produces exactly the requested closed
PCAPNG or fails without successful partial output. All three positive limits
are required and have no defaults.

## Configuration, Dependencies, and Execution

The application runs offline and unprivileged. Seed and stopping controls are
owned by model files; the application-owned resource limits bound every
invocation independently of the model stop.

The selected [traffic model](../../traffic_models/README.md) owns event
mathematics. [Genetic training](../30_genetic_training/README.md) and the
[orchestrator](../99_trafficlab/README.md) may call this application; shared
boundaries are [PCAP I/O](../../libs/pcap_io/README.md) and
[artifact publication](../../libs/artifact_io/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
