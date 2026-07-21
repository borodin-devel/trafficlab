# 02 State Construction and Learned Data

The `state.mode` is either `automatic` or `manual` and remains fixed during a
training run. State assignment must be deterministic. Each reference
observation belongs to exactly one state.

### Automatic mode

`automatic` selects one `automatic_submode`:

- `quantile` builds IAT categories and frame-size categories from marginal
  quantiles, then combines them. Tied values are never split. Each requested
  bucket count must produce exactly that many nonempty categories, or
  preparation fails.
- `exact` creates a state for every distinct observed `(IAT, frame length)`
  pair.
- `cluster` runs standard k-means over normalized
  `log1p(IAT seconds)` and frame length vectors. `cluster_count` cannot exceed
  the number of distinct vectors. A zero-variance feature is normalized to
  zero. Zero IAT remains valid because `log1p(0)` is zero.

Cluster implementation should use a maintained, correct Python library where
available and record its version in implementation lineage. Centroids and
members must have a canonical deterministic order before IDs are assigned.

### Manual mode

`manual` supplies explicit IAT and frame-length ranges. It defines categories;
the reference capture still learns emissions and transitions. Ranges use
`minimum <= value < maximum`.

Manual IAT bounds must be finite and have a minimum no smaller than zero.
Manual frame-length ranges must stay within the supported domain, so their
minimum is 60 through 1514 and their exclusive maximum is 61 through 1515.
Every observed IAT and frame length must be covered exactly once. A gap, an
overlap, or an observation outside every range is a preparation failure.

## Learned State Data

Preparation assigns each observation to a state. For each state, it stores
the observed pairs and their counts, its start weight, and counts of outgoing
transitions. Start weights count state membership across all reference
observations; they do not use the one literal first state of the capture.

The learned section makes the model file self-contained. After preparation,
[Traffic Generation](../../apps/50_traffic_generation/README.md) never rereads
the reference capture.

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

State IDs are contiguous integers from `0` through `state_count - 1`. An
emission is an actually observed pair, not a category center. Emission and
transition weights are positive integers. A state with no outgoing transition
restarts from the all-observed-state start distribution; the model must not
invent a transition, smooth unseen transitions, or reject an otherwise valid
model.
