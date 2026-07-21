# Markov Renewal Traffic Model Design

**Date:** 2026-07-20

**Status:** Approved design

## Goal

Add one selectable Layer 2 traffic model, `markov_renewal`, that learns
first-order transitions between joint inter-arrival-time (IAT) and Ethernet
frame-length states from one reference PCAPNG. It emits real observed
IAT/frame-length pairs from the chosen state.

## Scope

This design adds:

- `architecture/traffic_models/markov_renewal/README.md` as the model owner;
- `architecture/amendments/001_markov_renewal_registry.md` as the registry
  addition required by original-document immutability.

It does not modify original architecture documents, implement Python code or
PCAPNG parsing, add a dependency, add a configuration template, simulate
network layers above Ethernet, capture traffic, or require elevation.

## Registry Amendment

`architecture/traffic_models/README.md` lists selectable models, while project
guidance forbids editing original architecture documents. The amendment records
this conflict and declares `markov_renewal` selectable without changing the
original table.

The amendment names the conflicting registry and immutability sources, records
the decision and rationale, limits its scope to this model, tells readers to
treat it as a registry addition, rejects editing the original registry, records
no migration or compatibility break, and states its status, owner, and date.

## Ownership and Boundaries

The `markov_renewal` owner defines state construction, reference extraction,
transitions, emissions, model-file schema, normal values, trainable fields,
validation, deterministic ordering, stopping, and future tests.

[40 model creation](../../../architecture/apps/40_model_creation/README.md)
creates a normal file. [30 genetic
training](../../../architecture/apps/30_genetic_training/README.md) prepares a
candidate using one reference PCAPNG after 40 creates that file. [50 traffic
generation](../../../architecture/apps/50_traffic_generation/README.md) reads
the completed self-describing model file and creates one PCAPNG.

The model produces only timestamps and original Ethernet frame lengths. It does
not model addresses, payloads, VLAN, raw IEEE 802.11/Radiotap, IP, TCP, UDP,
application protocols, flows, directions, or sessions.

## Reference Observations

For reference frames in recorded order `0..N-1`, the model uses frames
`1..N-1`:

```text
IAT_i = timestamp_i - timestamp_(i - 1)
observation_i = (IAT_i, original_frame_length_i)
```

At least two frames are required. IAT is measured in seconds. Zero IAT is
valid; a negative, non-finite, missing, or value not representable as a finite
TOML number fails preparation. IAT values use deterministic serialization; the
recorded implementation version defines their handling.

Each observation uses original recorded Ethernet length, never retained
captured bytes. Valid length is an integer from 60 through 1514 bytes. The
reference must be Ethernet-form input with valid original-length metadata.
Unsupported links, raw 802.11/Radiotap, malformed PCAPNG, missing metadata, or
invalid lengths fail without skipping, clamping, or rewriting observations.

## State Modes

`state.mode` is the non-trainable value `automatic` or `manual` for the whole
training run. `automatic_submode` is also non-trainable and is `quantile`,
`exact`, or `cluster`. Candidates may cross over only when these non-trainable
settings are identical.

### Automatic Quantile

`quantile` builds ordered IAT and frame-length categories from the reference.
The positive integer trainable fields are `iat_bucket_count` and
`frame_size_bucket_count`. Each requested count must be no greater than the
number of distinct observed values of its feature and must create that many
nonempty categories. Tied values are never split. An unachievable count fails
preparation rather than silently changing configured complexity.

One state exists for each joint IAT-category/frame-size-category pair actually
observed. Empty Cartesian-product pairs are not states.

### Automatic Exact

`exact` creates one state for every exact observed `(IAT_seconds,
frame_size_bytes)` pair. It has no state-builder size field. Exact identity uses
the canonical serialized IAT value and exact integer frame length.

### Automatic Cluster

`cluster` uses ordinary k-means over:

```text
(normalized log1p(IAT_seconds), normalized frame_size_bytes)
```

The positive integer trainable `cluster_count` must not exceed the number of
distinct reference feature vectors. A zero-variance feature contributes zero
after normalization. Initialization uses the model seed and recorded
implementation/library version. Cluster IDs are canonicalized by ascending
centroid values, with the smallest member observation as a tie-break.

Implementation should prefer a maintained, tested k-means library and record
its identity and version. A handwritten implementation requires direct tests
against that library or authoritative examples.

### Manual Ranges

`manual` contains named explicit IAT ranges and named explicit frame-size
ranges. Each has `minimum` and `maximum` and means:

```text
minimum <= value < maximum
```

IAT limits are finite nonnegative seconds. A frame-size range minimum is an
integer from 60 through 1514; its exclusive maximum is an integer from 61
through 1515, allowing a range ending at 1515 to include a 1514-byte frame.
Ranges have positive width and unique IDs. Every reference IAT and length must
be in exactly one matching range; a gap, overlap, or out-of-range observation
fails preparation. A manual state is an observed IAT-range-ID/frame-size-range-
ID pair. Manual ranges are non-trainable.

## Learned State Data

Preparation records these values in the model file:

- one canonical ID for every observed state;
- all observed IAT/frame-size pairs assigned to each state, with frequency;
- one start-state weight per state, equal to its frequency across all reference
  observations; and
- one transition weight for every observed consecutive state pair.

The completed file includes this data, the reference content hash,
state-builder settings, schema version, seed, stop settings, and relevant
implementation/library identities. [50 traffic
generation](../../../architecture/apps/50_traffic_generation/README.md) must
generate from this file alone and never reread the reference capture.

