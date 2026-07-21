# 03 Score, Validation, Limits, and Testing

The primary similarity is the configured positive-scale monotonic mapping:

```text
similarity = exp(-raw_MMD / score_mapping_scale)
```

It is in `(0, 1]`, and therefore `[0, 1]`; higher is more similar and `1`
means the two fixed collections of window sequences have zero empirical MMD
under the configured sequence kernel. The mapping scale is required because
raw MMD has no method-independent interpretation for ranking.

Method-defined details in `similarity.toml` record `raw_MMD`,
`MMD_squared`, the horizon, window width, total window count, nonempty and
empty window counts for each capture, frames excluded at or after the horizon,
signature-kernel parameters, feature and mapping scales, numerical tolerance,
and the selected mathematical-library identity and version when one affects
the result. The [similarity result
contract](../../contracts/60_30_similarity_result/README.md) owns result
validation, hashing, and atomic publication.

## Validation and Failure

Configuration validation completes before PCAPNG processing. Both inputs must
pass the shared rules and contain at least two frames. The horizon, window
width, `iat_feature_scale`, `frame_length_feature_scale`,
`base_kernel_bandwidth`, `score_mapping_scale`, every degree weight, and
numerical tolerance must be finite and positive; the signature degree must be
a positive integer. Every kernel value, self-kernel denominator, intermediate
sum, `MMD_squared`, `raw_MMD`, and score must be finite. Kernel values outside
their required bounds beyond the configured numerical tolerance are invalid:
`k_base` must be in `[0, 1]` and normalized `K` must be in `[-1, 1]`. A finite
negative normalized `K` within that range is valid and is not a failure.

Mathematically, `MMD_squared` is non-negative. A computed negative value with
absolute magnitude at most `numerical_absolute_tolerance` is represented as
exact zero before its square root; a more negative value is a numerical
failure. No other score, kernel, or diagnostic value is clamped, repaired, or
substituted. Invalid configuration, inadequate input, a library failure, an
invalid denominator or kernel value, or any invalid numerical result makes the
evaluation unsuccessful, with no partial or fallback similarity published.

## Limits

This method compares complete anchored, ordered timing--size paths inside each
fixed window, rather than independent observations or only adjacent pairs.
MMD then compares the empirical collection of those whole-window paths: it
does not preserve ordering between windows, compare observations after the
horizon, or compare packet bytes, addresses, flows, direction, or
higher-protocol behavior. Its sensitivity depends on the configured horizon,
window width, signature degree and weights, and three-dimensional base-kernel
scales and bandwidth.

## Testing

Future deterministic unit tests cover window boundaries, time-zero handling,
local time coordinates, the mathematical anchor, empty constant-anchor paths,
post-horizon exclusion, ordered path construction, base-kernel bounds and
characteristic configuration, signature-kernel symmetry and bounds including
valid negative normalized values, MMD against authoritative examples or an
independent implementation, mapping bounds, window-count diagnostics, zero
IAT, invalid configuration, insufficient input, and numerical tolerance
handling. Future integration tests use small offline Ethernet PCAPNG fixtures
and verify `similarity.toml` details. No test requires network access, live
capture, sudo, root, or other elevated privilege.

## Reading

Follow the [architecture governance](../../README.md), the
[similarity-method registry](../README.md), the shared [temporal Layer 2
rules](../TEMPORAL_L2.md), the [60 similarity evaluation
application](../../apps/60_similarity_evaluation/README.md), and the
[similarity result contract](../../contracts/60_30_similarity_result/README.md)
before changing this method.
