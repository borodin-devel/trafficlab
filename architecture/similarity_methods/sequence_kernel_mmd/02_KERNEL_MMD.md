# 02 Signature Kernel and MMD

The following finite settings are explicit configuration:

```text
iat_feature_scale > 0
frame_length_feature_scale > 0
base_kernel_bandwidth > 0
signature_maximum_degree is a positive integer
signature_degree_weight_m > 0 for m = 0, ..., signature_maximum_degree
score_mapping_scale > 0
numerical_absolute_tolerance > 0
```

The degree weights are validated from their exact decimal configuration values
and are not silently normalized. The base feature kernel is the bounded
characteristic Gaussian RBF kernel on that three-dimensional input domain:

```text
k_base(z, z') = exp(-||z - z'||^2 / (2 * base_kernel_bandwidth^2))
```

It is finite, in `(0, 1]` and therefore bounded in `[0, 1]`, and is
characteristic on this three-dimensional input domain. The signature kernel
uses the Gaussian-RBF RKHS and the explicitly defined anchored,
piecewise-linear paths. Let `Sig_m(P)` be the degree-`m` signature of path `P`
in that RKHS, with `Sig_0(P) = 1`, including for a constant anchor path. The
configured truncated signature kernel is:

```text
K_raw(P, Q) = sum_(m=0 to signature_maximum_degree)(
                signature_degree_weight_m * <Sig_m(P), Sig_m(Q)>)

K(P, Q) = K_raw(P, Q) / sqrt(K_raw(P, P) * K_raw(Q, Q))
```

The implementation must use a maintained, correct signature-kernel library
when one meets this definition. A handwritten implementation is allowed only
when no suitable library exists; its rationale and independent correctness
tests against authoritative examples must be recorded. The normalized sequence
kernel `K` is finite, symmetric, positive definite, and bounded in `[-1, 1]`
within the configured numerical tolerance. Valid negative values of `K` are
retained. The configured degree-zero term makes `K_raw(P, P)` positive,
including for a constant anchor path.

Let `A_1, ..., A_n` and `B_1, ..., B_n` be the reference and generated window
paths, where `n = ceil(H / w)`. The raw squared MMD uses the deterministic
biased empirical estimator, which is defined even when `n = 1`:

```text
MMD_squared = (sum_(i,j) K(A_i, A_j) + sum_(i,j) K(B_i, B_j)
               - 2 * sum_(i,j) K(A_i, B_j)) / n^2
raw_MMD = sqrt(MMD_squared)
```
