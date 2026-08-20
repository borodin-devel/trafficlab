# Autocorrelation Similarity

## Purpose

Compare selected linear serial dependence in IAT and frame-length sequences.
Unlike the two KS metrics, this method is sensitive to ordering.

## Sample autocorrelation

For \(y_1,\ldots,y_N\), mean \(\bar y\), and positive lag \(k<N\), use the NIST
sample autocorrelation

\[
\rho_y(k)=
\frac{\sum_{i=1}^{N-k}(y_i-\bar y)(y_{i+k}-\bar y)}
     {\sum_{i=1}^{N}(y_i-\bar y)^2}.
\]

This value is in `[-1, 1]`. Trafficlab defines \(\rho_y(k)=0\) when the
denominator is zero, so constant series compare deterministically.

Compute ACFs separately for the IAT sequence and the frame-length sequence. Each
configured lag must be a unique positive integer smaller than all four relevant
sequence lengths. Lag weights \(a_k\ge0\) sum to one.

For each feature and trace, the NumPy implementation validates and scales the
column once, computes the whole-series mean and shared denominator once, then
uses centered dot products for only the configured lags. This execution order
does not change the estimator or the constant-series convention.

For feature \(f\), define bounded discrepancy

\[
D_f=\sum_k a_k
\frac{|\rho_{R,f}(k)-\rho_{G,f}(k)|}{2}.
\]

With nonnegative feature weights \(v_{IAT}+v_{size}=1\), define

\[
D_{ACF}=v_{IAT}D_{IAT}+v_{size}D_{size},
\qquad s_{ACF}=1-D_{ACF}.
\]

Both final discrepancy and score lie in `[0, 1]`.

## Diagnostics and edge cases

Return configured lags and weights, reference and generated ACF values for both
features, per-lag absolute differences, feature discrepancies, and final
discrepancy. Reject nonfinite samples, invalid/duplicate lags, weights that do
not sum to one, or traces too short for the largest lag.

If both series are constant, their feature discrepancy is zero. If only one is
constant, its zero ACF is compared normally with the other ACF. Autocorrelation
does not prove independence and does not capture nonlinear dependence.

## Trafficlab-specific choices

The zero convention for constant series, division of ACF difference by two,
selected lags, lag/feature weighting, and conversion to `1 - D` are Trafficlab
definitions. The sample ACF formula is the cited established estimator.

## Computational cost

For lag set \(L\) and total reference/generated sample length \(n+m\), direct
evaluation takes \(O(|L|(n+m))\) time. Centered working arrays require
\(O(n+m)\) space and retained diagnostics require \(O(|L|)\) space.

## Deterministic test examples

- A constant sequence has ACF `0` at every positive valid lag.
- Sequence `[1, 2, 3]` has lag-one numerator `0` and therefore ACF `0` under the
  specified whole-series-mean formula.
- Identical nonconstant sequences score `1`.
- Reference ACF `-1` versus generated ACF `1` at one fully weighted lag gives
  feature discrepancy `1`.
- A lag equal to sequence length is rejected.

## References

- NIST/SEMATECH, [“Autocorrelation”](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35c.htm), *e-Handbook of Statistical Methods*.
