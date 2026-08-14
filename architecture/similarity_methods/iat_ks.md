# Inter-Arrival-Time KS Similarity

## Purpose

Measure the maximum difference between the marginal empirical distributions of
packet inter-arrival time (IAT). It complements frame-size KS but does not
measure serial ordering between IAT values.

## Sample and definition

For ordered timestamps, construct

\[
\Delta_i=t_{i+1}-t_i,qquad i=1,\ldots,n-1.
\]

At least two packets are required. Timestamps must be finite and nondecreasing.
Zero IATs are retained because multiple captured packets can share a timestamp;
negative IATs are errors.

Using the empirical CDF

\[
\widehat F_n(x)=\frac{1}{n}\sum_i\mathbf 1[X_i\le x],
\]

define

\[
D_{IAT}=\sup_x|\widehat F_{\Delta_R}(x)-\widehat F_{\Delta_G}(x)|,
\qquad s_{IAT}=1-D_{IAT}.
\]

Distance and score both lie in `[0, 1]`.

## Algorithm

Sort the two IAT samples, scan their merged unique values, consume ties before
comparing ECDFs, and take the exact maximum difference. Do not bin, discard
zeros, add jitter, or normalize by mean IAT.

## Diagnostics and edge cases

Return `distance`, both IAT sample counts, both zero counts, medians, and a
configured upper empirical quantile such as `0.95`. For a sorted sample of
`n` values and `0 < q < 1`, the quantile is the nearest-rank order statistic at
the one-based rank `ceil(q*n)`. The median diagnostic is defined separately:
for odd `n` it is the middle value, and for even `n` it is the arithmetic mean
of the two middle values.

Identical IAT multisets score `1` even if their order differs. Two packets give
one valid IAT. A one-packet trace, decreasing timestamp, nonfinite timestamp, or
invalid diagnostic quantile is an error. Trafficlab reports no KS p-value.

## Trafficlab-specific choices

Retaining tied zero IATs, using `1 - D` as similarity, selecting diagnostics,
and using the statistic descriptively are Trafficlab choices.

## Computational cost

IAT construction is linear. Sorting and comparison take
\(O(n\log n+m\log m)\) time and \(O(n+m)\) straightforward storage.

## Deterministic test examples

- Times `[0, 1, 3]` produce IATs `[1, 2]`.
- Equal timestamp sequences score `1`.
- IAT samples `[1, 2]` and `[1, 3]` have distance `1/2` and score `1/2`.
- Times `[0, 0, 1]` retain one zero IAT.
- Times `[1, 0]` and a one-packet trace are rejected.

## References

- NIST/SEMATECH, [“Kolmogorov-Smirnov 2-Sample Goodness of Fit Test”](https://itl.nist.gov/div898/software/dataplot/refman1/auxillar/ks2samp.htm), *e-Handbook of Statistical Methods*.
