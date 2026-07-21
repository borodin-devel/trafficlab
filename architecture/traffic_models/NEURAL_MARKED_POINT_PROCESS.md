# Neural Marked-Point-Process Common Rules

## Role

This document owns the source preparation, windowing, split, lineage, and
candidate-fitting rules shared by the [Neural Hawkes](neural_hawkes/README.md)
and [marked point-process diffusion](marked_point_process_diffusion/README.md)
traffic models. It does not define either model's mathematics, neural-network
architecture, loss, sampling, model-file schema, or generation rules; those
belong to the respective model owner.

## Event Domain and Boundaries

Both models simulate Layer 2 Ethernet traffic only. A reference or generated
event is a pair:

```text
(iat_seconds, original_ethernet_frame_length_bytes)
```

`iat_seconds` is finite and non-negative. Frame length is the original,
untagged Ethernet frame length, including the Ethernet header and excluding
the four-byte FCS, as an integer from 60 through 1514 bytes inclusive.

The models do not consume or model packet bytes, addresses, IPv4, IPv6, TCP,
UDP, flows, direction, sessions, application data, or higher protocols. They
do not require live capture, network access, sudo, root, or any other elevated
privilege; fitting, generation, and future tests run offline.

## Reference Sources

One training run selects exactly one source mode:

- one supported reference capture file; or
- one directory containing supported reference capture files.

A directory is enumerated in deterministic relative-path order. Each selected
file is independently validated as a supported Ethernet capture and must have
enough valid frames for the selected model's preparation rules. Its timestamps
must yield finite, non-negative IATs and its original recorded frame lengths
must satisfy the event domain. An unreadable, unsupported, invalid, or
inadequate selected file is a preparation failure; implementations must not
silently skip or repair it.

Within a file, events derive from its ordered frames after the first frame:

```text
IAT_i = timestamp_i - timestamp_(i - 1)
event_i = (IAT_i, original_ethernet_frame_length_i)
```

No IAT, transition, history, sequence, window, or other training example may
cross a file boundary. In particular, the first frame of one file is never
timed against the last frame of another file.

## Windows and Validation Split

Preparation creates deterministic, fixed-size, non-overlapping training
windows from each file independently. Window size and every model-specific
event-count rule are explicit configuration. A trailing partial window is
handled only by the selected model owner's documented policy; it is never
combined with another file or another window to make a full example.

Whole-file validation is preferred whenever it leaves nonempty training and
validation sets. For `C >= 2` files in lexical relative-path order and
validation fraction `f`, validation receives the final
`min(C - 1, ceil(f * C))` files and training receives the preceding files.
Each selected validation file remains one unchanged validation unit.

A one-file source uses a chronological suffix boundary chosen before windows
are created. Let its ordered frames be `F_0` through `F_N` and its `N` derived
events be `E_1` through `E_N`, where `E_i` is derived from `F_(i-1)` and ends
at `F_i`. Let
`V = ceil(f * N)`. Training may use only `E_1` through `E_(N-V-1)`;
`E_(N-V)` is a guard used by neither training nor validation; validation uses
`E_(N-V+1)` through `E_N`. The derived validation PCAPNG contains anchor frame
`F_(N-V)` through `F_N`. Training and validation windows are built separately,
and no observation, transition, history, sequence, or window crosses the
boundary.

The partition is valid only when `N - V >= 2` and both sides provide the
selected model's minimum complete training and validation units. Otherwise
preparation fails before interpreting an index; it does not clamp counts or
move frames. The anchor is excluded from every training observation and
window. As the validation file's first frame, it supplies context for the first
validation IAT and participates in frame-based similarity comparison.

The derived validation PCAPNG preserves original frame bytes, timestamps,
lengths, and interface metadata. Candidate lineage records the source digest,
selected frame range, anchor index, split policy, and validation fraction;
generic detached status records the derived-file and launch digests under its
closed schema. Similarity evaluation receives this artifact, never the complete
mixed source capture.

Each unchanged file or derived suffix is one validation unit. Genetic training
creates an immutable generation input per unit, records the trained-model parent
digest, and sets the model stop to that unit's exact frame count, complete
validation-window count, or first-to-last timestamp horizon. It generates and
compares one PCAPNG only against the matching unit. Candidate fitness is the
equal-weight mean of all units and any unit failure fails the candidate.

One reference capture is supported, but it can overfit. Diagnostics must state
the selected source mode, capture count, total window count, training and
validation assignments, and this single-capture limitation whenever it
applies.

## Configuration, Lineage, and Determinism

All source, split, window, optimizer, epoch, learning-rate, history,
hidden-size, diffusion, generation, seed, and dependency controls are explicit
configuration and are recorded in `launch.toml`. A run records the exact source
paths or deterministic directory membership, every reference-artifact hash,
the source-mode and train/validation assignment, effective configuration,
seed policy and values, library identities and versions, and the hash of the
learned weights. Published artifacts retain this lineage.

Given identical reference artifacts, configuration, seeds, and supported
library versions, source enumeration, validation assignment, derived suffix
publication, window creation, candidate fitting, learned-weight serialization,
and recorded lineage are deterministic. Implementations define canonical
ordering wherever a library or serialization would otherwise leave it
unspecified.

## Candidate Fitting and Genetic Training

Each genetic candidate fits its neural weights locally using that candidate's
model-specific deterministic fitting procedure. Candidate evaluation records
the resulting learned-weight hash and uses its assigned validation data.

Genetic search may vary only explicitly declared high-level hyperparameters.
It must not directly mutate, crossover, or otherwise search individual learned
neural weights. Each model owner names the eligible hyperparameters and owns
its model-specific fitting and validation mathematics.

## Future Verification

Future unit tests cover deterministic directory enumeration, source-mode
validation, exact whole-file counts, suffix anchor/guard indices, insufficient
partition rejection, per-file boundary separation, deterministic
non-overlapping windowing, lineage, event-domain constraints, and seed
reproducibility. Future integration tests use small offline Ethernet pcapng
fixtures and prove the complete mixed source is never a suffix reference; no
fixture or test requires privileges, live capture, or network access.

## Reading

Read the [traffic-model registry](README.md), then the selected model owner:
[Neural Hawkes](neural_hawkes/README.md) or
[marked point-process diffusion](marked_point_process_diffusion/README.md).
The application owners define the surrounding workflow:
[Genetic Training](../apps/30_genetic_training/README.md),
[Model Creation](../apps/40_model_creation/README.md), and
[Traffic Generation](../apps/50_traffic_generation/README.md).
