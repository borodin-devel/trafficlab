# Two-State Markov-Modulated Poisson Process

## Purpose

An MMPP changes Poisson arrival rate according to a latent continuous-time
Markov chain (CTMC). The MVP uses two states: a low-rate regime and a high-rate
regime. It is a compact classical model for burst and idle timing.

## Mathematical definition

The CTMC generator is

\[
Q=\begin{bmatrix}
-q_{01} & q_{01}\\
q_{10} & -q_{10}
\end{bmatrix},
\qquad q_{01},q_{10}>0.
\]

Conditional on CTMC state \(z\in\{0,1\}\), arrivals form a Poisson process with
rate \(\lambda_z\), constrained for identifiability by

\[
0<\lambda_0<\lambda_1.
\]

The arbitrary-time stationary regime probabilities are

\[
\pi_0=\frac{q_{10}}{q_{01}+q_{10}},\qquad
\pi_1=\frac{q_{01}}{q_{01}+q_{10}}.
\]

They describe time occupancy, not the regime seen at an arrival. With stationary
mean arrival rate

\[
\bar\lambda=\pi_0\lambda_0+\pi_1\lambda_1,
\]

the event-stationary, or arrival-epoch, probabilities are

\[
a_0=\frac{\pi_0\lambda_0}{\bar\lambda},\qquad
a_1=\frac{\pi_1\lambda_1}{\bar\lambda}.
\]

Trafficlab normalizes the reference around an observed arrival at time zero, so
generation requires this rate-weighted arrival-epoch initialization. The
probability calculations must remain finite and normalized for large finite
positive rates, without overflowing their transition-rate, weighted-rate, or
mean-rate intermediates.

For the exact hand case

```text
q01 = 1, q10 = 3, lambda0 = 1, lambda1 = 9
pi = (3/4, 1/4)
lambda_bar = 3
a = (1/4, 3/4)
```

the high-rate regime has only one quarter of arbitrary-time occupancy but three
quarters of the arrival-epoch mass. Asanjarani and Nazarathy give the
rate-weighted event-stationary vector for Markovian arrival processes and
distinguish it from the time-stationary vector [asanjarani-nazarathy].

## Chromosome and fitting

The canonical chromosome order is `(q01, q10, lambda0, lambda1)`. All four genes
use the
[generic logarithmic coordinate](../genetic_models/basic_generational.md#gene-coordinates-and-initialization)
and have finite positive configured bounds `0 < L < U`.

Repair is deterministic and consumes no RNG:

1. reject nonfinite values;
2. leave `q01` and `q10` in their named positions and sort `lambda0` and
   `lambda1` into ascending order;
3. defensively clamp every ordered value to its named positive bound;
4. validate every named bound and `0 < lambda0 < lambda1`.

Repair never swaps `q01` with `q10` and never adds jitter to equal arrival
rates. If named clamping destroys strict arrival-rate order, the candidate is
invalid.

The finite GA fits these values directly using generated-trace similarity. It
is not a likelihood estimator or proof that the fitted latent mechanism caused
the observed traffic. Fitting the non-timing part stores the reference joint
empirical distribution of `(direction, frame_length)`.

## Generation

Generation must:

1. draw the initial regime from \((a_0,a_1)\), assigning a unit-uniform draw
   below \(a_0\) to regime zero and the threshold or above to regime one;
2. emit the conditioned arrival at `t=0` with a joint empirical mark;
3. draw the next arrival clock and then the regime-transition clock in the
   existing order;
4. continue the existing exact competing-clock and reliability behavior.

In regime `z`, the arrival clock is `Exponential(lambda_z)` and the transition
clock is `Exponential(q_z,1-z)`. Advance by the smaller time when its
`next_time <= W`:

- on an arrival, emit a joint empirical direction/length mark and remain in `z`;
- on a regime change, switch state without emitting a packet.

The exponential distribution is memoryless, so both clocks may be resampled
after either event. An exact floating-point tie is resolved as a regime change
first, then clocks are resampled; this deterministic convention prevents double
events. Finish normally when the next arrival/regime-change event has
`next_time > W`.

If a packet-count, output-size, or wall-time reliability guard is reached before
that comparison establishes full-window completion, return incomplete
generation.

Schema 5 preserves the schema-4 local PCG64 generator and schema-3 scalar order. The exact order is `random()`
for the arrival-epoch regime, `choice(total_mark_count)` for the time-zero mark,
then `exponential(scale=1.0/lambda_z)` followed by
`exponential(scale=1.0 / (q01 if z == 0 else q10))` for every competing-clock
race. An arrival
inside `[0, W]` then consumes `choice(total_mark_count)`; a regime transition or
out-of-window race consumes no mark draw. Choices use half-open scalar indexes.

A candidate that generates too few packets for a mandatory similarity method
receives invalid candidate fitness. Zero or nonfinite rates, a missing mark
distribution, or a derived time-stationary or arrival-epoch probability outside
`[0, 1]` are structural errors.

## Trafficlab-specific choices

Direct simulation-fitness optimization, the two-state restriction,
arrival-epoch initialization, the conditioned packet at time zero for trace
alignment, tie handling, and regime-independent joint empirical marks are
Trafficlab choices. Conditioning on the packet at time zero is not a property
of an unconditioned MMPP. Packet marks do not reveal or depend on the latent
regime in this MVP.

## Computational cost

Storing empirical marks takes \(O(n)\) fitting time and space in the worst case.
Generating \(m\) arrivals and \(r\) regime changes takes \(O(m+r)\) time and
\(O(m)\) output space.

## Deterministic test examples

- `q01=1`, `q10=3` gives time-stationary probabilities `pi0=0.75`,
  `pi1=0.25`.
- With `lambda0=1` and `lambda1=9`, the exact hand case gives
  `a=(1/4, 3/4)` at an arrival epoch.
- Repair preserves those named transition-rate positions rather than sorting
  them.
- Swapping unordered arrival-rate genes repairs them into low/high order.
- Every repaired rate remains within its own named positive bound.
- Nonpositive or equal post-repair arrival rates are rejected.
- A stub RNG sequence verifies arrival-versus-regime event selection and tie
  handling.
- The initial regime threshold uses `a0`; the conditioned joint empirical mark
  is emitted at time zero before the arrival and transition clocks are drawn.
- A scripted event at `W` is processed and one after `W` finishes normally.
- A reliability guard reached while the next event is within `W` returns
  incomplete generation.
- Equal model, seed, and limits produce the same regimes, timestamps, and marks.

## Direct scientific validation

Bounded tests with predeclared seeds, sample sizes, tolerances, and analytical or
independent test-only oracles must directly cover the arrival-epoch state
mixture, long-run mean arrival rate \(\bar\lambda\), arbitrary-time regime
occupancy \(\pi\), serial dependence, complete-window generation, and joint
empirical marks. Exact tests must also cover the initial-state threshold, the
regime/mark/arrival-clock/transition-clock RNG order, competing-clock ties, and
finite normalized probabilities for large finite rate inputs. Round trips and
same-implementation fixed-seed generation are not sufficient scientific
evidence.

## References

- Wolfgang Fischer and Kathleen Meier-Hellstern, [“The Markov-modulated Poisson
  process (MMPP) cookbook”][mmpp-cookbook], *Performance Evaluation* 18(2),
  149–171, 1993.

[mmpp-cookbook]: https://doi.org/10.1016/0166-5316(93)90035-S
[asanjarani-nazarathy]: https://arxiv.org/abs/1905.01736
