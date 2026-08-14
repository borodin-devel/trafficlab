# Genetic Operators Design

## Problem

Trafficlab defines heterogeneous selection, family-safe reproduction, repair,
duplicate retries, and deterministic checkpoint resume, but it does not define
what crossover and mutation actually do. Initialization distributions are also
implicit. The same experiment and master seed could therefore produce different
populations in different implementations.

The MVP needs one exact, bounded implementation for its three existing
chromosomes. The design must retain per-family tuning without adding per-gene
operator configuration or a general evolutionary-optimization framework.

## Operator Configuration

Each enabled family has three operator settings:

```text
crossover_probability in [0, 1]
mutation_probability  in [0, 1]
mutation_scale        in (0, 1]
```

All values must be finite. Crossover probability applies once to a same-family
child. Mutation probability applies independently to every gene. Mutation scale
is the standard deviation in that gene's normalized coordinate.

The experiment may override all three values independently for
`poisson_empirical`, `markov_renewal`, and `mmpp`. An enabled family with omitted
operator values uses these Trafficlab defaults:

\[
p_c=0.9,\qquad p_m=1/d_f,\qquad \sigma=0.1,
\]

where \(d_f\) is that family's fixed chromosome length. Thus the default expected
number of selected mutation genes is one. These are engineering defaults, not
universal genetic-algorithm constants. Unknown family or setting names are
configuration errors. Settings for a disabled family are rejected as unused
configuration.

The MVP removes any ambiguous global crossover or mutation setting. Operator
values live only in the family block that uses them.

## Gene Coordinates and Initialization

Every gene has finite configured bounds \(L<U\). Integer bounds must be integers
and contain at least two values. Logarithmic genes additionally require \(L>0\).

Continuous linear genes use

\[
z=\phi_{lin}(x)=\frac{x-L}{U-L},\qquad
x=\phi^{-1}_{lin}(z)=L+z(U-L).
\]

Positive scale/rate genes use

\[
z=\phi_{log}(x)=\frac{\log x-\log L}{\log U-\log L},\qquad
x=\phi^{-1}_{log}(z)=
\exp\bigl(\log L+z(\log U-\log L)\bigr).
\]

Integer genes use the linear coordinate and decode with

\[
x=L+\left\lfloor z(U-L)+\frac12\right\rfloor.
\]

The half case therefore rounds upward. Initialization draws every continuous or
logarithmic gene with \(z\sim Uniform(0,1)\). Integer initialization draws
uniformly from the inclusive integer set \(\{L,\ldots,U\}\). Initialization then
uses the same family repair and validation as offspring.

The fixed coordinate mapping is:

| Family | Gene | Coordinate |
|---|---|---|
| Poisson empirical | `c_lambda` | logarithmic |
| Markov Renewal | `q1`, `q2`, `alpha` | linear |
| Markov Renewal | `r` | integer |
| Markov Renewal | `c_t` | logarithmic |
| MMPP | `q01`, `q10`, `lambda0`, `lambda1` | logarithmic |

## Reflection

Mutation operates in normalized coordinates. Define reflection into `[0, 1]`
for any finite real \(v\) by

\[
r=v\bmod 2,\qquad
reflect(v)=
\begin{cases}
r,&0\le r\le1,\\
2-r,&1<r<2.
\end{cases}
\]

Here modulo returns a value in `[0, 2)`. Reflection avoids accumulating mass at a
bound, which direct clamping would do. It is a Trafficlab boundary rule.

## Same-Family Reproduction

For same-family parents \(a,b\), draw one
\(C\sim Bernoulli(p_c)\).

If \(C=1\), apply uniform crossover independently in chromosome order:

\[
y_j=
\begin{cases}
a_j,&B_j=0,\\
b_j,&B_j=1,
\end{cases}
\qquad B_j\sim Bernoulli(1/2).
\]

If \(C=0\), clone the fitter parent. A fitness tie uses the existing smallest
stable-candidate-ID rule.

After crossover or cloning, draw mutation-selection values
\(M_j\sim Bernoulli(p_m)\) independently in chromosome order. For each selected
gene, transform its current value to \(z_j\), draw

\[
\epsilon_j\sim Normal(0,\sigma^2),
\qquad z'_j=reflect(z_j+\epsilon_j),
\]

then decode it with the gene's fixed coordinate mapping. A same-family child may
select no mutation genes. Elites and family champions are copied without
crossover or mutation.

## Different-Family Reproduction

Different-family parents never cross chromosomes. Clone the fitter parent using
the same stable-ID tie rule, then apply the cloned family's mutation settings.

Draw all per-gene mutation selections normally. If none is selected, choose one
gene uniformly from the chromosome and mutate it. During any mandatory mutation,
including a duplicate retry, an integer gene that decodes unchanged moves one
integer step in the sampled Gaussian direction and reflects that step at an
endpoint. An exact zero Gaussian value uses the positive direction.

