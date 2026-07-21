# 00 Neighbour-Transition Method Definition

## Role

`neighbor_transition` is a selectable similarity method that compares one-step timing--size state ordering in one reference and one generated supported Ethernet PCAPNG file.

## Shared Behavior

The [temporal Layer 2 rules](../TEMPORAL_L2.md) own supported-PCAPNG extraction, use of timestamps and original Ethernet frame lengths only, time-zero alignment, IAT derivation, input validation, deterministic processing, configuration recording, result publication, and offline testing boundaries. This method adds only state categorisation and transition-distance mathematics.

## States and Configuration

For every noninitial frame `i` in recorded order, the method forms:

```text
z_i = (log1p(IAT_i in seconds), original_frame_length_i)
```

The valid observation domain is transformed IAT `x >= 0` and integer frame length `l` in `[60, 1514]`. A configured category is one joint state:

```text
state q = ([a_q, b_q), [c_q, d_q])
```

`0 <= a_q < b_q <= infinity`; `b_q = infinity` denotes `[a_q, infinity)`. `60 <= c_q <= d_q <= 1514`, and length bounds are inclusive. An observation is in a state precisely when both components are in its stated intervals.

The configured nonempty category list must partition the valid domain: categories must be nonempty, pairwise nonoverlapping, and cover every valid `(x, l)` exactly once. Configuration validation rejects a gap, overlap, empty category, or category outside the valid domain. Validating only the observations present in the captures is insufficient. The canonical state order is ascending `(a_q, b_q, c_q, d_q)`, where `infinity` sorts after finite endpoints; ordered position is the state identifier.

## Transitions and Distance

Each capture's ordered observations map to `s_1, ..., s_n`. For each `i = 1, ..., n - 1`, it increments ordered transition `(s_i, s_(i + 1))`. Transition keys are the canonical pair of state identifiers, which fixes transition serialization and probability-vector order.

For capture `X`, its probability for transition `t` is:

```text
p_X(t) = count_X(t) / (n - 1)
```

The vector contains every ordered pair of configured states, including zero-count pairs. With reference vector `p`, generated vector `q`, and `m(t) = (p(t) + q(t)) / 2`, the base-2 Jensen-Shannon distance is:

```text
JSD(p, q) = sqrt(0.5 * sum_t(p(t) * log2(p(t) / m(t)))
                 + 0.5 * sum_t(q(t) * log2(q(t) / m(t))))
```

Zero-probability terms contribute zero. Base-2 Jensen-Shannon divergence is in `[0, 1]`; its square-root Jensen-Shannon distance is therefore in `[0, 1]`. The primary similarity uses this natural bound:

```text
similarity = 1 - JSD(p_reference, p_generated)
```

Thus similarity is in `[0, 1]`; `1` means equal adjacent-transition distributions under the configured partition.

## Result and Failure

Method-defined details in `similarity.toml` record raw Jensen-Shannon distance, ordered categories and identifiers, ordered transition counts and probabilities for both captures, observation and transition counts, and all reproducing configuration. The [similarity result contract](../../contracts/60_30_similarity_result/README.md) owns result validation, hashing, and atomic publication.

Configuration validation completes before scoring. Each capture needs at least three frames, hence two observations and one adjacent transition. An empty category, category gap or overlap, uncovered observation, invalid state assignment, non-finite transformed value or calculation, or inadequate input makes evaluation unsuccessful. The method never drops an observation or transition, repairs boundaries, normalizes invalid configuration, or publishes a partial or fallback similarity.

## Limits

The method measures one-step local ordering of configured timing--size states. It cannot distinguish sequences with the same adjacent-transition distribution but different longer-range order, and it does not compare contents, addresses, flows, direction, or higher-protocol behavior.

## Testing

Future deterministic unit tests cover boundaries, complete nonoverlapping partitions, gap and overlap rejection, canonical state and transition ordering, zero-count transitions, base-2 Jensen-Shannon bounds, zero IAT, inadequate inputs, and raw diagnostics. Future integration tests use small offline Ethernet PCAPNG fixtures and verify `similarity.toml` details. No test requires network access, live capture, sudo, root, or other elevated privilege.

## Reading

Follow the [architecture governance](../../README.md), the [similarity-method registry](../README.md), the shared [temporal Layer 2 rules](../TEMPORAL_L2.md), the [60 similarity evaluation application](../../apps/60_similarity_evaluation/README.md), and the [similarity result contract](../../contracts/60_30_similarity_result/README.md) before changing this method.
