# Approximate Joint MMD Similarity

## Purpose

Measure a joint difference in noninitial packet timing, frame length, and
direction using a deterministic random-Fourier approximation. It is a bounded,
descriptive distance; serial dependence means Trafficlab does not compute or
publish an IID MMD p-value.

## Continuous coordinates and categorical direction

For each noninitial packet, use the continuous vector

\[
x=(\log(1+\mathrm{IAT}),\log(\mathrm{frame\_length})).
\]

Compute coordinatewise mean and population standard deviation from the
reference trace only. Standardize reference and generated values with those
reference values, replacing each scale by `max(reference_std, scale_floor)`.
`scale_floor` is a configured finite positive float. Generated observations
never affect centering or scaling.

Direction has a categorical delta kernel: outbound and inbound use separate
feature blocks. Thus direction code `0` or `1` has no numeric distance or
ordering; features from different directions occupy disjoint coordinates.

## Random Fourier map and streaming mean

For configured positive feature count `K` and nonnegative seed, a dedicated
local `Generator(PCG64(seed))` draws a frozen `K x 2` standard-normal frequency
matrix \(\omega_k\). For standardized (x), the active direction block is

\[
z(x)=\frac{1}{\sqrt K}
(\cos(\omega_1^Tx),\ldots,\cos(\omega_K^Tx),
  \sin(\omega_1^Tx),\ldots,\sin(\omega_K^Tx)).
\]

The inactive direction block is zero, giving total embedding dimension `4K`.
Every individual feature vector has norm one because each frequency pair
contributes `cos² + sin²` and the common scale is `1/sqrt(K)`.

`RandomFeatureMean` adds one active block at a time to a `4K` accumulator and
divides once by the noninitial sample count. It never creates an `N x 4K`
feature matrix or a pairwise packet-distance matrix. Per trace it costs
`O(NK)` time and `O(K)` working memory, aside from the immutable input columns
and `K x 2` frequency matrix.

## Score and diagnostics

For reference and generated mean embeddings,

\[
D_{MMD}=\frac{\lVert\bar z_R-\bar z_G\rVert_2}{2},
\qquad s_{MMD}=1-D_{MMD}.
\]

Mean embeddings are convex averages of unit-norm vectors, so their distance is
at most two and the discrepancy is in `[0, 1]`. Only roundoff above the upper
bound is clamped; a material violation is rejected.

Diagnostics retain `W`, feature count, `4K` embedding dimension, seed,
reference-only continuous mean and scales, scale floor, noninitial sample
counts, and final discrepancy. They intentionally do not store the potentially
large generated frequency matrix because seed and dimension reproduce it.

## Deterministic examples

- Identical traces under the same settings produce equal streaming embeddings
  and score `1`.
- A frozen two-frequency cosine/sine map matches direct coordinate arithmetic
  and each one-event block has unit norm.
- Changing the seed changes frequencies while preserving the `4K` feature
  shape and all input validation rules.

## References

- Gretton et al., [“A Kernel Two-Sample Test”](https://jmlr.org/papers/v13/gretton12a.html), 2012.
- Rahimi and Recht, [“Random Features for Large-Scale Kernel Machines”](https://people.eecs.berkeley.edu/~brecht/papers/07.rah.rec.nips.pdf), 2007.
