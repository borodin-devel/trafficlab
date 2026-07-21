# 02 Diffusion Architecture and Probability Law

The model has a continuous Gaussian diffusion for active event values and a
separate categorical count head.  Its deterministic, explicit schedule is
`beta_1 ... beta_T`, where `T = diffusion_steps`, every beta is finite and in
`(0, 1)`, `alpha_t = 1 - beta_t`, `alpha_bar_0 = 1`, and
`alpha_bar_t = product(alpha_s for s = 1 ... t)`.  The selected noise-schedule
family and all its endpoints are configuration and model-file fields.  For
each active row, the Markov forward transition and its closed form are:

For an active clean row `x_0`, the forward process is:

```text
q(x_t | x_(t-1)) = Normal(sqrt(alpha_t) * x_(t-1), beta_t * I)
x_t = sqrt(alpha_bar_t) * x_0 + sqrt(1 - alpha_bar_t) * epsilon
epsilon ~ Normal(0, I)
```

The count head `C_theta(c)` returns logits over the integer counts
`0 ... max_events_per_window`.  It is not noised, transitioned, or reverse
sampled by the continuous diffusion.  At generation it is sampled first from
its categorical distribution with the seed-derived RNG; its sampled `n`
defines the active prefix.  The model then starts value sampling with Gaussian
`x_T` only for those `n` active rows.  Inactive slots are ignored rather than
decoded as packets.

The denoiser `D_theta(x_t, n, t, c)` has token embeddings for the noisy pair,
slot position, diffusion step, sampled or training count, and context.  Its
configured stack of bidirectional self-attention blocks may attend across every
proposed slot in the current window, allowing correlated timing and marks, but cannot
attend to a later generated window.  It returns only the predicted continuous
noise matrix `epsilon_hat`.  For every active row and step, the clean estimate
is deterministic:

```text
x0_hat = (x_t - sqrt(1 - alpha_bar_t) * epsilon_hat) / sqrt(alpha_bar_t)
```

The decoder requires both coordinates of `x0_hat` to be finite, sets
`x_iat = max(0, x0_hat_iat)` (ReLU), and maps `x0_hat_length` through
`sigmoid`.  Thus the decoded clean values have non-negative normalized IAT
coordinates, with `x_iat = 0` possible, and normalized frame-length
coordinates in `[0, 1]`.

For the supported epsilon-predicting DDPM sampler, reverse sampling uses the
recorded update in decreasing step order `T ... 1`:

```text
mu_theta(x_t, t, c) = (x_t - beta_t * epsilon_hat / sqrt(1 - alpha_bar_t))
                      / sqrt(alpha_t)
beta_tilde_t = beta_t * (1 - alpha_bar_(t - 1)) / (1 - alpha_bar_t)
x_(t - 1) = mu_theta(x_t, t, c)
            + sqrt(beta_tilde_t) * z_t
z_t ~ Normal(0, I) for t > 1; z_1 = 0
```

The same seed-derived RNG supplies the count sample, terminal Gaussian noise,
and every `z_t` in this stated order.  The complete window is accepted only
after count sampling, all reverse steps, and conversion checks succeed.
`predictor` is `epsilon` and `sampler` is `ddpm`; the step subset is `all`.
`sampling_stochasticity`, retained for configuration compatibility, is exactly
`1.0`; no tempered or variance-scaled DDPM variant is supported.
An implementation may not silently substitute a different schedule, predictor,
or sampler.
