# 01 Reference Preparation

Training consumes one ordered reference capture. For input frames indexed
`0..N-1`, it derives observations for frames `1..N-1`:

```text
IAT_i = timestamp_i - timestamp_(i - 1)
observation_i = (IAT_i, original_frame_length_i)
```

At least two input frames are required. Every IAT must be finite and
non-negative; zero IAT is valid. Every original frame length must be an
integer from 60 through 1514 bytes. Invalid timestamps, negative IATs,
non-finite values, unsupported link-layer data, or invalid frame lengths fail
preparation rather than being repaired silently.

The first emitted frame is placed after its sampled IAT from generated time
zero. Generated timestamps are therefore nondecreasing and may be equal.
