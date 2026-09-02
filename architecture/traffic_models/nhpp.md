# Piecewise-Constant Nonhomogeneous Poisson Process

## Purpose

The nonhomogeneous Poisson process (NHPP) represents repeatable changes in
arrival intensity across a normalized observation window without latent state.
Trafficlab uses equal-width, piecewise-constant bins and empirical joint
direction/length marks.

## Definition and fitting

For `B = bin_count` and window `W`, bins are `[bW/B, (b+1)W/B)` except the last,
which is closed at `W`. Their width is `h = W/B`, and the fitted intensity is

\[
\hat\lambda_b = N_b/h,
\]

where `N_b` counts reference arrivals after the conditioned packet at time zero.
The time-zero packet is excluded from rate evidence, while an event at `W`
belongs to the final bin. Rates may be zero. The integer chromosome is
`(bin_count)`; exact integers are clamped to configured inclusive bounds in
`2..16`.

Each bin stores its first-appearance joint empirical `(direction, frame_length)`
marks. Empty tables use the global empirical distribution. The time-zero
generated event uses bin zero's table (or that global fallback).

## Generation and reproducibility

Generation emits one marked conditioned event at `t=0`. Every positive-rate bin
draws `Exponential(scale=1/lambda_b)` from its local clock. A draw beyond a
nonfinal bin advances to the next bin and discards its residual. Zero-rate bins
advance with no random or mark draw. An event at `W` is emitted.

Schema 5 requires `numpy.random.Generator(numpy.random.PCG64(seed))`. The exact
order is the time-zero `choice`, then each positive-bin `exponential`, followed
by a `choice` only for an in-bin arrival. Crossing, exhausted, and zero-rate
bins consume no mark draw. Packet, byte, and wall guards apply before draws,
after draws, and before emission.

## Limits and validation

Fitting is `O(n + B)` and generating `m` arrivals is `O(m + B)`. The model
captures changing marginal rate, not latent-state or serial dependence. Direct
scientific validation uses independent analytical means `lambda_b h` per bin,
their integrated sum, zero-bin absence, and active-bin mark frequencies under
fixed seeds and declared tolerances.

## References

- D. R. Cox and V. Isham, *Point Processes*, Chapman and Hall, 1980.
