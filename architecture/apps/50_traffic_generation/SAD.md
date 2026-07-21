# Traffic Generation Software Architecture Document

## Role

This application reads one immutable, self-describing, generation-ready
traffic model file and creates one synthetic PCAPNG file from that model.

## Interface

The conceptual command is:

```text
traffic_generation --model-file PATH --output PATH \
  --max-packets N --max-output-bytes N --max-proposals N
```

All paths and all three positive integer limits are required and have no
defaults. The model file identifies its traffic model, generation-ready state,
effective parameters, and lineage. A builder or transient candidate
configuration is rejected. The application creates exactly one PCAPNG file at
the absent `--output` destination; its filename is not fixed. The selected
[traffic model](../../traffic_models/README.md) owns synthetic-traffic behavior,
model-file validation, and its stop condition.

## Generation Resource Envelope

`max_packets` bounds accepted packets. `max_output_bytes` bounds the complete
PCAPNG size, including headers and metadata. `max_proposals` counts every event
proposal, including accepted, rejected, resampled, and the first proposal past
a duration boundary. A packet-count stop above `max_packets`, or a window-count
stop whose validated `window_count * max_events_per_window` upper bound exceeds
`max_packets`, fails validation before sampling.

Before accepting a packet, incrementing a proposal, or writing bytes that would
exceed its limit, generation fails without successful publication. Reaching a
limit before the model-owned stop condition completes is an error with the
limit and observed bounded counts in diagnostics; output is never silently
truncated or repaired. The proposal limit guarantees finite duration-mode work
even when a valid model repeatedly samples zero IAT.

## Boundaries

This application does not choose a model, alter parameters, compare traffic,
assign fitness, or select candidates. Those responsibilities belong to
[30 genetic training](../30_genetic_training/README.md),
[40 model creation](../40_model_creation/README.md), and
[60 similarity evaluation](../60_similarity_evaluation/README.md).

## Diagnostics and Testing

Each attempt follows the shared
[startup record](../../CONFIGURATION.md#startup-record) and single-file
[artifact publication](../../libs/artifact_io/SAD.md#structure-and-data-flow)
rules. The PCAPNG contains input/producer lineage but no self-digest;
`artifact-status.toml` carries its final SHA-256 and launch-record digest. No
implementation exists yet. Future tests are unprivileged and verify
deterministic PCAPNG validation, all resource boundaries, detached status, and
publication with temporary directories.

## Reading

Follow the [architecture governance](../../README.md) and read the selected
traffic-model owner before changing this application.

## Cross-Cutting Architecture

Model validation and seeded event generation are model-owned deterministic
cores. CLI, PCAPNG rendering, streaming output, hashes, and publication form the
application shell. Model stops and the three required resource limits jointly
bound output, file growth, zero-IAT duration loops, and rejection/resampling
loops. Invalid events fail without repair. Execution is offline and
unprivileged; logs summarize batches, never packets.
