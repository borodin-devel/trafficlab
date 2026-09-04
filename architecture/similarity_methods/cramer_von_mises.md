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

`cramer_von_mises_similarity` applies the formula separately to IATs and frame
lengths in three strata: global, canonical `outbound`, and canonical `inbound`.
IATs retain zero intervals and use the direction of their destination packet;
frame lengths use each packet's own direction. At the configuration boundary,
the canonical directions are named `uplink` and `downlink`. Finite nonnegative
feature weights
\(w_{IAT}+w_{size}=1\) combine the component discrepancies:

\[
D=w_{IAT}D_{IAT}+w_{size}D_{size},\qquad s=1-D.
\]

produce one discrepancy in each stratum. Independently configured
`global`/`uplink`/`downlink` weights (v_G,v_U,v_D) are finite, nonnegative,
sum to one, and produce

\[
D=v_GD_G+v_UD_{outbound}+v_DD_{inbound}.
\]

Both features are evaluated in every stratum. When both traces have no sample
for one stratum/feature, its discrepancy is zero. When exactly one trace has no
sample, its discrepancy is one; no observation is fabricated. A zero-weight
stratum remains retained and validated in diagnostics.

## Diagnostics and edge cases

For each stratum and feature, return its status (`compared`, `both_empty`, or
`one_sided_empty`), reference/generated sample counts, duplicate-observation tie
counts, raw sum, normalization weight, and normalized discrepancy. Empirical
CvM comparisons have normalization weight `1`; empty-policy records use zero
raw sum and normalization. The top-level diagnostics retain the observation
window, feature weights, canonical-direction stratum weights, all three
stratum discrepancies, and the exact final discrepancy. No p-value is present.

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
- With feature weights `(IAT=1/4,size=3/4)` and stratum weights
  `(global=1/2,uplink=1/4,downlink=1/4)`, the direction-swapped three-packet
  example in the direct tests has discrepancies `(0,3/64,3/8)` and aggregate
  `27/256`.

## References

- Anderson, [“On the Distribution of the Two-Sample Cramér–von Mises
  Criterion”](https://doi.org/10.1214/aoms/1177704477), 1962.
