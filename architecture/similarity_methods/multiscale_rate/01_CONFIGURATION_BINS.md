# 01 Configuration and Bins

## Configuration and Bins

The explicit finite method settings are a positive comparison horizon `H`, an
ordered nonempty set of distinct positive window widths `w_1, ..., w_m`, two
positive feature weights, and one positive scale weight for every window
width. The feature weights and scale weights each sum to `1`:

```text
packet_feature_weight > 0
original_byte_feature_weight > 0
packet_feature_weight + original_byte_feature_weight = 1

scale_weight_s > 0 for every configured scale s
sum_s(scale_weight_s) = 1
```

Weights are validated from their exact decimal configuration values; invalid
weights are never silently normalized. Each input must contain at least one
frame so its canonical time zero exists.

For width `w`, the method creates `ceil(H / w)` bins beginning at time zero:

```text
bin_k = [k * w, min((k + 1) * w, H))
for k = 0, ..., ceil(H / w) - 1
```

Frames whose aligned time is in `[0, H)` contribute to exactly one bin; a
frame at or after `H` is excluded from scoring. Every bin, including a bin
with no frames, remains in both vectors. For capture `X`, the two vectors at
scale `w` are:

```text
P_X,w[k] = number of frames in bin_k
B_X,w[k] = sum of original Ethernet frame lengths in bin_k
```
