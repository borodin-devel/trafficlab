# Cramér--von Mises Similarity

## Purpose

Measure the complete difference between two empirical distributions of IATs or
frame sizes. This is a descriptive two-sample ECDF distance: Trafficlab does
not calculate or publish an IID p-value.

## Definition

For nonempty reference and generated samples with sizes \(n\) and \(m\), pool
their distinct ordered support values \(u_j\). Let \(a_j\) and \(b_j\) be the
number of reference and generated observations equal to \(u_j\), and update
both ECDFs once after consuming every tie at that support:

\[
F_j=\frac{\sum_{k\le j}a_k}{n},\qquad
G_j=\frac{\sum_{k\le j}b_k}{m},\qquad
p_j=\frac{a_j+b_j}{n+m}.
\]

Trafficlab's bounded pooled-mass discrepancy is

\[
D_{CvM}=\sum_j p_j(F_j-G_j)^2,
\qquad s_{CvM}=1-D_{CvM}.
\]

The pooled masses sum to one and squared ECDF differences lie in `[0, 1]`, so
the discrepancy and score lie in `[0, 1]`. The raw sum is already the
normalized discrepancy and is retained in diagnostics. This is a bounded,
interpretable descriptive convention, rather than a calibrated classical test
statistic.

## Trace inputs and aggregation

`cramer_von_mises_similarity` applies the formula separately to all IATs and
all frame lengths. IATs retain zero intervals and are associated with the
direction of their destination packet for availability reporting. Frame lengths
use each packet's own direction. Finite nonnegative feature weights
\(w_{IAT}+w_{size}=1\) combine the component discrepancies:

\[
D=w_{IAT}D_{IAT}+w_{size}D_{size},\qquad s=1-D.
\]

The method reports each direction stratum's availability but compares complete
global feature samples at this pure-function boundary. A missing one-sided
stratum is never replaced with an invented observation.

## Diagnostics and edge cases

For each feature, return reference/generated sample counts, duplicate-observation
tie counts, raw sum, normalization weight `1`, and normalized discrepancy. The
top-level diagnostics retain the observation window, feature weights,
direction-stratum availability, and final discrepancy. No p-value is present.

Both traces require at least two canonical events so each IAT sample is
nonempty. Direct sample evaluation likewise requires each sample to be
nonempty and finite. Exact ties receive one joint ECDF update. Identical sample
multisets have discrepancy zero. A disjoint pair of singleton samples has
discrepancy `1/2`; that is the exact pooled-mass value, not an unbounded
classical statistic.

## Computational cost

Counting and scanning are linear in the total sample count. Sorting the pooled
distinct support dominates at \(O((n+m)\log(n+m))\) time. The counts and sorted
support use \(O(n+m)\) space in the straightforward implementation.

## Deterministic test examples

- `[1, 2]` versus `[1, 3]` has pooled masses `(1/2, 1/4, 1/4)` and discrepancy
  `1/16` at support `2`.
- `[1, 1, 2]` versus `[1, 2, 2]` consumes each tie group once and has
  discrepancy `1/18`.
- Identical tied samples, including zero IATs, have score `1`.

## References

- Anderson, [“On the Distribution of the Two-Sample Cramér–von Mises
  Criterion”](https://doi.org/10.1214/aoms/1177704477), 1962.
