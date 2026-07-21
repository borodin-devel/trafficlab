# 02 Fitting and Candidate Hyperparameters

The common owner selects whole-file validation or an anchored chronological
suffix before preparing independent deterministic non-overlapping windows.
This owner fits a candidate's
weights only on its assigned training windows. Teacher forcing evaluates each
next event from its strictly earlier, finite history. The per-event loss is:

```text
negative_log_likelihood = -log p_time(observed_iat | history)
                          -log p_mark(observed_length | observed_iat, history)
```

The training objective is the deterministic mean of valid training-event
losses plus configured regularization. Validation uses the same likelihood,
without optimizer updates, over only assigned validation windows. It reports
mean time loss, mean mark loss, total loss, valid-event counts, and
perplexity-equivalent likelihood diagnostics where numerically defined.

Each candidate initializes learned weights from the recorded candidate seed,
uses the configured optimizer and deterministic batch order, and serializes
the selected checkpoint canonically. The selected checkpoint is the lowest
validation total loss; exact ties select the earlier epoch. The stopping policy
has explicit `max_epochs`, `min_epochs`, validation interval, and non-negative
early-stopping patience. It stops on the first applicable condition: reached
maximum epochs, exhausted configured patience after `min_epochs`, or a
documented numerical failure. A candidate that cannot produce a finite
training and validation loss, has no usable training or validation event,
produces non-finite weights or gradients, or cannot serialize and hash its
selected checkpoint fails and is ineligible for genetic selection.

## Candidate Hyperparameters

Only the following high-level, explicit configuration values are eligible for
genetic variation:

- `hidden_size`
- `attention_layers`
- `attention_heads`
- `history_length`
- `architecture.time_mixture_components` and
  `architecture.mark_mixture_components`
- `fitting.learning_rate`
- `optimizer.family` and its explicit controls
- `fitting.max_epochs`, `fitting.min_epochs`,
  `fitting.early_stopping_patience`, and
  `fitting.validation_interval_epochs`
- `fitting.batch_size` and `fitting.window_width`

The source mode, run-wide validation fraction and split policy, event domain,
file-boundary rule, seed policy, conditional law family,
continuous-to-integer conversion, stop configuration, and model schema version
are fixed for one genetic-training run. The authoritative genetic-training
schema supplies the effective split fields; genetic training applies them
identically to each in-memory candidate before window preparation. They are not
search-space fields. Learned neural weights are fitted artifacts, never genetic
parameters: genetic operations must not mutate, crossover, replace, or
otherwise search individual learned weights.
