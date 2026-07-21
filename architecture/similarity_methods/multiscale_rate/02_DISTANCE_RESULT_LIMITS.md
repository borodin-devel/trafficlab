# 02 Distance, Result, and Limits

## Distance and Score

For non-negative vectors `a` and `b`, normalized L1 distance is:

```text
L1_normalized(a, b) = sum_k(abs(a[k] - b[k])) / sum_k(a[k] + b[k])
```

The denominator is positive because each valid capture contributes its
time-zero frame before the positive horizon. Thus each component distance is
in `[0, 1]`. At scale `w`, the packet and original-byte components combine as:

```text
D_w = packet_feature_weight * L1_normalized(P_reference,w, P_generated,w)
    + original_byte_feature_weight
      * L1_normalized(B_reference,w, B_generated,w)
```

The raw multi-scale distance and primary similarity are:

```text
D = sum_w(scale_weight_w * D_w)
similarity = 1 - D
```

Validated positive unit-sum feature and scale weights preserve `D` and the
primary similarity in `[0, 1]`. Higher similarity is more similar and `1`
means that all configured packet and original-byte count vectors are equal.

## Result and Failure

Method-defined details in `similarity.toml` record `D`, every scale's window
width, bin count, packet component distance, original-byte component distance,
combined scale distance, feature and scale weights, and reference and
generated counts of frames excluded at or after the horizon. They also record
the horizon and all configuration needed to reproduce the score. The
[similarity result contract](../../contracts/60_30_similarity_result/README.md)
owns result validation, hashing, and atomic publication.

An empty capture, non-positive or non-finite horizon or width, duplicate or
empty width set, non-finite data or calculation, invalid weights, or an
undefined normalized-L1 denominator makes the evaluation unsuccessful. The
method never drops required zero-count bins, repairs invalid data, normalizes
invalid weights, or publishes a partial or fallback score.

## Limits

This method measures coarse packet and byte-rate structure: it detects bursts
and idle periods at the configured scales, but loses packet order and exact
timing inside a bin. It does not compare timing--size association within a
bin, packet-size distribution apart from aggregate original bytes, or any
frames at or after the configured horizon. It also does not compare contents,
addresses, flows, or protocol behavior.
