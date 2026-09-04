# Exponential Autoregressive Conditional Duration

## Purpose and definition

The exponential autoregressive conditional duration (ACD) family represents
serial clustering in packet interarrival times without a latent regime. For a
configured integer order `p` in `1..3`, Trafficlab uses ACD(`p`,`p`):

\[
\Delta_i=\psi_i\epsilon_i,\qquad \epsilon_i\sim\operatorname{Exp}(1),
\]

\[
\psi_i=\omega+\sum_{j=1}^{p}\alpha_j\Delta_{i-j}
              +\sum_{j=1}^{p}\beta_j\psi_{i-j}.
\]

The parameters satisfy `omega > 0`, every coefficient is nonnegative, and

\[
\sum_j\alpha_j+\sum_j\beta_j<1.
\]

The stationary mean duration is therefore

\[
\mu=\frac{\omega}{1-\sum_j\alpha_j-\sum_j\beta_j}.
\]

The chromosome contains only `(order)`. Repair rejects non-integers and clamps
an exact integer to its configured inclusive bounds within `1..3`. Direction
and frame length use one first-appearance-ordered joint empirical distribution
independent of the duration recursion.

## Deterministic likelihood fit

Fitting uses every consecutive reference duration after the conditioned event
at time zero, including exact zero IATs. Let `bar_delta` be their arithmetic
mean. Since the normalized reference ends at positive `W`, this mean is finite
and positive. Every unavailable pre-sample duration and conditional mean is
initialized to `bar_delta`.

The exponential negative log likelihood, omitting its parameter-independent
constant, is

\[
L=\sum_i\left(\log\psi_i+\frac{\Delta_i}{\psi_i}\right).
\]

Zero durations are valid terms: they contribute `log(psi_i)` and still update
later recursion. No jitter, filtering, or positive floor is applied.

Trafficlab optimizes an unconstrained vector `(u, z_1, ..., z_2p)`. Let
`m=max(0,z_1,...,z_2p)`, `q_0=exp(-m)/D`,
`q_k=exp(z_k-m)/D`, and `D=exp(-m)+sum_k exp(z_k-m)`. With the fixed numerical
stationarity margin `delta=1e-12`, it maps coordinates to

\[
c_k=(1-\delta)q_k,\qquad
s=1-\sum_k c_k,\qquad
\omega=\bar\Delta\exp(u)s,
\]

where the first `p` values of `c` are alpha and the last `p` are beta. This
stable scaled-simplex transform makes coefficients nonnegative, retains at
least the declared stationarity margin, and makes the stationary mean
`bar_delta * exp(u)`. The optimizer starts from the all-zero unconstrained
vector, so its initial stationary mean is exactly `bar_delta` and its lag
coefficients are equal.

The fixed solver is `scipy.optimize.minimize` with `L-BFGS-B`, analytic
recursion and transform gradients, `tol=1e-10`, `ftol=1e-10`, `gtol=1e-10`,
`maxls=20`, and at most 500 iterations. Fitting rejects a non-success status,
a nonfinite or wrong-width solution, a nonfinite loss, an iteration count
outside the fixed budget, transformed parameters outside the model domain, or
a reported final loss inconsistent with a direct likelihood evaluation. It
never publishes a capped nonconverged estimate.

For the hand case `omega=0.5`, `alpha=(0.2)`, `beta=(0.3)`, prehistory mean
`2`, and durations `(1,0,3)`, the conditional means are
`(1.5, 1.15, 0.845)`. The likelihood is the sum of
`log(1.5)+1/1.5`, `log(1.15)`, and `log(0.845)+3/0.845`.

## Generation and reproducibility

Generation initializes the prior `p` durations and prior `p` conditional means
to the retained initial conditional duration used by the likelihood fit. It
emits one conditioned marked event at
`t=0`. For each later event it recurses `psi`, draws exactly one scalar
`rng.exponential(1.0)`, multiplies it by `psi`, and draws a joint empirical mark
only when the resulting event lies in the closed window `[0,W]`. A zero
innovation and zero IAT are valid. An event exactly at `W` is emitted, after
which another innovation is required to establish natural completion beyond
`W`.

Schema 5 owns `numpy.random.Generator(numpy.random.PCG64(seed))`. Exact draw
order is the time-zero scalar `choice`, then one scalar unit-exponential
innovation per proposed duration, followed by one scalar `choice` only for an
in-window arrival. The packet, output-byte, and wall guards apply before draws,
after every draw, and before emission. Reaching a guard first returns an
explicit incomplete result. Invalid innovations, conditional means, durations,
absolute times, seeds, or windows fail explicitly rather than being clipped.

## Payload, cost, and validation

The strict fitted payload contains exactly `omega`, ordered `alpha`, ordered
`beta`, ordered joint `marks`, and diagnostics containing the finite positive
initial conditional duration, finite final negative log likelihood, exact
iteration count in `0..500`, and convergence result. Only `converged=true` is
admissible because estimator failure never publishes a model. The fit boundary
independently recomputes the likelihood from the returned coefficients and
initial duration before retaining the solver outcome. Coefficient vectors must have equal length
matching the repaired outer `order` gene, with order in `1..3`; all numeric
primitives are exact finite floats; marks are nonempty and unique; and the
stationarity and finite-mean constraints are rechecked after loading. Extra,
missing, coercible, nonfinite, negative, nonstationary, nonconverged,
over-budget, or order-mismatched values are rejected.

Fitting costs `O(np)` per likelihood/gradient evaluation and bounded
`O(500np)` overall; generation costs `O(mp)` for `m` proposed arrivals. Direct
scientific validation independently checks the hand recursion, stationary
mean, recovered unit-innovation mean, joint-mark frequencies, zero IATs,
endpoint completion, scalar RNG order, strict loading, nonconvergence, and all
three reliability guards under fixed seeds and declared tolerances. Distinct
order-two and order-three coefficient vectors directly validate newest-to-oldest
recursion, analytic-gradient lag indexing, and generated history updates.

## Reference

- Robert F. Engle and Jeffrey R. Russell, [“Autoregressive Conditional
  Duration: A New Model for Irregularly Spaced Transaction
  Data”](https://doi.org/10.2307/2999632), *Econometrica*, 1998.
