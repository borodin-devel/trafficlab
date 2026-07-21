# 01 Conditional Event Laws

### Inter-arrival time

The conditional IAT law is an explicitly normalized, non-negative continuous
density. The normal model is a finite mixture of log-normal components whose
mixing weights, log-location, and positive scale are functions of `h_i`.
Thus `iat_i > 0` under continuous sampling and the law has no negative mass.
For reference zero IATs, fitting uses the configured non-negative-
IAT observation policy: a deterministic atom-at-zero mixture with a sigmoid
probability plus the positive log-normal density. The combined likelihood is
defined on `[0, infinity)` and preserves valid simultaneous timestamps.

An implementation may substitute a conditional intensity only when it is
non-negative, integrable to a normalized next-event time density, numerically
stable over the configured time domain, and is recorded as a new schema
version. It must retain the same causal information boundary and zero-IAT
policy; it may not silently clamp negative samples.

### Frame-length mark

Conditioned on `h_i` and the sampled or observed `iat_i`, the mark law is a
continuous finite mixture of truncated normal densities on the closed physical
interval `[60, 1514]` bytes. Component weights and valid scales are neural
outputs. Truncation normalizes the density on that interval, so fitting and
sampling have no probability mass outside the Ethernet length domain.

After continuous sampling, length maps deterministically to an original
Ethernet frame length by round-half-to-even followed by exact range validation:

```text
integer_length = round_half_to_even(sampled_continuous_length)
require 60 <= integer_length <= 1514
```

This produces one integer from 60 through 1514 bytes inclusive; it neither
resamples nor makes random rounding choices. A non-finite sample or a result
outside that range is a generation failure, not an opportunity to clamp or
repair output.
