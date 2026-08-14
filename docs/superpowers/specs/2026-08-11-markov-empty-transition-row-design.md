# Markov Renewal Empty Transition Row Design

## Problem

The Markov Renewal model permits additive smoothing `alpha = 0`. An active
state may occur only as the final reference packet, so it has no observed
outgoing transitions. For that state, every transition count is zero and the
published estimator becomes `0 / 0`, even though generation requires a valid
next-state probability row.

The MVP must define this case without removing exact zero smoothing, adding a
configuration option, or inventing another estimator.

## Transition Estimator

Let

\[
N_j=\sum_m N_{jm}
\]

be the total number of observed transitions leaving active state `j`, and let
`K >= 1` be the number of active states.

When `N_j + alpha*K > 0`, retain the existing additive estimator:

\[
p_{jk}=\frac{N_{jk}+\alpha}{N_j+\alpha K}.
\]

The denominator is zero only when both `N_j = 0` and `alpha = 0`. In that case,
define the row as

\[
p_{jk}=\frac{1}{K}
\]

for every active destination state `k`.

This is the continuous extension of the existing estimator. When `N_j = 0` and
`alpha > 0`, the formula already gives

\[
\frac{0+\alpha}{0+\alpha K}=\frac{1}{K}.
\]

The defined row is finite, nonnegative, and sums to one. It is a Trafficlab
boundary rule for an unobserved outgoing row, not a claim that the reference
observed uniform behavior.

## Fit and Generation Behavior

Fitting applies the uniform rule only to an active source state with no observed
outgoing transition when `alpha = 0`. Other zero-smoothed rows remain their
ordinary empirical transition frequencies. Positive smoothing continues to use
the published formula for every row.

The rule consumes no RNG. The fitted transition matrix stores the uniform row
in the same form as every other row. Serialization adds no field or special row
type.

Generation samples a destination from the resulting row normally. Because an
empty source row has no source-specific holding-time observations, the existing
sparse timing fallback reaches the global IAT sample. That fallback does not
change.

Loading validates every row as finite, nonnegative, and summing to one within
the documented numerical tolerance. `K = 0`, a missing or invalid global IAT
sample, or any malformed stored row remains an invalid model. The uniform rule
does not hide structural errors.

The formerly undefined empty-row case is a valid candidate and does not receive
fitness `0` merely because `alpha = 0`.

## Deterministic Evidence

Use the state sequence `[A, B]`, where `B` appears only as the final packet and
therefore has no outgoing transition. With `alpha = 0` and `K = 2`, fitting must
produce

\[
P=\begin{bmatrix}
0 & 1\\
1/2 & 1/2
\end{bmatrix}.
\]

Focused tests also require:

- a nonempty zero-smoothed row to equal its empirical transition frequencies;
- a positive-smoothing empty row to equal the same uniform row through the
  ordinary formula;
- a stub RNG to enter the final-only state, select from its uniform row, and use
  the global IAT fallback;
- serialization and loading to preserve and validate the row;
- equal model, seed, window, and limits to produce the same generated trace;
- missing or invalid global IAT data to remain invalid.

One small model integration fixture contains a final-only active state. Fitting
with `alpha = 0`, generation, model JSON round-trip, and fixed-seed reproduction
must complete without an undefined row or invalid-candidate result.

## Documentation Scope

Implementation updates only:

- `architecture/traffic_models/markov_renewal.md` for the exact estimator and
  generation interaction;
- `architecture/TESTING.md` for mathematical, serialization, and integration
  evidence;
- `architecture/ROADMAP.md` Phase 4 for implementation ownership.

No experiment setting, artifact field, traffic model, generic abstraction,
random policy, timing fallback, genetic behavior, or similarity method changes.
