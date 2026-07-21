# 03 Fitting, Validation, and Candidate Hyperparameters

The common owner supplies deterministic training and validation windows.  For
each training example, fitting derives `x_0` and `n`, samples one diffusion
step and its continuous Gaussian noise with the candidate seed, and minimizes
the masked deterministic mean:

```text
loss = lambda_continuous * mean_active(||epsilon - epsilon_hat||^2)
     + lambda_count * categorical_cross_entropy(C_theta(c), n)
     + configured_regularization
```

`mean_active` is undefined for an empty window; empty windows contribute only
the separately trained count-head loss.  Validation uses the same deterministic
continuous-noise draws and loss without optimizer updates.  It reports
continuous loss, count loss, total loss, active-event count, window count,
generated-count diagnostics, and any
configured fixed-seed sampling diagnostics.  It does not claim a normalized
likelihood when the selected diffusion objective is not one.

Each candidate initializes weights from its recorded candidate seed, uses a
deterministic window and batch order, and canonically serializes every eligible
checkpoint.  The selected checkpoint has the lowest finite validation total
loss; exact ties select the earlier epoch.  The stopping policy explicitly
sets `max_epochs`, `min_epochs`, validation interval, and non-negative
early-stopping patience.  Fitting stops at the first applicable condition:
maximum epochs, exhausted patience after `min_epochs`, or numerical failure.

A candidate fails and is ineligible for genetic selection if preparation is
invalid; either assigned split is unusable; a loss, gradient, parameter,
schedule, or validation metric is non-finite; count/mask validation fails;
the selected checkpoint cannot be serialized and hashed; or its required
lineage cannot be recorded.  It must not repair such failures by clipping
inputs, replacing noise, skipping examples, or publishing partial artifacts.

## Candidate Hyperparameters

Only these explicit high-level values are eligible for genetic variation:

- `window_width_seconds`, `max_events_per_window`, trailing-window policy, and
  `generated_history_events`;
- denoiser hidden size, attention-layer count, attention-head count, feed-
  forward size, dropout policy, and context/seed embedding sizes;
- `diffusion_steps`, continuous noise-schedule family and endpoints, the
  epsilon predictor, DDPM sampler, and stochasticity parameter;
- `learning_rate`, optimizer family, and its explicit controls, including
  weight decay, beta or momentum values, epsilon, and gradient-norm limit;
- `max_epochs`, `min_epochs`, validation interval, early-stopping patience,
  `batch_size`, regularization, loss weights, and validation policy values
  explicitly named by this model schema; and
- generation seed, window count or duration stop value, and any explicit
  sampler-generation controls.

The run-wide validation fraction and split policy are fixed inputs from the
authoritative genetic-training schema. They remain identical across candidates
and are never model parameters or genetic search fields.

External labels, addresses, payloads, protocol fields, packet bytes, flow or
session identifiers, direction, application data, and live capture metadata
are not conditions and are never candidate hyperparameters.  Learned neural
weights are fitted artifacts, not genetic parameters: genetic operations must
not mutate, crossover, replace, or otherwise search individual weights.
