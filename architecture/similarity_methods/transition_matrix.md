# Transition-Matrix Fidelity Diagnostic

## Purpose

Transition fidelity is a deterministic final-only diagnostic of local traffic
structure. It has no genetic-fitness weight and is blocked from all genetic
candidate and trial evaluation. Only the final comparison boundary can call it
on normalized traces in one shared closed observation window.

## Reference-frozen states

For positive configured size-bin count \(S\) and IAT-bin count \(I\), transform
reference frame lengths and noninitial IATs by `log1p`. Compute Type-7
(linear) quantiles at \(0,1/S,\ldots,1\) and \(0,1/I,\ldots,1\), respectively.
Those reference-only extrema and interior thresholds remain frozen while
encoding both traces.

Each event state is `(direction, size_category, iat_category)`. Direction is
categorical. Size categories are `below`, zero-based ordinary bins, and
`above`; IAT categories are `initial`, `below`, zero-based ordinary bins, and
`above`. Values outside the reference extrema use explicit `below` or `above`
categories, so generated outliers cannot move a threshold or alter the
vocabulary. Interior threshold values use the later ordinary bin. The first
event uses `initial`, never a fabricated numerical IAT.

The declared support is the complete two-direction Cartesian vocabulary. It is
formed before examining generated data and is the only state support that
receives smoothing. Require no more than 256 states and no more than 65,536
state-to-state cells before allocating rows. Each trace needs at least two
events; this supplies one noninitial IAT and a defined frozen state sequence.

## Components

Let \(c_R,c_G\) be aligned counts over declared support and let \(\alpha>0\)
be the configured additive pseudocount. Each PMF is

\[
p_x=\frac{c_R(x)+\alpha}{\sum_yc_R(y)+\alpha|V|},\qquad
q_x=\frac{c_G(x)+\alpha}{\sum_yc_G(y)+\alpha|V|}.
\]

Trafficlab uses base-2 Jensen--Shannon divergence on this same finite support:

\[
J(P,Q)=\frac12\sum_xp_x\log_2\frac{p_x}{(p_x+q_x)/2}
+\frac12\sum_xq_x\log_2\frac{q_x}{(p_x+q_x)/2}.
\]

It compares (1) state occupancy, (2) every conditional source row and takes
their unweighted mean JSD, including uniformly smoothed empty source rows, and
(3) run-length PMFs. Runs are maximal consecutive identical states. Their
reference-frozen support is lengths one through the largest reference run plus
an `overflow` category; longer generated runs enter that explicit edge.

With normalized configured weights \(w_O,w_T,w_R\),

\[
D=w_OJ_O+w_TJ_T+w_RJ_R,\qquad s=1-D.
\]

Diagnostics retain reference log thresholds, declared vocabulary, both encoded
state sequences, integer occupancy and transition counts, every smoothed row,
run counts/PMFs, all three component JSDs, weights, caps' realized counts, and
the final discrepancy. Material nonfinite or out-of-range arithmetic is an
error; only negligible endpoint roundoff is clamped.

## Deterministic checks and references

- Type-7 thresholds from a reference never change when generated values change.
- Opposite directions are distinct states even at equal size and IAT values.
- Empty source rows are uniform under positive smoothing.
- Identical traces have zero occupancy, row, and run divergences and score one.

The categorical state design, edge categories, active support, caps, row
average, and run overflow convention are Trafficlab definitions.

- Hyndman and Fan, ["Sample Quantiles in Statistical Packages"](https://doi.org/10.1080/00031305.1996.10473566), 1996.
- Lin, ["Divergence Measures Based on the Shannon Entropy"](https://doi.org/10.1109/18.61115), 1991.
