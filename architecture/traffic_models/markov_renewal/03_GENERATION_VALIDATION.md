# 03 Generation, Model File, and Validation

Generation samples a start state according to `start_weight`. It samples an
observed emission pair from that state according to emission weights, advances
time by the sampled IAT, and writes a frame with the sampled original frame
length. It then samples the next state from the current state's transition
weights.

Generation ends by exactly one configured stop mode:

- `packet_count` emits the configured number of packets.
- `duration` emits every frame whose generated timestamp is within the
  configured duration; it stops before a next sampled frame would exceed it.

Every invocation is also subject to traffic generation's required positive
packet, complete-output-byte, and proposal limits. A proposal includes accepted,
rejected, resampled, and first-beyond-duration events. Reaching a limit before
the selected stop completes fails without successful output. In particular,
`max_proposals` terminates duration generation even when repeated valid zero
IATs would never advance time.

The model does not infer a network interface, addresses, packets bytes, or
protocol headers. The generation application owns writing the valid pcapng
artifact and its file contract.

## Model File

[Model Creation](../../apps/40_model_creation/README.md) creates an immutable
normal builder configuration. [Genetic Training](../../apps/30_genetic_training/README.md)
copies its values, applies and validates all candidate parameters, and only
then prepares a distinct generation-ready file from the reference capture. It
never edits the builder. State counts and clustering controls therefore apply
before state construction. One candidate is one prepared model file with its
selected state construction and effective parameters.

A normal automatic quantile model is:

```toml
model = "markov_renewal"
schema_version = 1
model_state = "builder"
seed = 0

[state]
mode = "automatic"
automatic_submode = "quantile"

[automatic.quantile]
iat_bucket_count = 4
frame_size_bucket_count = 4

[stop]
mode = "packet_count"
packet_count = 1000
```

An automatic cluster model replaces the automatic section with:

```toml
[state]
mode = "automatic"
automatic_submode = "cluster"

[automatic.cluster]
cluster_count = 4
```

A manual model provides explicit ranges instead:

```toml
[state]
mode = "manual"

[[manual.iat_ranges]]
minimum_seconds = 0.0
maximum_seconds = 0.01

[[manual.frame_size_ranges]]
minimum_bytes = 60
maximum_bytes = 1515
```

`duration` is the alternative stop configuration:

```text
[stop]
mode = "duration"
duration_seconds = 60.0
```

## Genetic Training

Only automatic state-complexity controls are trainable:

- `automatic.quantile.iat_bucket_count`
- `automatic.quantile.frame_size_bucket_count`
- `automatic.cluster.cluster_count`

The mode, automatic submode, manual ranges, seed policy, and stop configuration
are fixed for a training run. Crossover is only between candidates with the
same traffic-model name; this model's own parameter rules still apply.

## Validation and Determinism

Preparation validates the complete candidate and source capture before
publishing a model file. It must validate every learned state, emission,
transition, count, ID, and configuration field before publishing atomically.
The generation-ready file records builder digest, candidate identity,
effective parameters, preparation state, seeds and implementation versions,
and the SHA-256 hash of the exact reference artifact as `reference_sha256`.

Given identical source data, configuration, seed, and supported library
versions, preparation and generation must produce the same state assignment,
model serialization, and generated sequence. Implementations must define
canonical ordering for observations, states, emission entries, and transition
entries. Random sampling must use the model seed or the application's explicit
seed policy.

## Testing

Future unit tests cover observation derivation, zero IAT, negative and
non-finite rejection, all three automatic submodes, manual gaps and overlaps,
state assignment, start-weight sampling, dead-end restart, stop modes, all
three generation limits, immutable builder transition, deterministic
serialization, and hash lineage.

Future integration tests cover preparation from a supported pcapng fixture,
atomic publication, and generation from the self-contained model without
reference-capture access. Fixtures need no live capture, network access, or
elevated privilege.

## Reading

Read [Traffic Models](../README.md) for shared Layer 2 rules and this model's
selectable-name registration. The application owners define the surrounding
workflow: [Genetic Training](../../apps/30_genetic_training/README.md),
[Model Creation](../../apps/40_model_creation/README.md), and
[Traffic Generation](../../apps/50_traffic_generation/README.md).
