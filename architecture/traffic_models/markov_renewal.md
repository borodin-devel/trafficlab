# Markov Renewal Model

## Purpose

A Markov renewal process couples a discrete state transition with the time until
that transition. Trafficlab uses observable direction/size states so the model
can reproduce short-range packet order and timing through an observable,
interpretable representation.

For packet \(i\), define

\[
J_i=(d_i,b(l_i)),
\]

where \(d_i\) is direction and \(b(l_i)\) is a frame-size bin. With holding time
\(\Delta_i=t_{i+1}-t_i\), the Markov renewal kernel is

\[
Q_{jk}(u)=P(J_{i+1}=k,\Delta_i\le u\mid J_i=j)
         =p_{jk}F_{jk}(u).
\]

## Estimator

Let \(N_{jk}\) be the number of observed transitions from active state \(j\) to
active state \(k\). Let \(K\ge1\) be the number of active states and

\[
N_j=\sum_m N_{jm}
\]

be the total number of observed transitions leaving \(j\). The complete additive
estimator is

\[
p_{jk}=
\begin{cases}
\dfrac{N_{jk}+\alpha}{N_j+\alpha K},
  &N_j+\alpha K>0,\\
\dfrac{1}{K},
  &N_j=0\ \text{and}\ \alpha=0,
\end{cases}
\qquad \alpha\ge0.
\]

The denominator can be zero only in the second case. When \(N_j=0\) and
\(\alpha>0\), the first case already gives
\(\alpha/(\alpha K)=1/K\), so the second case is its continuous extension to
zero smoothing. Every row is finite, nonnegative, and sums to one. The uniform
unobserved row is a Trafficlab boundary choice, not evidence that the reference
exhibited uniform transitions.

The explicit empty-row fallback applies when an active source state has
\(N_j=0\) and \(\alpha=0\). With positive smoothing, the ordinary estimator also
produces a uniform row, so no separate rule is needed. Use of either uniform
unobserved-row case must be retained in fitted-model and evaluation diagnostics;
it weakens state-conditional interpretation but remains a declared finite-sample
rule.

Fitting constructs this row without an RNG draw and stores it as an ordinary
transition row. Loading applies the same finite, nonnegative, and row-sum
validation as for every other row. `K = 0` and malformed stored rows remain
model errors. This defined empty row is not an invalid candidate merely because
`alpha = 0`.

For an observed transition pair, its empirical holding-time CDF is

\[
\widehat F_{jk}(u)=\frac{1}{N_{jk}}
 \sum_i \mathbf 1[J_i=j,J_{i+1}=k,\Delta_i\le u].
\]

Fitting also records frame lengths observed in every destination state, the IATs
leaving each source state, and the global IAT sample.

## Chromosome

The canonical chromosome order and generic coordinate kinds are

```text
(q1: linear, q2: linear, alpha: linear, r: integer, c_t: logarithmic)
```

The genes represent:

1. two quantile levels \(0<q_1<q_2<1\);
2. smoothing \(\alpha\ge0\);
3. integer minimum conditional support \(r\ge1\);
4. finite positive timing multiplier \(c_t\).

