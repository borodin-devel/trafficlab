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

The stationary initial-state probabilities are

\[
\pi_0=\frac{q_{10}}{q_{01}+q_{10}},\qquad
\pi_1=\frac{q_{01}}{q_{01}+q_{10}}.
\]

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

The GA fits these values directly using generated-trace similarity. There is no
additional likelihood optimizer in the MVP. Fitting the non-timing part stores
the reference joint empirical distribution of `(direction, frame_length)`.

## Generation

Sample the initial regime from \(\pi\) and emit one joint empirical mark at time
zero. In regime `z`, independently sample time to the next arrival from
`Exponential(lambda_z)` and time to the next regime change from
`Exponential(q_z,1-z)`. Advance by the smaller time when its `next_time <= W`:

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

A candidate that generates too few packets for enabled metrics receives invalid
candidate fitness. Zero or nonfinite rates, a missing mark distribution, or a
stationary probability outside `[0, 1]` are structural errors.

## Trafficlab-specific choices

Direct simulation-fitness optimization, the two-state restriction, stationary
initialization, the forced packet at time zero for trace alignment, tie handling,
and regime-independent joint empirical marks are Trafficlab choices. The forced
packet is not a general MMPP property. Packet marks do not reveal or depend on
the latent regime in this MVP.

## Computational cost

Storing empirical marks takes \(O(n)\) fitting time and space in the worst case.
Generating \(m\) arrivals and \(r\) regime changes takes \(O(m+r)\) time and
\(O(m)\) output space.

## Deterministic test examples

- `q01=1`, `q10=3` gives stationary probabilities `pi0=0.75`, `pi1=0.25`.
- Repair preserves those named transition-rate positions rather than sorting
  them.
- Swapping unordered arrival-rate genes repairs them into low/high order.
- Every repaired rate remains within its own named positive bound.
- Nonpositive or equal post-repair arrival rates are rejected.
- A stub RNG sequence verifies arrival-versus-regime event selection and tie
  handling.
- The initial empirical mark is emitted at time zero in the stationary regime.
- A scripted event at `W` is processed and one after `W` finishes normally.
- A reliability guard reached while the next event is within `W` returns
  incomplete generation.
- Equal model, seed, and limits produce the same regimes, timestamps, and marks.

## References

- Wolfgang Fischer and Kathleen Meier-Hellstern, [“The Markov-modulated Poisson
  process (MMPP) cookbook”][mmpp-cookbook], *Performance Evaluation* 18(2),
  149–171, 1993.

[mmpp-cookbook]: https://doi.org/10.1016/0166-5316(93)90035-S
