# 01 Windows and Sequences

The explicit finite configuration contains a positive comparison horizon `H`
and a positive fixed window width `w`. It creates `ceil(H / w)` ordered,
non-overlapping windows beginning at time zero:

```text
window_k = [k * w, min((k + 1) * w, H))
for k = 0, ..., ceil(H / w) - 1
```

Only noninitial frames have an IAT feature. For every noninitial frame `i`
whose aligned timestamp `t_i` is in `[0, H)`, the method appends, in recorded
order, the following time-augmented feature point to its window `k`'s ordered
packet-feature sequence:

```text
z_i = ((t_i - k * w) / w,
       log1p(IAT_i in seconds) / iat_feature_scale,
       original_frame_length_i / frame_length_feature_scale)
```

A frame at or after `H` is excluded from scoring. Every admitted packet has a
local time coordinate in `[0, 1)`, including in the half-open final window.
The base-kernel input domain consists of the three-dimensional vectors
`a = (0, 0, 0)` and the packet-feature values `z_i` above. Let `φ` be the
Gaussian-RBF feature map from this three-dimensional input domain into its
RKHS. For each window `k`, its path is explicitly the piecewise-linear
interpolation through `φ(a), φ(z_1), ..., φ(z_r)`, where
`z_1, ..., z_r` are that window's packet-feature values in recorded order. The
empty-window path is constant at `φ(a)`. The anchor is mathematical only: it
is not a packet and adds no frame, bytes, IAT, or timestamp. Every configured
window is retained. Thus both captures always contribute exactly `ceil(H / w)`
ordered path samples, including empty windows. Each input needs at least two
frames so that the timing--size observation domain is defined.
