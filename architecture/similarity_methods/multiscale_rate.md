# Multiscale-Rate Similarity

## Purpose

Compare packet volume and captured-byte volume over several time resolutions.
This is a Trafficlab-defined descriptive metric, not a named statistical test.

## Time series construction

Receive normalized traces and their shared observation window \(W>0\). For each
unique, strictly increasing width \(0<h\le W\), create
\(B=\lceil W/h\rceil\) bins. Bins are left-closed and right-open, except the last
bin includes timestamp \(W\). Events outside `[0, W]` are a shared-boundary error
rather than silently ignored.

Floating-point division uses one explicit boundary convention. Before applying
either `ceil(W / h)` or `floor(t / h)`, snap the finite quotient to its nearest
integer when, and only when, their absolute difference is at most four ULPs of
the quotient. Thus the implementation uses
`ceil(snap(W / h))` bins and assigns an event to
`min(floor(snap(t / h)), B - 1)`. This makes intended decimal boundaries stable:
`W = 2.1, h = 0.3` creates seven bins, and `t = 0.3, h = 0.1` enters zero-based
bin index three. The final `min` also implements the closed right endpoint.

For each trace and width, construct separate outbound and inbound bins, then
flatten outbound cells before inbound cells. For feature
\(f\in\{packet,byte\}\):

\[
r_{h,f}=(r_{out,1},\ldots,r_{out,B},r_{in,1},\ldots,r_{in,B}),
\]

and construct \(g_{h,f}\) identically. Packet cells contain event counts; byte
cells contain sums of captured frame lengths. Every event direction must be
exactly `outbound` or `inbound`.

The columnar implementation applies the same four-ULP rule elementwise, forms
direction-offset integer bin indexes, and uses NumPy counting over those
indexes. Captured-byte cells accumulate as exact unsigned integers when their
proved bound fits; the defensive larger-bound path uses Python integers. Float
weighted sums never become authoritative byte counts.

For configured maximum direction-bin cell count \(C_{max}\), require

\[
2\sum_h B_h\le C_{max}.
\]

Before allocating vectors, also require every \(2B_h\) and the total
\(2\sum_h B_h\) to fit the platform sequence-index range. This platform limit
applies even when a larger configured cap is supplied.

The same cell layout is used independently for packet and byte vectors.

## Discrepancy and score

For two aligned nonnegative vectors \(r,g\), define normalized L1 discrepancy

\[
D(r,g)=
\frac{\sum_i|r_i-g_i|}{\sum_i r_i+\sum_i g_i}.
\]

When both denominator sums are zero, define \(D(r,g)=0\). Otherwise the
triangle inequality gives \(0\le D\le1\).

Evaluate every accepted integer cell as the exact ratio `(value, 1)` and every
finite floating-point cell through its exact binary `as_integer_ratio()` value.
Because all denominators are powers of two, rescale their numerators to the
largest denominator and accumulate both L1 sums with integer arithmetic. Convert
only the final exact numerator/denominator ratio to a float. This avoids both
overflow and per-cell binary64 rounding without adding a rational-number
framework.

Let packet/byte feature weights \(v_f\ge0\) sum to one and scale weights
\(a_h\ge0\) sum to one. The total discrepancy and score are

\[
D_{MS}=\sum_h a_h\sum_f v_fD(r_{h,f},g_{h,f}),
\qquad s_{MS}=1-D_{MS}.
\]

They lie in `[0, 1]`.

## Diagnostics and edge cases

Return `observation_window_seconds`, widths, direction-bin cell counts, feature
and scale weights, outbound and inbound packet/byte totals at every scale, every
per-scale per-feature discrepancy, scale totals, feature totals, and final
discrepancy.

Require nonempty traces with finite nondecreasing timestamps, valid directions,
positive integer frame lengths, finite \(W>0\), positive finite widths no larger
than \(W\), valid normalized weights, and a direction-bin cell count within its
cap. Empty cells are normal. Two all-zero vectors match; one all-zero and one
nonzero vector has discrepancy `1`. A trace whose last packet is before \(W\) is
represented by trailing zero bins.

The shared comparison boundary owns first-packet alignment and cropping. This
metric intentionally measures relative run shape, not absolute wall-clock start
time.

## Trafficlab-specific choices

Shared alignment and cropping, the bin endpoint rule, trailing-zero
representation, direction-separated packet and captured-byte features,
outbound-before-inbound layout, normalized L1 formula, empty-vector convention,
weights, cell cap, and `1 - D` conversion are Trafficlab definitions.

## Computational cost

For width set \(W\), \(n+m\) events, and cell cap \(C_{max}\), a
single-pass-per-scale implementation takes \(O(|W|(n+m)+C_{max})\) time. An
implementation that advances all scale indices together may approach
\(O(n+m+C_{max})\). Stored vectors require \(O(C_{max})\) space.

## Deterministic test examples

- Identical vectors have discrepancy `0` and score `1`.
- `[0, 0]` versus `[0, 0]` uses the zero-denominator convention and scores `1`.
- With vector layout `[outbound, inbound]`, reference `[1, 0]` versus generated
  `[0, 1]` gives
  \(D=(|1-0|+|0-1|)/(1+1)=1\) and score `0`.
- `[1, 1]` versus `[1, 0]` has discrepancy `1/3`.
- Mixed traces differ only in the direction-bin cells whose counts differ.
- Reversing direction remains indistinguishable only when both direction-bin
  vectors are exactly symmetric at every configured scale.
- An event exactly at \(W\) enters the last bin.
- Duplicate widths or \(2\sum_h B_h>C_{max}\) are rejected.

## References

- This bounded score is a Trafficlab-specific definition. Its inputs and common
  score contract are defined in the [similarity catalog](README.md); it does not
  claim an external named method.
