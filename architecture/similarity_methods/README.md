# Similarity Methods

Similarity methods compare two [canonical traces](../SYSTEM.md#data-flow) in
memory. The shared comparison boundary gives them already normalized and cropped
events in `[0, W]` plus the same finite positive observation window `W`. Methods
do not open PCAPNG files, choose model parameters, or hide failed preconditions
behind a low score.

Each method returns:

```text
evaluate(reference, generated, observation_window, settings) -> score, diagnostics
```

The score lies in `[0, 1]`; `1` means the traces are identical under that method
and `0` is the method's maximum defined discrepancy. Diagnostics contain the
underlying distance and the sample/configuration values needed to interpret it.
Every method returns the shared `W` as `observation_window_seconds` in its
diagnostics.

Frame-size KS, IAT KS, and ACF remain sample-based: they use only values derived
from packets inside `[0, W]` and do not invent samples for boundary silence.
Multiscale rate represents silence directly with empty time-bin cells.

## Aggregate fitness

For enabled methods \(m\), nonnegative weights \(w_m\) must sum to one. The
aggregate used by genetic search and final comparison is

\[
S(R,G)=\sum_m w_m s_m(R,G).
\]

The result always retains every \(s_m\) and its diagnostics. An empty sample,
invalid lag, excessive bin count, or nonfinite value is an evaluation error. An
invalid mathematical candidate may receive worst fitness at the genetic layer;
the metric itself does not fabricate `0`.

## MVP methods

| Method | Behavior measured | Configuration | Minimum input | Cost | Limitation |
|---|---|---|---|---|---|
| [Frame-size KS](frame_size_ks.md) | Marginal frame-length distribution | None | One packet per trace | Sort dominated | Ignores order and timing |
| [IAT KS](iat_ks.md) | Marginal inter-arrival distribution | Diagnostic quantile | Two packets per trace | Sort dominated | Ignores serial dependence |
| [Autocorrelation](autocorrelation.md) | IAT and size serial dependence | Lags and weights | More values than maximum lag | Linear per lag | Only selected linear lags |
| [Multiscale rate](multiscale_rate.md) | Direction-separated packet and byte volumes over time scales | Widths, weights, cell cap | Nonempty trace | Linear in events and cells | Sensitive to time alignment |

Only these implemented methods belong in the registry. New methods need a
distinct behavior to measure, a bounded interpretable definition, hand-checked
tests, and an implementation before receiving an architecture file.
