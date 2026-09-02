# Markov Packet-Train Model

## Purpose

Packet trains describe burst structure at a coarser scale than packet-level
states. Trafficlab separates a reference trace at unusually long gaps, models
the capped train-length sequence as a Markov chain, and retains individual
packet marks and gaps conditioned on train state and packet position. The model
does not retain or replay a complete reference train or packet subsequence.

## Reference-only segmentation

For the `n - 1` adjacent reference gaps, let `theta` be their Hyndman--Fan Type
7 quantile at the fixed level `q = 0.90`. For sorted gaps
`x_(0) <= ... <= x_(m-1)`, `h = (m - 1)q`, `j = floor(h)`, and `g = h - j`,

\[
\theta=(1-g)x_{(j)}+g x_{(j+1)}.
\]

A gap at or below `theta` remains inside its current train. Only a gap strictly
greater than `theta` starts a new train. The fitted payload stores the exact
floating threshold, `gap_quantile = 0.9`, and endpoint name
`less_than_or_equal`. At least one inter-train gap is required; without it the
empirical model cannot establish full-window completion after a train.

Consecutive separating gaps are valid and create singleton trains. A singleton
packet belongs only to the `first` position class. In a longer train, packet
zero is `first`, the final packet is `last`, and every other packet is
`interior`. The last interval is always retained, including a singleton final
train.

## Capped train states and estimators

The single integer chromosome is `(length_cap)`, with configured inclusive
bounds inside `3..8`. Repair accepts one exact integer and clamps it to those
bounds without consuming randomness. For actual train length `L_r`, its state
is

\[
S_r=\min(L_r, C),
\]

where `C` is the repaired cap. Only states observed in the reference are active,
in first-appearance order. Every active state retains its full ordered
`actual_lengths` reservoir, so the capped state never substitutes for an
emitted train length. The initial-state distribution is the empirical train
occupancy

\[
\pi_j=\frac{\#\{r:S_r=j\}}{R}.
\]

For active-state transition counts `N_jk` and `K` active states, Trafficlab uses
the fixed additive pseudocount one:

\[
p_{jk}=\frac{N_{jk}+1}{\sum_m N_{jm}+K}.
\]

An unobserved row is therefore uniform. The fitted model records those rows and
their use in generation diagnostics.

Each state stores three separate first-appearance-ordered empirical joint
`(direction, frame_length)` distributions: `first`, `interior`, and `last`.
Unused position pools are empty. Counts must agree exactly with every stored
actual length. Within-train gaps are stored individually by state and the
destination packet's `interior` or `last` class. Every within gap must be finite,
nonnegative, and at most `theta`.

## Inter-train gaps and sparse fallback

Every observed separating gap is retained in three aligned views: the selected
source/destination cell, its source-state row, and the global pool. A smoothed
transition may select a cell with no observed gap. Timing then uses the first
nonempty reservoir in this fixed order:

1. the exact source/destination transition pool;
2. every inter-train gap leaving the source state;
3. the global inter-train gap pool.

Every inter-train value is finite and strictly greater than `theta`. The fitted
diagnostics serialize the tier selected for every transition cell, reference
usage counts, and indexes of source rows without observed outgoing gaps.
Generation reports the four canonical Markov counters: three timing-tier counts
and the count of transitions sampled from unobserved rows. A fallback supplies
defined finite-sample behavior; it is not evidence for an unobserved
conditional timing law.

## Generation and reproducibility

One call owns `numpy.random.Generator(numpy.random.PCG64(seed))`. The exact
scalar draw order is:

1. `random()` for the initial active state;
2. `choice(actual_length_count)` for its actual train length;
3. `choice(first_mark_population)` for the time-zero packet;
4. for each later packet in that train, `choice(within_gap_count)`, followed
   only for an in-window timestamp by `choice(position_mark_population)`;
5. after a completed train, `random()` for the next state and
   `choice(inter_train_gap_count)` from the selected fallback tier;
6. only when that next train starts at or before `W`,
   `choice(actual_length_count)` and `choice(first_mark_population)`, then
   repeat steps 4--6.

All `choice` calls are scalar and use the half-open integer range. A packet at
exactly `W` is emitted; a proposed packet after `W` completes generation without
drawing its mark or later train attributes. The clock and resource guards are
checked before stochastic decisions, immediately after each draw, and before
each in-window packet. Packet or byte exhaustion in the middle of a train
returns an incomplete diagnostic prefix rather than finishing the stored train.

Generation samples every packet, within gap, boundary gap, state, and actual
length separately. Neither runtime state nor the wire payload has a whole-trace,
whole-train, or packet-sequence template field.

## Validation, scope, and cost

Loading binds `length_cap` to the outer chromosome, recomputes the Type-7 q90
from the complete fitted within/inter gap multiset, rebuilds train and packet
count identities, reconstructs additive transition rows, and checks every
redundant diagnostic. Unknown fields, wrong scalar types, malformed matrices,
nonfinite values, duplicate marks, and altered endpoint or estimator constants
are errors.

For `n` packets, `R` trains, and `K` active capped states, fitting costs
`O(n + K^2)` time and `O(n + K^2)` fitted space. Generating `m` packets costs
`O(mK)` with cumulative transition sampling and `O(m)` output space. The model
represents burst/train dependence; it does not model payloads, protocols, or a
causal network mechanism.

## Deterministic examples and direct evidence

- Gaps equal to `theta` remain in their train; only larger gaps split.
- Train lengths `(3, 4, 6)` under cap four produce states `(3, 4, 4)`,
  occupancy `(1/3, 2/3)`, and actual reservoirs `3:(3)`, `4:(4,6)`.
- With state order `(3,4)`, observed transitions `3->4` and `4->4`, and
  pseudocount one, both transition rows are `(1/3, 2/3)`.
- A 13-packet hand trace with ten gaps of one and two gaps of ten has Type-7
  q90 `9.1`, three trains, ten within gaps, and two inter-train gaps.
- Scripted generation independently exercises transition, source, and global
  gap fallbacks, scalar order, a packet exactly at `W`, and guard exhaustion
  after an interior packet.
- Payload mutation covers every fitted constant, matrix, pool, outer-gene bind,
  and diagnostic; schema round trips prove no template field is admitted.

The scientific oracle calculates the three-train segmentation, occupancy,
transition matrix, actual-length distributions, gap bounds, and position-mark
frequencies directly from raw literals without importing production helpers.

## Reference

- Raj Jain and Shawn Routhier, [“Packet Trains—Measurements and a New Model for
  Computer Network Traffic”](https://doi.org/10.1109/JSAC.1986.1146410),
  *IEEE Journal on Selected Areas in Communications*, 1986.
