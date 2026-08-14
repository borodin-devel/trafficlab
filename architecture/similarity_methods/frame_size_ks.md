# Frame-Size KS Similarity

## Purpose

Measure the maximum difference between the empirical distributions of captured
frame length. This is a descriptive two-sample distance. Trafficlab computes no
hypothesis-test p-value.

## Definition

For sample \(X_1,\ldots,X_n\), its empirical cumulative distribution function
is

\[
\widehat F_n(x)=\frac{1}{n}\sum_{i=1}^{n}\mathbf 1[X_i\le x].
\]

For reference lengths \(R\) and generated lengths \(G\), the two-sample
Kolmogorov-Smirnov distance and Trafficlab score are

\[
D_{KS}=\sup_x|\widehat F_R(x)-\widehat F_G(x)|,
\qquad s_{size}=1-D_{KS}.
\]

Because empirical CDFs lie in `[0, 1]`, both distance and score lie in `[0, 1]`.

## Input and algorithm

Use the full captured frame length \(l_i\) from each canonical event. Require at
least one finite positive integer length in each trace. Sort both samples, walk
their merged unique values, update each ECDF after consuming all ties at that
value, and retain the maximum absolute difference. This exact procedure handles
the discrete, tied nature of packet sizes; no interpolation or histogram is
used.

## Diagnostics and edge cases

Return `distance`, `reference_count`, `generated_count`, and the minimum and
maximum length of each sample. Identical multisets score `1`, even if packet
order differs. A singleton sample is valid. Empty, noninteger, or nonpositive
length samples are errors.

The classical distribution-free p-value interpretation of KS requires
additional assumptions and is not used here, especially because frame sizes are
discrete. Only the ECDF sup distance is part of the metric.

## Trafficlab-specific choices

Converting the KS distance to similarity with `1 - D`, treating it purely as a
descriptive fitness component, and using captured frame length are Trafficlab
choices.

## Computational cost

Sorting dominates at \(O(n\log n+m\log m)\) time. The merged scan is
\(O(n+m)\). A straightforward implementation stores \(O(n+m)\) values.

## Deterministic test examples

- `[100, 200]` versus `[100, 200]` has distance `0` and score `1`.
- `[100]` versus `[200]` has distance `1` and score `0`.
- `[1, 2]` versus `[1, 3]` has maximum ECDF difference `1/2` and score `1/2`.
- Reordering either sample does not change the result.

## References

- NIST/SEMATECH, [“Kolmogorov-Smirnov 2-Sample Goodness of Fit Test”](https://itl.nist.gov/div898/software/dataplot/refman1/auxillar/ks2samp.htm), *e-Handbook of Statistical Methods*.
