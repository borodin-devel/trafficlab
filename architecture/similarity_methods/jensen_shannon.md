# Jensen--Shannon Similarity

## Purpose

Measure categorical mark and direction-conditioned timing differences with a
symmetric, finite, base-2 Jensen--Shannon divergence (JSD). This is a
descriptive two-sample distance: Trafficlab neither assumes IID packets nor
calculates an IID p-value.

## PMF support and definition

For integer count maps (c_R(x)) and (c_G(x)), construct PMFs only over the
union of observed categories. With (p_x=c_R(x)/n_R), (q_x=c_G(x)/n_G), and
(m_x=(p_x+q_x)/2), Trafficlab uses

\[
D_{JS}(P,Q)=\frac12\sum_{p_x>0}p_x\log_2\frac{p_x}{m_x}
           +\frac12\sum_{q_x>0}q_x\log_2\frac{q_x}{m_x}.
\]

Terms with zero source mass are omitted; no pseudocount is added. The base-2
definition lies in `[0, 1]`: identical PMFs have divergence zero and disjoint
PMFs have divergence one.

The mark component uses every packet's exact joint category
`(direction, frame_length)`. Direction is categorical, not an ordered number;
two otherwise equal frame lengths in opposite directions are different marks.

The IAT component uses every noninitial packet's destination direction and
IAT category `(following_direction, bin_index)`. For observation window `W`
and configured positive integer `B`, its shared edges are

\[
e_j=\frac{j}{B}\log(1+W),\qquad j=0,\ldots,B.
\]

An IAT maps through `log1p`. Bins are left-closed, so interior boundary values
use the later bin; the final endpoint is included in the last zero-based bin. These edges depend only on
the reference-derived `W`, never on generated values. Every compared trace has
at least two events and remains inside `[0, W]`, so all IAT values fit this
closed support.

## Aggregation and diagnostics

Finite nonnegative configured weights satisfy

\[
w_{IAT}+w_{mark}=1,\qquad
D=w_{IAT}D_{IAT}+w_{mark}D_{mark},\qquad s=1-D.
\]

Diagnostics retain `W`, the two feature weights, each component's sample
count, union-aligned category records with reference/generated integer counts,
the frozen IAT bin edges, both component JSDs, and the aggregate discrepancy.
Only negligible floating-point roundoff at a documented bound is clamped;
materially out-of-range arithmetic is rejected.

## Cost and deterministic examples

Counting is linear in packet count; sorting union support costs

\[
O((u_{mark}+u_{IAT})\log(u_{mark}+u_{IAT}))
\]

for the distinct categorical supports. Space is linear in those supports.

- Equal exact mark and IAT PMFs have score `1`.
- Two disjoint singleton PMFs have base-2 JSD `1`, with no pseudocount.
- With `W = 4` and `B = 2`, the edges are
  `(0, log1p(4)/2, log1p(4))`; an IAT at either positive edge follows the
  documented endpoint rule.

## References

- Lin, [“Divergence Measures Based on the Shannon Entropy”](https://doi.org/10.1109/18.61115), 1991.
