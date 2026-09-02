# Similarity Methods

Similarity methods compare two [canonical traces](../SYSTEM.md#data-flow) in
memory. The shared comparison boundary gives them already normalized and cropped
columnar `TrafficTrace` values in `[0, W]` plus the same finite positive
observation window `W`. Methods consume those owned read-only NumPy columns
directly; event-record conversion remains only an external boundary for a
generic caller. Methods do not open PCAPNG files, choose model parameters, or
hide failed preconditions behind a low score.

Each method returns:

```text
evaluate(reference, generated, observation_window, settings) -> score, diagnostics
```

The score lies in `[0, 1]`; `1` means the traces are identical under that method
and `0` is the method's maximum defined discrepancy. Diagnostics contain the
underlying distance and the sample/configuration values needed to interpret it.
Every method returns the shared `W` as `observation_window_seconds` in its
diagnostics.

Frame-size KS, IAT KS, ACF, Cramér--von Mises, and Anderson--Darling remain sample-based: they use only values derived
from packets inside `[0, W]` and do not invent samples for boundary silence.
Multiscale rate represents silence directly with empty time-bin cells.

## Aggregate fitness

For the eight mandatory similarity methods \(m\), nonnegative weights \(w_m\)
must sum to one. The aggregate used by genetic search and final comparison is

\[
S(R,G)=\sum_m w_m s_m(R,G).
\]

All eight methods always execute and retain every \(s_m\) and its diagnostics.
All method-specific preconditions and settings always apply. A zero weight
contributes exactly zero to the aggregate and does nothing else: it does not
disable execution, validation, or diagnostics. A zero-weight method failure
still fails a direct comparison or invalidates a genetic candidate. An empty
sample, invalid lag, excessive bin count, or nonfinite value is therefore an
evaluation error at any weight. An invalid mathematical candidate may receive
worst fitness at the genetic layer; the metric itself does not fabricate `0`.
The similarity result and checkpoint retain their fixed eight-method shapes for
every valid weight vector.

The existing method hand calculations, together with one-hot, mixed-weight,
and zero-weight aggregate and failure tests, are sufficient evidence for this
weight-semantics change. No duplicate metric implementation is required.

In a controlled one-factor sensitivity comparison, changing one method weight
alters only aggregate contribution and may alter candidate or family ranking.
For fixed traces and method settings, every component score and diagnostic stays
fixed, and all eight mandatory methods still execute. This separates sensitivity
to declared aggregation policy from a change in measured trace behavior.

## MVP methods

| Method | Behavior | Settings | Minimum | Cost | Limitation |
|---|---|---|---|---|---|
| [Frame-size KS][frame-size] | Frame lengths | None | One packet/trace | Sorting | Ignores order/timing |
| [IAT KS][iat] | Inter-arrival times | Quantile | Two packets/trace | Sorting | Ignores dependence |
| [Autocorrelation][acf] | IAT/size dependence | Lags, weights | More than max lag | Linear/lag | Selected lags |
| [Multiscale rate][rate] | Directional volume by scale | Widths, weights, cap | Nonempty | Linear | Time alignment |
| [Cramér--von Mises][cvm] | Pooled ECDF difference for IAT and size | IAT/size weights | Two packets/trace | Sorting | No tail emphasis |
| [Anderson--Darling][ad] | Endpoint-normalized tail ECDF difference | IAT/size weights | Two packets/trace | Sorting | Sparse tails dominate |
| [Jensen--Shannon][js] | Exact marks and shared-bin direction/IAT PMFs | Bin count, IAT/mark weights | Two packets/trace | Counting + support sort | Ignores category geometry |
| [Approximate joint MMD][mmd] | Random-Fourier timing/size/direction embedding | Feature count, seed, scale floor | Two packets/trace | Linear in packets × features | Kernel approximation sensitivity |

These eight methods, in the table's order, form the complete schema-5 genetic
fitness registry. Final-only diagnostics are a separate comparison-stage
contract: they have no genetic weight and are never represented by assigning a
zero fitness weight. A zero weight applies only to a registered fitness method,
which still executes and validates as described above.

Every similarity method needs a distinct behavior to measure, a bounded
interpretable definition, hand-checked tests, and an implementation before
receiving an architecture file.

[acf]: autocorrelation.md
[ad]: anderson_darling.md
[cvm]: cramer_von_mises.md
[js]: jensen_shannon.md
[mmd]: approximate_mmd.md
[frame-size]: frame_size_ks.md
[iat]: iat_ks.md
[rate]: multiscale_rate.md
