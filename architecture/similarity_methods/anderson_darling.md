# Anderson--Darling Similarity

## Purpose

Measure a tail-amplified difference between the IAT or frame-size ECDFs. It is
a descriptive two-sample distance; packet serial dependence and tied values
mean Trafficlab does not calculate or publish an IID p-value.

## Definition

Use the same pooled, ordered, tie-aware support scan as
[Cramér--von Mises](cramer_von_mises.md). After consuming all observations at
support \(u_j\), let \(F_j\), \(G_j\), and \(H_j\) be the reference, generated,
and pooled ECDFs:

\[
H_j=\frac{\sum_{k\le j}(a_k+b_k)}{n+m}.
\]

For every support point strictly before the pooled endpoint, assign

\[
q_j=\frac{1}{H_j(1-H_j)}.
\]

The endpoint \(H_j=1\) is excluded rather than evaluated with a zero
denominator. Trafficlab records the raw tail-weighted sum and maps it to a
bounded discrepancy by the accepted-weight total:

\[
T_{AD}=\sum_{H_j<1}q_j(F_j-G_j)^2,\qquad
Q_{AD}=\sum_{H_j<1}q_j,
\]
\[
D_{AD}=\begin{cases}T_{AD}/Q_{AD},&Q_{AD}>0,\\0,&Q_{AD}=0,\end{cases}
\qquad s_{AD}=1-D_{AD}.
\]

This endpoint normalization makes the descriptive discrepancy lie in `[0, 1]`.
The zero-denominator case occurs only when the entire pooled sample has one
support value, where both ECDFs are equal.

## Trace inputs and aggregation

`anderson_darling_similarity` compares IAT and frame-size samples separately in
global, canonical `outbound`, and canonical `inbound` strata, retaining zero
IATs. IAT direction belongs to the destination packet. It combines each
stratum's bounded feature discrepancies under finite nonnegative weights
summing to one:

\[
D=w_{IAT}D_{IAT}+w_{size}D_{size},\qquad s=1-D.
\]

Strict configured `global`/`uplink`/`downlink` weights then aggregate the three
stratum discrepancies exactly; `uplink` maps to canonical `outbound` and
`downlink` to canonical `inbound`. A feature absent from both traces in one
stratum has discrepancy zero. A one-sided empty feature has discrepancy one,
without a fabricated sample or tail weight. Zero-weight strata remain retained
and validated.

## Diagnostics and edge cases

Per-stratum feature diagnostics retain empty/comparison status, sample and tie
counts, raw sum \(T_{AD}\), normalization weight \(Q_{AD}\), and normalized
discrepancy. Top-level diagnostics retain the observation window, feature and
canonical-direction stratum weights, each stratum result, and final discrepancy.
No p-value is reported.

Both traces need two canonical events, and direct sample calls require
nonempty finite numeric samples. Equal values are consumed together before an
ECDF comparison. The lowest or highest tail can contribute more than a central
support difference of the same ECDF size; this is the intended AD behavior.

## Computational cost

Counting and scanning are linear in the total sample count; sorting pooled
support costs \(O((n+m)\log(n+m))\). The straightforward implementation uses
\(O(n+m)\) space for counts and support.

## Deterministic test examples

- `[1]` versus `[2]` has one accepted support point with an ECDF difference of
  `1`, so `D_AD = 1` and score `0`.
- A lower-tail one-step difference in `[0, 2, 3, 4]` versus `[1, 2, 3, 4]` has
  a larger discrepancy than the corresponding central difference in
  `[0, 1, 3, 4]` versus `[0, 2, 3, 4]`.
- Identical tied samples, including zero IATs, have score `1`.

## References

- Scholz and Stephens, [“K-Sample Anderson–Darling Tests”](https://doi.org/10.1080/01621459.1987.10478517), 1987.
