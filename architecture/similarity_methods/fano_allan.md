# Fano/Allan Dispersion Diagnostic

## Purpose

This final-only diagnostic compares burstiness across configured time scales.
It is deterministic, has no genetic-fitness weight, and is never called while
the genetic algorithm evaluates a candidate or trial. The final comparison
boundary alone may call it after both traces have been normalized to the shared
closed observation window.

## Curves and bins

For each strictly increasing finite width \(0<h\le W\), let
\(B=\lceil\operatorname{snap}(W/h)\rceil\), where `snap` is the shared
four-ULP rule: it replaces a quotient by its nearest integer only when the
distance is at most four ULPs. Events are assigned by the existing canonical
rule `min(floor(snap(t / h)), B - 1)`. Thus bins are left-closed, interior bins
are right-open, and an event at \(t=W\) belongs to the last bin.

The diagnostic requires \(B\ge2\) at every scale. It retains three count
curves per trace: total, outbound, and inbound packet counts. If
\(c=(c_1,\ldots,c_B)\), \(\bar c=B^{-1}\sum_i c_i\), then

\[
F(c)=\begin{cases}
\frac{B^{-1}\sum_i(c_i-\bar c)^2}{\bar c},&\bar c>0,\\
0,&\bar c=0,
\end{cases}
\]

and

\[
A(c)=\begin{cases}
\frac{(B-1)^{-1}\sum_{i=1}^{B-1}(c_{i+1}-c_i)^2}{2\bar c},&\bar c>0,\\
0,&\bar c=0.
\end{cases}
\]

The variance is population variance. The explicit zero-mean rule makes an
all-zero directional channel finite and equal between traces.

## Discrepancy, bounds, and diagnostics

For either factor \(x\in\{F,A\}\), compare reference and generated values by

\[
d(x_R,x_G)=
\begin{cases}
0,&\log(1+x_R)+\log(1+x_G)=0,\\
\frac{|\log(1+x_R)-\log(1+x_G)|}{\log(1+x_R)+\log(1+x_G)},&\text{otherwise}.
\end{cases}
\]

This is finite and in \([0,1]\). At each scale, the Fano and Allan channel
differences are the unweighted mean over total, outbound, and inbound curves.
Configured finite nonnegative Fano/Allan weights and scale weights each sum to
one. They combine those values as

\[
D=\sum_h a_h(w_Fd_{F,h}+w_Ad_{A,h}),\qquad s=1-D.
\]

Diagnostics retain `W`, widths, weights, exact raw count vectors, window
counts, every reference/generated Fano and Allan curve, scale and component
differences, and score operands. Only negligible binary64 roundoff at a
documented endpoint is clamped.

Before allocation, require at most 65,536 outbound-plus-inbound window cells
across all scales. This is a fixed final-only allocation cap, not a fitness
setting.

## Deterministic checks and references

- One packet in every total/outbound window gives Fano and Allan factors zero.
- An all-zero inbound channel gives both factors zero rather than `NaN`.
- A packet at `W` enters the final count cell.
- A width yielding one window is an error rather than a neutral score.

The factor definitions follow Allan's count-based fluctuation statistic and
the conventional Fano factor; the bin convention, aggregation, zero convention,
and cap are Trafficlab definitions.

- Allan, ["Statistics of Atomic Frequency Standards"](https://doi.org/10.1109/PROC.1966.4634), 1966.
- Fano, ["Ionization Yield of Radiations. II"](https://doi.org/10.1103/PhysRev.72.26), 1947.
