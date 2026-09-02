# Categorical Packet Hidden Markov Model

## Purpose

The packet HMM represents latent traffic regimes while keeping timing,
direction, and frame size in one joint categorical emission. A fitted state can
therefore favor a combination such as a short outbound gap and small frame,
rather than sharing marks across timing regimes. The model is descriptive: a
latent state is not asserted to be an application or network-protocol state.

## Reference observations and categories

The packet at normalized time zero has no preceding IAT. Trafficlab retains its
single raw `(direction, frame_length)` mark separately. Each later packet is one
HMM observation built from its raw IAT, direction, and frame length.

The fixed quantile levels are `(1/3, 2/3)`. For a nonempty sorted sample
`x_(0) <= ... <= x_(m-1)`, the Hyndman--Fan Type-7 quantile at `q` uses
`h=(m-1)q`, `j=floor(h)`, `g=h-j`, and

\[
Q_7(q)=(1-g)x_{(j)}+g x_{(j+1)}.
\]

IAT thresholds are computed only from strictly positive post-`t0` IATs. Zero
has its own category index zero. Positive IATs use indices one, two, and three,
with `x <= Q_7(1/3)`, `Q_7(1/3) < x <= Q_7(2/3)`, and values above the upper
threshold. If there are no positive IATs the threshold vector is empty and only
zero-IAT categories exist. Size thresholds use all post-`t0` frame lengths and
the same inclusive-upper tercile convention. Repeated thresholds are valid;
the resulting empty nominal bins are not materialized.

A category is `(iat_bin, direction, size_bin)`. Direction is an unordered
`outbound`/`inbound` categorical value, not a numeric distance. The vocabulary
contains only combinations observed in the reference, in first-appearance
order. Every category has a nonempty ordered reservoir of individual raw
`(iat, frame_length)` members. The fitted model stores no observation path,
packet subsequence, or whole-trace template.

## Categorical HMM and scaled inference

For `K` latent states, observed symbol `y_t`, initial law `pi`, transition
matrix `A`, and emission matrix `B`,

\[
P(z_0=j)=\pi_j,\quad P(z_t=j\mid z_{t-1}=i)=A_{ij},\quad
P(y_t=m\mid z_t=j)=B_{jm}.
\]

The chromosome is the exact integer `(state_count)` under configured inclusive
bounds inside `2..4`. Repair clamps it without randomness. The fixed
`fixed_cyclic_v1` initialization is

\[
\pi_j=\frac{j+1}{K(K+1)/2},\qquad
A_{ij}=\frac{K+1\;\mathbf 1[i=j]+\mathbf 1[i\ne j]}{2K}.
\]

For `M` observed symbols, unnormalized emission weight is `K+1` when
`m mod K = j` and one otherwise; each row is then normalized. Thus all initial
probabilities are positive and fitting is deterministic.

Forward probabilities are scaled at every time. With
`u_0(j)=pi_j B_(j,y_0)`, `c_0=sum_j u_0(j)`, and
`alpha_0(j)=u_0(j)/c_0`, later rows use

\[
u_t(j)=B_{j,y_t}\sum_i\alpha_{t-1}(i)A_{ij},\quad
c_t=\sum_j u_t(j),\quad \alpha_t(j)=u_t(j)/c_t.
\]

The log likelihood is `sum_t log(c_t)`. Backward rows divide by `c_(t+1)`;
normalized `alpha_t beta_t` gives `gamma`, and normalized adjacent pair mass
gives `xi`. Zero or nonfinite scaling mass is an error.

## Bounded Baum--Welch and labels

Baum--Welch runs at most 100 updates and declares convergence when accepted log
likelihood improvement is in `[0, 1e-8]`. Initial, transition, and emission
expected counts receive fixed additive smoothing `0.001` before normalization.
Because a smoothed MAP-style update can reduce the unsmoothed data likelihood,
Trafficlab deterministically halves the step between the current and proposed
tables until likelihood is nondecreasing within absolute tolerance `1e-10`.
The complete finite likelihood history, update count, and convergence Boolean
are persisted. Reaching the iteration cap is a valid explicitly nonconverged
fit, not a false convergence claim or an unbounded retry.

Latent labels are canonicalized after fitting. A state's first key is its
emission-weighted mean raw IAT, where each category contributes its reservoir
mean. Ties compare emission vectors, then the transition matrix under the same
permutation, then initial probabilities. Since `K <= 4`, every tied admissible
permutation is enumerated and the lexicographically least representation is
selected. Permuting input state labels therefore produces the same wire tables.

## Generation and exact random order

One call owns `numpy.random.Generator(numpy.random.PCG64(seed))`. It first calls
`choice(1)` for the separately stored empirical `t0` mark. It then calls
`random()` for the initial hidden state. For each proposed later packet it
calls `random()` for the category and `choice(category_pool_size)` for one raw
member. After each emitted in-window packet, it calls `random()` for the next
hidden-state transition and repeats category then member sampling. All
continuous cumulative draws use `[0,1)` and all integer choices use `[0,n)`;
invalid injected endpoints are errors.

The raw member supplies the IAT and frame length; its category supplies the
direction. A proposed packet exactly at `W` is emitted. A proposal after `W`
completes normally without drawing the later transition. The generator checks
wall, packet, and byte guards before decisions, after scalar draws, and before
each in-window packet. Zero IATs are valid, so packet and wall guards also bound
an all-zero process. Diagnostics count emitted packets by hidden state and
category; the separate `t0` packet is excluded from those counters.

## Payload validation, scope, and cost

The strict payload stores both threshold and quantile vectors, vocabulary,
individual reservoirs, `pi`, `A`, `B`, fixed estimator constants, convergence
record, and the empirical `t0` mark. Loading binds `state_count` to the outer
gene; rebuilds every value object; checks exact JSON scalar and matrix shapes;
recomputes Type-7 thresholds from the reservoir multiset; checks category
membership, positive normalized probability rows, nondecreasing likelihood,
and canonical labels; and rejects unknown fields. Dumping and generation repeat
runtime-model validation.

For `N` post-`t0` packets, `M` observed categories, `K <= 4` states, and at most
`I=100` updates, fitting costs `O(I N K^2 + I N K + I K M)` time. Inference
workspace is `O(N K^2)` for adjacent posteriors; fitted storage is
`O(N + K^2 + K M)`. Generating `n` packets costs `O(n(K+M))` with scalar
cumulative row scans and `O(n)` output storage. The model does not represent
payloads, protocol fields, causal semantics, or unseen category combinations.

## Direct evidence

- A two-state, three-observation likelihood and every state posterior are
  checked against enumeration of all eight hidden paths.
- Fixed initialization, smoothing positivity, nondecreasing accepted
  likelihood, the 100-update bound, explicit nonconvergence, and state-label
  permutation invariance have direct tests.
- Zero IAT, Type-7 thresholds, observed-only vocabulary, category membership,
  raw reservoirs, strict payload mutations, scalar endpoint failures, `t=W`,
  and mid-stream guards are exercised independently.
- A 40,000-observation PCG64 run with seed `104729` checks hidden-state
  occupancy and marginal emission frequencies within predeclared absolute
  tolerance `0.015` of an independently solved two-state stationary law.

## References

- Lawrence R. Rabiner, [“A Tutorial on Hidden Markov Models and Selected
  Applications in Speech Recognition”](https://doi.org/10.1109/5.18626),
  *Proceedings of the IEEE*, 1989.
- Alessandro Dainotti et al., [“Internet traffic modeling by means of Hidden
  Markov Models”](https://doi.org/10.1016/j.comnet.2008.05.004), *Computer
  Networks*, 2008.
