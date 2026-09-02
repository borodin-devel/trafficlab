# Classical Classifier Two-Sample Diagnostic

## Purpose and boundary

The classical classifier two-sample test (C2ST) asks whether a deterministic
linear classifier can distinguish fixed-duration reference windows from final
generated windows. It is a final-only diagnostic: genetic candidates and trial
checkpoints never compute or retain it. Its result describes distinguishability
of the declared representation; it is not a hypothesis-test p-value and does
not identify a traffic mechanism.

The final comparison stage calls C2ST once after both authoritative PCAPNG
traces have been normalized to the same finite positive closed window `[0, W]`.
The strict similarity configuration retains the feature version, window width,
fold count, guard size, maximum window count, L2 coefficient, iteration limit,
and tolerance.

## Frozen `window-v1` representation

For configured width \(h>0\), create

\[
B=\left\lceil\operatorname{snap}(W/h)\right\rceil
\]

nonoverlapping blocks. `snap` applies the shared four-ULP integer rule both to
the window-count quotient and to every event quotient `t / h` before taking its
floor. Blocks are left-closed and right-open except that the last block includes
`W`; decimal boundaries such as `0.3 / 0.1` therefore start the later block.
Before feature allocation, require \(B\le B_{max}\), where the configured
positive `maximum_window_count` is itself bounded by 65,536. Sorted block
indexes are grouped once and processed as contiguous slices, so extraction is
linear in packets plus windows rather than rescanning every packet per window.

Each block has these 14 coordinates in this immutable order:

1. outbound and inbound packet counts;
2. outbound and inbound byte counts;
3. frame-size mean and Type-7 q25, q50, and q75;
4. mean and Type-7 q25, q50, and q75 of strictly positive IATs formed only
   between adjacent packets inside that block;
5. zero-IAT count; and
6. activity count, defined as packet count minus zero-IAT count, equivalently
   the number of distinct timestamp groups in the block.

An empty summary and a block with no positive IAT use four zeros. IATs never
cross a block boundary, so neither a feature nor a guard can borrow temporal
information from an adjacent block.

## Guarded contiguous folds and reference transform

Split the ordered indexes into the configured number of contiguous evaluation
blocks. Remainder indexes are assigned one each to the earliest folds. For an
evaluation interval, its configured number of immediately preceding and
following indexes form the guard. Evaluation, guard, and training indexes are
pairwise disjoint; the guard belongs to neither data set. Every window is an
evaluation window exactly once, and every fold must retain at least one
training window. Reference and generated samples use the same indexes, so both
training and evaluation labels are exactly balanced.

The exact `divmod(B, fold_count)` layout, the earliest-fold remainder policy,
and every ordered evaluation/guard/training index vector are reconstructed at
artifact loading. Since each fold retains one complete partition of the `B`
indexes, require `B * fold_count <= 65,536` before any fold index tuple is
created. This fixed evidence cap prevents a maximum-window/maximum-fold
configuration from materializing quadratic diagnostic state.

For each fold and coordinate \(k\), compute the population mean and standard
deviation from reference training blocks only. A zero reference deviation is
replaced by one. Apply this one frozen transform unchanged to reference and
generated training and evaluation blocks. Generated values therefore cannot
move reference centering, scaling, folds, or guards. Diagnostics retain each
reference mean and scale together with every index partition.

## Deterministic logistic fit

Reference labels are zero and generated labels are one. With intercept \(b\),
coefficient vector \(\beta\), standardized row \(x_i\), and configured
\(\lambda>0\), minimize

\[
L(b,\beta)=\frac1n\sum_i
\left[\log(1+\exp(b+x_i^T\beta))-y_i(b+x_i^T\beta)\right]
+\frac{\lambda}{2}\lVert\beta\rVert_2^2.
\]

The intercept is not penalized. Trafficlab calls SciPy `minimize` with
`L-BFGS-B`, the analytic gradient, an all-zero initial vector, configured fixed
iteration/tolerance limits, and no random state. A non-success status,
nonfinite or wrong-width parameter vector, nonfinite final loss, or iteration
count outside the configured limit aborts comparison. Fold diagnostics retain
the intercept, ordered standardized coefficients, iteration count, loss, and
explicit `converged = true`. The top-level intercept and coefficients are the
arithmetic means of the retained fold values.

## Out-of-fold measurements and score

Pool each window's one out-of-fold generated-class probability. A probability
at least `0.5` predicts generated. Balanced accuracy is the mean of reference
specificity and generated sensitivity. AUC is the Mann--Whitney probability
that a generated score exceeds a reference score, with half credit for exact
ties. Thus all tied predictions have AUC `0.5`, independent of input ordering.

The similarity score is

\[
s=1-2\lvert\operatorname{AUC}-0.5\rvert.
\]

Only binary64 roundoff within \(10^{-15}\) of an endpoint is clamped. AUC
`0.5` maps to similarity one and perfect separability in either orientation
maps to zero. Diagnostics retain pooled AUC and balanced accuracy, exact sample
counts, settings, feature order, fold evidence, solver identity, and
coefficients.

## Why C2ST is not genetic fitness

Blocked cross-validation and repeated optimization are materially more costly
than the eight direct fitness methods. More importantly, using a fitted
classifier during selection would make the genetic objective depend on fold
support and classifier estimation noise, encouraging candidates to exploit the
declared representation. Keeping C2ST at the final boundary provides an
independent structural diagnostic and prevents trial/checkpoint payloads from
acquiring a second post-fit publication path.

## Deterministic checks and references

- Hand-counted blocks cover frame quantiles, local positive/zero IATs, activity,
  interior boundaries, and the closed `W` endpoint.
- Fold oracles prove contiguous evaluation blocks, exact guards, complete
  out-of-fold coverage, balanced labels, and no adjacent training leakage.
- A scalar loss/gradient oracle is independent of the optimizer; tie-aware AUC
  is checked against hand-counted positive/negative pairs.
- Identical window matrices give AUC `0.5`; direction/size-separated matrices
  give AUC `1`.

The classifier two-sample interpretation follows Lopez-Paz and Oquab; the
blocked folds, feature representation, reference-only transform, solver policy,
and similarity mapping are Trafficlab definitions.

- Lopez-Paz and Oquab, ["Revisiting Classifier Two-Sample Tests"](https://arxiv.org/abs/1610.06545), 2017.
- Fawcett, ["An Introduction to ROC Analysis"](https://doi.org/10.1016/j.patrec.2005.10.010), 2006.
