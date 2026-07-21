# 01 Observations and Transport Mathematics

For every noninitial frame `i` in recorded order, the method creates one
observation:

```text
z_i = (log1p(IAT_i in seconds), original_frame_length_i)
```

Each capture therefore requires at least two frames. The IAT component is in
seconds before applying `log1p`; a valid zero IAT consequently has transformed
value `0`.

The following finite method settings are explicit configuration:

```text
iat_feature_scale > 0
frame_length_feature_scale > 0
entropic_regularization > 0
score_mapping_scale > 0
solver_absolute_tolerance > 0
solver_relative_tolerance > 0
solver_maximum_iterations is a positive integer
```

For observations `z = (x, l)` and `z' = (x', l')`, the configured positive
feature scales define the ground cost:

```text
c(z, z') = sqrt(((x - x') / iat_feature_scale)^2
                + ((l - l') / frame_length_feature_scale)^2)
```

Each empirical cloud with `n` observations has uniform weights `1 / n`; the
two captures may have different values of `n`. The method uses the configured
positive entropic regularization `epsilon` to compute regularized transport:

```text
OT_epsilon(mu, nu) = min_pi sum_jk(pi_jk * c(z_j, z'_k))
                     + epsilon * sum_jk(pi_jk * (log(pi_jk) - 1))
```

where `pi` has the empirical weights of `mu` and `nu` as its row and column
marginals. The raw distance is the debiased Sinkhorn divergence:

```text
D = OT_epsilon(mu_reference, mu_generated)
    - 0.5 * OT_epsilon(mu_reference, mu_reference)
    - 0.5 * OT_epsilon(mu_generated, mu_generated)
```
