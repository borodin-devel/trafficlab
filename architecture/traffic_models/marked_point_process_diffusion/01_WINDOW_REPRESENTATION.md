# 01 Window Representation and Information Boundary

`window_width_seconds` is a positive finite duration and windows are
non-overlapping.  A window contains the events from one source file whose
event timestamps fall in its half-open interval, in source order.  The
explicit `max_events_per_window` is a positive integer bound.  A window with
more events than that bound is a preparation failure; it is not truncated.
The configured trailing-window policy is either `discard` or `include`; the
latter preserves a final shorter-duration window and records its effective
duration.  No window crosses a file boundary.

For a window `[window_start, window_start + effective_duration)` containing
`n` events at timestamps `timestamp_0 ... timestamp_(n-1)`, where
`0 <= n <= max_events_per_window`, IATs are re-anchored to that window:

```text
iat_0 = timestamp_0 - window_start                         when n > 0
iat_j = timestamp_j - timestamp_(j - 1)                    when 1 <= j < n
```

Thus the first IAT is from the window start, not from a source event before
the window, and `sum(iat_0 ... iat_(n-1))` is the offset of the last event
from the window start.  Each active `iat_j` is finite and non-negative; zero
IATs, and therefore equal event timestamps, are valid.  The canonical
representation is a prefix mask and padded matrix:

```text
m = (1 repeated n times, 0 repeated max_events_per_window - n times)
x_0[j] = (log1p(iat_j) / iat_feature_scale,
          (length_j - 60) / 1454)                     when m[j] = 1
x_0[j] = (0, 0)                                       when m[j] = 0
```

`iat_feature_scale` is finite and positive.  The normalized IAT coordinate
preserves zero IAT exactly because `log1p(0) / iat_feature_scale` is zero.
The prefix mask, equivalently the integer event count `n`, is an explicit
random part of the model rather than an end token inferred from padded values.
Only masked-in rows are events or receive continuous diffusion noise.  A
decoder must reject a non-prefix mask, a count outside the configured bound,
or a non-finite normalized event value.

At generation time the context is `c = (seed, H)`, where `H` is exactly the
ordered event pairs previously emitted by this model in earlier windows, capped
at `generated_history_events`.  The first window has empty `H`; after a window
is emitted its converted output is appended before the next window is sampled.
The context encoder has a learned empty-history representation and a seed
embedding.  It receives no target-window event, future generated event,
reference event, external label, address, packet bytes, payload, protocol,
flow, direction, session, interface state, or live-network state.  Fitting
uses the same empty or preceding model-generated-history context policy; it
does not add an undeclared teacher-forced condition.

A generated window is bounded independently of the generation stop policy.
After the model decodes all active rows to finite non-negative IATs, it must
validate their joint support: `sum(iat_seconds[0] ... iat_seconds[n-1])` is
strictly less than `window_width_seconds`.  This is
acceptance validation of the complete sample, not post-decode trimming.  If a
complete sampled window violates this support, generation of that window fails
with no partial output, dropping, clipping, rescaling, shortening, splitting,
resampling, clamping, or other repair.  Every accepted sampled count is emitted
intact; an empty window is valid.