Quantile categories are ordered by feature range; exact states by
`(IAT_seconds, frame_size_bytes)`; cluster states by canonical centroid; and
manual states by their range-ID pair. Emissions and transition rows follow
canonical state-ID order.

## Generation

Generation selects a state from start-state weights, samples one stored
IAT/frame-size pair from that state by its frequency, emits a frame of that
length, and advances the output timestamp by that IAT.

For every later frame, it selects the next state from the current state's
observed transition weights, samples a stored pair from that state, emits it,
and advances by its IAT. Only the current state affects the next-state choice.

A state with no recorded outgoing transition selects a new state from
start-state weights. It does not invent unseen transitions or fail a valid
model. Zero IAT may produce equal successive timestamps; timestamps are
nondecreasing.

The model supports exactly one stop mode:

- `packet_count` requires a positive integer and emits exactly that many
  frames; or
- `duration` requires finite positive `duration_seconds` and emits a frame only
  while its cumulative timestamp is at most that duration.

The first frame is scheduled after its sampled IAT from time zero. A duration
shorter than that IAT emits no frame. Both stop values, neither value, or a
value incompatible with the selected mode is invalid.

## Model File Shape

40 creates a normal file containing model identity, seed, one state-builder
configuration, and stop controls. A normal automatic quantile file is:

```toml
model = "markov_renewal"
schema_version = 1
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

Automatic exact uses no `automatic.quantile` or `automatic.cluster` table.
Automatic cluster has only:

```toml
[automatic.cluster]
cluster_count = 4
```

Manual mode has no automatic table and uses explicit ranges, for example:

```toml
[state]
mode = "manual"

[[manual.iat_ranges]]
id = "short"
minimum = 0.0
maximum = 0.01

[[manual.frame_size_ranges]]
id = "small"
minimum = 60
maximum = 512
```

30 adds learned data only after successful reference preparation. Its exact
shape is:

```toml
[learned]
reference_sha256 = "..."
state_count = 2

[[learned.states]]
id = 0
start_weight = 12

[[learned.states.emissions]]
iat_seconds = 0.002
frame_size_bytes = 128
weight = 3

[[learned.states.transitions]]
to_state = 1
weight = 7
```

State IDs are the contiguous nonnegative integers from `0` through
`state_count - 1` in canonical state order. Every `learned.states` row has one
canonical unique ID, one positive integer `start_weight`, at least one emission
row, and zero or more transition rows.
Emission rows have finite nonnegative `iat_seconds`, an integer
`frame_size_bytes` from 60 through 1514, and a positive integer `weight`.
Transition rows identify another state ID and have a positive integer `weight`.
Rows are canonical, complete, and covered by the model-file content hash.
Unknown, partial, or hash-mismatched learned data is invalid.

## Trainable Fields

Only these fields are trainable:

- `automatic.quantile.iat_bucket_count`;
- `automatic.quantile.frame_size_bucket_count`; and
- `automatic.cluster.cluster_count`.

The active automatic submode determines which fields may appear. Model identity,
schema version, state mode, automatic submode, manual ranges, seed, stop
settings, reference hash, learned states, emissions, start weights, transition
weights, and implementation/library identity are non-trainable.

## Validation, Determinism, and Lineage

Validation rejects unknown keys, unsupported schema versions, invalid types,
invalid state-mode combinations, irrelevant state-builder tables, invalid seed
or stop controls, nonpositive or unachievable automatic counts, invalid manual
ranges, unassigned or ambiguous observations, empty states or emissions,
invalid IDs or weights, noncanonical ordering, invalid transition endpoints,
missing learned data after preparation, malformed reference lineage, and hash
mismatches. Validation completes before 50 publishes output; invalid input is
never partially applied.

For one recorded generator implementation and library set, the same validated
model file produces the same fitting, state ordering, emissions, transitions,
timestamps, and frame lengths for the same seed. Candidate lineage records the
reference and model hashes, mode/settings, learned-state count, generator
identity, and k-means library identity in cluster mode. Completed model files
are validated, canonically serialized, hashed, and atomically published.

No model behavior depends on mapping iteration, PCAPNG parser completion, or
child-process completion order. No operation requires network access, live
capture, or elevated privilege.

## Testing Expectations

Future deterministic, unprivileged tests use local PCAPNG fixtures and cover:

- original length rather than captured bytes, two-frame minimum, zero/negative
  IAT, and unsupported links;
- quantile categories and tied values, exact states, deterministic k-means,
  zero-variance features, and invalid automatic counts;
- manual half-open ranges, complete coverage, gaps, overlaps, and out-of-range
  observations;
- canonical state/emission/start/transition extraction;
- seeded starts, transitions, emissions, dead-end restart, zero-IAT timestamps,
  and both stop modes;
- normal files, learned-data replacement, schema failures, hashes, and atomic
  publication; and
- 30 orchestration with fake 40 and 50 applications and retained failures.

No test uses a real network, live capture, elevation, or privileged script.

## Alternatives Considered

Separate selectable models for quantile, exact, cluster, and manual state
builders would duplicate state, transition, emission, lineage, and validation
rules. One model with explicit modes keeps those rules consistent.

Fixed bucket centers or cluster centroids would simplify emission, but discard
observed joint variation. Sampling stored pairs preserves that variation.

Smoothing unseen transitions would invent behavior absent from the reference.
Restarting from observed state frequencies preserves empirical transitions and
continues after a terminal state.
