# 00 Autocorrelation Method Definition

## Role

`autocorrelation` is a selectable similarity method that compares configured linear repetition in the timing and original-frame-length sequences of one reference and one generated supported Ethernet PCAPNG file.

## Shared Behavior

The [temporal Layer 2 rules](../TEMPORAL_L2.md) own supported-PCAPNG extraction, use of timestamps and original Ethernet frame lengths only, time-zero alignment, IAT derivation, input validation, deterministic processing, configuration recording, result publication, and offline testing boundaries. This method adds only autocorrelation mathematics and method-specific validation.

## Sequences and Configuration

For every noninitial frame `i` in recorded order, the method creates aligned sequences:

```text
x_i = log1p(IAT_i in seconds)
l_i = original_frame_length_i
```

The explicit finite configuration is an ordered nonempty set of distinct positive integer lags `K`, positive timing and frame-length feature weights, and one positive lag weight for every `k` in `K`. Weights are validated from exact decimal configuration values and never silently normalized:

```text
timing_feature_weight + frame_length_feature_weight = 1
lag_weight_k > 0 for every k in K
sum_(k in K)(lag_weight_k) = 1
```

## Autocorrelation, Distance, and Score

For either sequence `y = y_1, ..., y_n`, let `mean(y)` be its arithmetic mean. For configured lag `k`, the sample autocorrelation is:

```text
r_y(k) = sum_(i=1 to n-k)((y_i - mean(y)) * (y_(i+k) - mean(y)))
         / sum_(i=1 to n)((y_i - mean(y))^2)
```

This is evaluated independently for timing `x` and frame length `l` in each capture. For feature `f` and lag `k`:

```text
d_f(k) = abs(r_f,reference(k) - r_f,generated(k)) / 2
```

Every sample autocorrelation is in `[-1, 1]`; its absolute inter-capture difference is at most two, so dividing by two makes every component distance `[0, 1]`. The configured weights define:

```text
D = sum_(k in K)(lag_weight_k *
      (timing_feature_weight * d_timing(k)
       + frame_length_feature_weight * d_frame_length(k)))
similarity = 1 - D
```

Positive unit-sum feature and lag weights preserve `D` and similarity in `[0, 1]`. Higher is more similar; `1` means all configured timing and frame-length autocorrelations are equal.

## Result and Failure

Method-defined details in `similarity.toml` record each configured lag, both captures' timing and frame-length autocorrelations for every lag, component distances, feature and lag weights, raw distance, observation count, and all reproducing configuration. The [similarity result contract](../../contracts/60_30_similarity_result/README.md) owns result validation, hashing, and atomic publication.

Configuration validation completes before scoring. Each capture requires at least `max(K) + 1` observations and at least `max(K) + 2` frames, so every lag has paired observations. Neither feature sequence in either capture may be a constant series: its zero denominator makes sample autocorrelation undefined. Invalid configuration, insufficient observations, a constant series, undefined autocorrelation, or a non-finite value or calculation makes evaluation unsuccessful. The method never drops a lag, invents a correlation, normalizes invalid weights, or publishes a partial or fallback similarity.

## Limits

The method detects configured linear repetition at selected lags. It does not preserve individual adjacent transitions or arbitrary nonlinear dependencies, and it does not compare contents, addresses, flows, direction, or higher-protocol behavior.

## Testing

Future deterministic unit tests cover the formula, positive and negative correlations, `[-1, 1]` and score bounds, per-lag difference divided by two, feature and lag weights, zero IAT, insufficient observations, a constant series, invalid configuration, and raw diagnostics. Future integration tests use small offline Ethernet PCAPNG fixtures and verify `similarity.toml` details. No test requires network access, live capture, sudo, root, or other elevated privilege.

## Reading

Follow the [architecture governance](../../README.md), the [similarity-method registry](../README.md), the shared [temporal Layer 2 rules](../TEMPORAL_L2.md), the [60 similarity evaluation application](../../apps/60_similarity_evaluation/README.md), and the [similarity result contract](../../contracts/60_30_similarity_result/README.md) before changing this method.