Reference quantiles at \(q_1,q_2\) become two strictly increasing numerical size
thresholds and hence three size bins per observed direction. Gene bounds come
from the experiment and satisfy the
[generic coordinate requirements](../genetic_models/basic_generational.md#gene-coordinates-and-initialization).

For sorted observed lengths \(x_{(0)}\le\dots\le x_{(n-1)}\), Trafficlab uses the
Hyndman--Fan Type 7 sample quantile. With
\(h=(n-1)q\), \(j=\lfloor h\rfloor\), and \(g=h-j\),

\[
Q_7(q)=(1-g)x_{(j)}+g x_{(j+1)},
\]

where the upper index is capped at \(n-1\). Thresholds remain real numbers; they
are not rounded to frame lengths. For thresholds \(a<b\), the inclusive bin
comparisons are \(b(l)=0\) when \(l\le a\), \(b(l)=1\) when \(a<l\le b\), and
\(b(l)=2\) when \(l>b\).

Repair is deterministic and consumes no RNG:

1. reject nonfinite values;
2. decode and round positive `r` half upward as `floor(r + 0.5)`;
3. sort `q1` and `q2` into ascending order;
4. defensively clamp every ordered gene to its named bound;
5. validate every named bound and `0 < q1 < q2 < 1`;
6. validate `alpha >= 0`, integer `r >= 1`, and `c_t > 0`;
7. derive the two reference quantile thresholds and reject them if equal.

Repair does not add jitter to equal quantiles or equal numerical thresholds. If
named clamping destroys strict quantile order, validation fails. A reference with
too few distinct frame sizes to produce two thresholds therefore makes the
candidate invalid rather than silently changing state meaning.

Only states containing at least one observed packet are active. Smoothing may
assign probability to a transition without a direct holding-time sample; the
fallback below makes its timing defined.

## Sparse timing fallback

To sample a `j -> k` holding time:

1. use the empirical `j -> k` IAT sample when it has at least \(r\) values;
2. otherwise use all empirical IATs leaving state \(j\) when that set is not
   empty;
3. otherwise use the global empirical IAT sample.

Thus the global-IAT fallback applies only when the selected transition has
fewer than \(r\) observations and the source state has no leaving-IAT sample.
Model and evaluation diagnostics must retain which timing tier was used and how
often. Falling back from a transition-conditioned sample weakens the
state-conditional holding-time interpretation; it supplies defined
finite-sample behavior rather than evidence for that conditional law.

Multiply the sampled value by \(c_t\). Nonfinite, negative, or absent global
IATs invalidate fitting and loading. Zero IATs remain valid observations.

An empty transition row has no IATs leaving its source state. After sampling its
uniform destination, the same fallback therefore reaches the global IAT sample;
there is no separate empty-row timing rule.

## Generation

Sample the initial state from the empirical reference state frequencies and put
the first packet at relative time zero. Repeatedly:

1. sample destination state \(k\) from the current row \(p_{jk}\);
2. sample and scale a holding time using the fallback rule;
3. finish normally if the next timestamp is greater than `W`;
4. otherwise sample a frame length from reference lengths in state \(k\);
5. emit its direction and length at a timestamp at most `W`, then set \(j=k\).

If a packet-count, output-size, or wall-time reliability guard is reached before
the next-timestamp comparison establishes full-window completion, return
incomplete generation. The RNG is local to one generation call. A row must sum
to one within numerical tolerance after construction and loading.

Schema 5 preserves the schema-4 local PCG64 generator and schema-3 scalar order: `random()` for
the initial weighted state, `choice(frame_count)` for its frame, then on each
iteration `random()` for the destination row and `choice(holding_count)` for the
selected timing tier. Only an in-window next timestamp consumes the final
`choice(destination_frame_count)`. All `choice` calls are scalar and sample the
half-open index range; an omitted conditional step consumes no draw.

## Trafficlab-specific choices

Quantile-based size states, additive smoothing, the exact sparse fallback,
empirical frame emissions, timing scaling, and the chosen chromosome are
Trafficlab definitions. The kernel factorization and conditional holding-time
concept are established Markov renewal mathematics.

## Computational cost

For \(n\) packets and \(K\) active states, fitting takes \(O(n+K^2)\) time and
space including the dense transition matrix and empirical samples. Simple
cumulative row sampling generates \(m\) packets in \(O(mK)\) time and \(O(m)\)
output space.

## Deterministic test examples

- Alternating two-state input with \(\alpha=0\) yields transition probabilities
  of one for the observed alternation.
- A hand-counted transition table reproduces the stated smoothed formula.
- State sequence `[A, B]` with `alpha = 0` gives A's row `[0, 1]` and
  final-only B's row `[1/2, 1/2]`.
- A nonempty zero-smoothed row equals its empirical transition frequencies; a
  positive-smoothing empty row is uniform through the ordinary formula.
- A stub RNG enters the final-only state, samples its uniform row, and reaches
  the global IAT fallback.
- Serialization and loading preserve and validate the uniform row. Missing,
  nonfinite, or negative global IAT data remains invalid; zero IATs remain valid.
- An undersupported transition uses source-state IATs, then global IATs.
- Equal seed, fitted model, and limits yield an identical trace.
- A scripted next event at `W` is emitted and one after `W` finishes normally.
- A last packet before `W` is valid when the next sampled event is after `W`.
- A reliability guard reached while the next event is within `W` returns
  incomplete generation.
- Reversed `q1` and `q2` are sorted; equal repaired quantiles are invalid.
- Integer coordinate half cases round upward, and `r` remains within its named
  inclusive bounds.
- Named clamping after sorting must preserve strict quantile order.
- Duplicate numerical quantile thresholds invalidate the candidate.
- Distinct numerical thresholds produce exactly three size bins per direction.

## Direct scientific validation

Bounded tests with predeclared seeds, sample sizes, and tolerances must directly
cover transition probabilities, generated state occupancy, conditional holding
times, every fallback tier and its diagnostics, complete-window generation, and
joint direction/length marks. Serialization and fixed-seed reproduction are
necessary but not substitutes for this evidence.

## References

- Ronald Pyke, [“Markov Renewal Processes: Definitions and Preliminary
  Properties”][pyke], *The Annals of Mathematical Statistics* 32(4), 1231–1242,
  1961.

[pyke]: https://doi.org/10.1214/aoms/1177704863
