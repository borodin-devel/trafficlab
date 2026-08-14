# Poisson Empirical Model

## Purpose and assumptions

This model is the independent-arrival baseline. It assumes stationary,
independent arrivals at a constant rate. It does not claim to reproduce bursts,
serial dependence, or time-of-day effects. Direction and frame length are
treated jointly as empirical marks but are independent of arrival time.

Given `n >= 2` normalized packets over the finite positive shared observation
window

\[
W=t_n-t_1,
\]

there are \(N=n-1\) observed inter-arrival intervals and the maximum-likelihood
rate estimate is

\[
\hat\lambda=\frac{N}{W}.
\]

This estimate is conditioned on the active span from the first through the last
observed packet. Silence before the first packet and after the last packet is
excluded, so \(\hat\lambda=(n-1)/W\) must not be interpreted as an unconditional
rate over a separately observed workload window. The packet at `t=0` is a
normalized observed arrival, not evidence that the underlying process began at
that instant.

A homogeneous Poisson process has independent exponential inter-arrival times

\[
f_\Delta(u)=\lambda e^{-\lambda u},\qquad u\ge0.
\]

## Chromosome and fitting

The canonical chromosome order is `(c_lambda)`. The gene uses the
[generic logarithmic coordinate](../genetic_models/basic_generational.md#gene-coordinates-and-initialization)
with finite configured bounds `0 < L < U`. The candidate rate is

\[
\lambda_g=c_\lambda\hat\lambda.
\]

Repair rejects nonfinite input, defensively clamps a finite value to `[L, U]`,
and validates `c_lambda > 0`. The repaired one-value tuple is the chromosome's
canonical serialized form.

Fitting computes \(\hat\lambda\) and stores the observed `(direction,
frame_length)` pairs and their frequencies. It rejects fewer than two packets,
non-monotonic timestamps, a nonpositive or nonfinite observation window,
nonpositive or nonfinite genes, and an empty mark distribution.

## Generation

Start the relative clock at zero and sample the first empirical mark. For each
later packet, sample \(\Delta\sim\operatorname{Exponential}(\lambda_g)\) and set
`next_time = clock + Delta`. Emit a sampled joint mark when `next_time <= W`.
Finish normally when `next_time > W`. If a packet-count, output-size, or
wall-time reliability guard stops the process before that comparison establishes
completion, return incomplete generation. The same fitted model, seed, window,
and guards produce exactly the same trace.

Zero sampled delays are permitted by floating-point generation only if the RNG
returns them; timestamps remain nondecreasing. A result too small for a
mandatory similarity method is a valid generation but an invalid fitness
candidate.

## Trafficlab-specific choices

- The GA scales the direct rate estimate instead of evolving an unconstrained
  rate.
- Direction and size are sampled as a joint empirical mark so their observed
  association is preserved.
- The first generated packet is placed at relative time zero; fitness compares
  subsequent inter-arrival intervals.

These choices are implementation definitions, not properties required by a
Poisson process.

## Computational cost

Fitting takes \(O(n)\) time and stores at most \(O(n)\) distinct marks.
Generating \(m\) packets takes \(O(m)\) time and \(O(m)\) output space.

## Deterministic test examples

- Packets at times `0, 1, 2` give \(\hat\lambda=1\).
- With \(c_\lambda=2\), the fitted generation rate is `2`.
- Logarithmic encoding maps `L` to `0` and `U` to `1`, and decoding maps those
  coordinates back to the same bounds.
- Repair leaves each exact positive bound unchanged and clamps finite values
  outside either bound.
- The same seed produces identical timestamps and joint marks.
- Stubbed `next_time == W` is emitted; `next_time > W` finishes without emission.
- A first packet followed by a next event after `W` is valid natural completion.
- A reliability guard reached while the next event is within `W` returns
  incomplete generation.
- A one-packet or zero-window reference trace is rejected.

## Direct scientific validation

Bounded tests with predeclared seeds, sample sizes, and tolerances must directly
cover exponential IAT behavior, mean generated rate over the active-span
interpretation, complete-window generation, and the frequencies of joint
empirical direction/length marks. Round-trip and same-seed tests alone do not
establish those properties.

## References

- Robert G. Gallager, [“Poisson Processes,” MIT 6.262 course notes,
  chapter 2][gallager], 2011.

[gallager]: https://ocw.mit.edu/courses/6-262-discrete-stochastic-processes-spring-2011/resources/mit6_262s11_chap02/