Continuous mutation is unchanged only on a probability-zero draw or reflection
symmetry. If finite-precision decoding leaves the complete repaired chromosome
identical to its surviving source parent, the ordinary duplicate-retry rule below
performs another forced mutation. This preserves the existing promise that a
cross-family reproduction cannot silently remain an unchanged clone.

## Family Repair and Validation

Repair runs after initialization, crossover, and mutation:

1. reject nonfinite genes;
2. reflect/decode selected mutations and clamp other finite values to their
   configured bounds as a defensive no-op for normal offspring;
3. round `r` with the documented integer rule;
4. apply the family ordering rule;
5. validate every bound and family invariant.

Family rules are:

- Poisson empirical validates finite positive `c_lambda`.
- Markov Renewal sorts `q1` and `q2`, then applies their named bounds and requires
  `0 < q1 < q2 < 1`. It validates `alpha >= 0`, integer `r >= 1`, and positive
  `c_t`. Reference quantiles must still produce two distinct numerical size
  thresholds; otherwise the candidate is invalid.
- MMPP leaves `q01` and `q10` in named positions, sorts `lambda0` and `lambda1`,
  then applies their named bounds and requires every rate positive and
  `lambda0 < lambda1`.

If sorting followed by named-bound enforcement produces equality or violates an
ordering invariant, repair fails. It does not add jitter or silently change the
chromosome meaning. A failed family repair is an invalid candidate with fitness
`0` and a reason.

## Duplicate Handling

A duplicate is the same family name plus exact numeric equality of the repaired
gene tuple in canonical chromosome order. There is no tolerance-based equality
or model-output comparison.

After a valid child is repaired, compare it with all survivors and accepted
children. For each of the existing configured duplicate-mutation attempts:

1. mutate the current valid duplicate with its family settings;
2. force one uniformly selected gene when normal selection chooses none;
3. repair and validate;
4. accept immediately when the result is valid and distinct;
5. retain the last valid duplicate as the base when an attempted result is
   invalid, then continue.

If the bounded attempts find no valid distinct tuple, keep the original valid
duplicate. Population size never changes and the loop cannot be unbounded.
Invalid children skip duplicate handling and retain fitness `0`.

## Deterministic Random Draw Order

All gene loops use the fixed chromosome order published above. Conditional draws
occur in this order:

1. same-family crossover decision;
2. one parent-choice draw per gene when crossover occurs;
3. one mutation-selection draw per gene;
4. one Gaussian draw per selected gene, in gene order;
5. forced-gene index draw followed by its Gaussian draw when required;
6. duplicate attempts, each repeating mutation selection, selected Gaussian
   draws, and any forced-gene draws in the same order.

Steps that do not apply consume no random draw. Family repair consumes no random
numbers. The checkpoint's existing genetic settings and RNG state therefore
fully determine the next offspring. Resume compatibility must include every
family's operator values, gene bounds, coordinate kinds, and chromosome order.

## Errors and Reliability

Invalid configuration fails preflight. A mathematical child with failed repair
or model-specific validation remains an invalid candidate with fitness `0` and a
direct reason. Duplicate exhaustion keeps a valid duplicate and is not an error.

RNG, checkpoint, filesystem, parser, or evaluator failures remain infrastructure
errors and abort fitting. Operators have no retry loop except the existing
bounded duplicate attempts.

## Tests

Focused deterministic tests cover:

- default and overridden per-family settings, probability endpoints, invalid
  bounds, log lower bounds, unknown keys, and disabled-family settings;
- linear, logarithmic, and integer encode/decode examples;
- reflection at both endpoints and across more than one interval;
- initialization distributions using a stub RNG;
- crossover probability `0` and `1`, uniform per-gene parent choice, fitter-parent
  cloning, and stable-ID fitness ties;
- mutation probability `0` and `1`, range-normalized Gaussian values, and fixed
  chromosome draw order;
- cross-family forced mutation and integer unchanged-value handling;
- Poisson, Markov Renewal, and MMPP repair, ordering, equality rejection, and
  distinct reference-quantile thresholds;
- exact duplicate identity, successful forced mutation, invalid retry, bounded
  exhaustion, and population-size preservation;
- checkpoint/resume producing exactly the same next children and RNG state as an
  uninterrupted run with nondefault per-family settings.

One small heterogeneous integration test uses all three families and nondefault
operator values. It verifies that crossover occurs only within a family, each
child uses its own family's settings, and all families remain represented.

## Documentation Scope

Implementation updates only the current owners:

- `architecture/SYSTEM.md` for per-family operator configuration;
- `architecture/genetic_models/basic_generational.md` for exact reproduction,
  mutation, duplicate, failure, checkpoint, and test contracts;
- the three traffic-model documents for coordinate mappings and family repair;
- `architecture/TESTING.md` and `architecture/ROADMAP.md` for focused unit and
  heterogeneous integration evidence.

No new genetic strategy, operator registry, plugin interface, schema version,
parallel evaluator, adaptive mutation, self-tuning chromosome, or per-gene
operator settings are added. Model families still compete in one population,
and the existing selection, elitism, family champions, fitness, observation
window, checkpoint cadence, and final-seed policy do not change.
