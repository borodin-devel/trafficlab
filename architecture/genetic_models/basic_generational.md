# Basic Generational Genetic Search

## Purpose

The basic strategy lets different traffic-model families compete under one
similarity fitness while preserving family-specific chromosomes. It is bounded,
deterministic for a master seed, and checkpointed after every generation.
One seeded family priority, defined below, prevents configuration or registry
input order and lexical family names from privileging a family during search.
Lexical output order is presentation-only for history and reports.

## Fitness

For the four mandatory similarity method scores \(s_m\in[0,1]\) and configured
weights \(w_m\ge0\) with \(\sum_mw_m=1\), candidate fitness is

\[
S=\sum_m w_m s_m.
\]

Each candidate is fitted to the same normalized reference and evaluated over the
same complete `W`, list of trial seeds, and reliability guards. Its fitness is
the arithmetic mean of its per-seed aggregate scores. Component scores and
invalid-candidate diagnostics remain in history. All four methods execute and
validate at every weight; a zero weight contributes exactly zero to the
aggregate but a failure of that method still invalidates the candidate.

## Population contract

An individual contains a stable candidate ID, model-family name, family-specific
genes, fitness state, and component diagnostics. Population size \(P\) is fixed.
The initial population allocates an equal base quota to every enabled family;
any remainder is assigned in `family_priority` order. Family initializers draw
within configured bounds using the search RNG and the coordinate rules below.
Initial candidate slots contain each family quota contiguously in
`family_priority` order. A stable candidate ID is the integer pair
`(birth_generation, birth_index)`, serialized as two integers. Within one family,
IDs compare lexicographically for the tie rule; they never resolve a cross-family
tie. Initial candidates and later children receive `birth_index` in creation
order; copied elites and champions retain their original IDs.

The population must satisfy

\[
P\ge E+F,
\]

where \(E\) is the configured global elite count and \(F\) is the number of
enabled families. This reserves room for global elites and one champion from
each family. If a family champion is already a global elite, it occupies one
slot rather than being duplicated.

Each next population is ordered as global elites by the competition tie rule,
missing family champions in `family_priority` order, then children in creation
order. All selection indexes refer to this order.

## Gene coordinates and initialization

Every registered `ModelFamily` declares one coordinate kind in canonical gene
order alongside its `gene_names`. The supported kinds are `linear`, `log`, and
`integer`; generic coordinate construction zips those declarations with the
family's exact configured bounds. The current normative registry remains the
three complete families (`poisson_empirical`, `markov_renewal`, and `mmpp`),
and the scientific artifact schema remains 4.

Every continuous gene has finite bounds `L < U`. A logarithmic gene also
requires `L > 0`. An integer gene has integer bounds `L < U`. Continuous linear
genes use

\[
z = \frac{x-L}{U-L}, \qquad x = L+z(U-L).
\]

Positive logarithmic genes use

\[
z = \frac{\log x-\log L}{\log U-\log L}, \qquad
x = \exp\!\left(\log L+z(\log U-\log L)\right).
\]

Integer genes use the linear coordinate and decode with

\[
x = L+\left\lfloor z(U-L)+\frac{1}{2}\right\rfloor.
\]

Thus an exact half rounds upward. Initialization draws `z ~ Uniform(0, 1)` for
each continuous linear or logarithmic gene. It draws each integer gene uniformly
from the inclusive set `{L, ..., U}`. It then applies the same deterministic
family repair and validation used for offspring.

Before any search draw, Trafficlab derives one neutral priority from the sorted
enabled family names exactly as follows:

```python
priority_rng = numpy.random.Generator(numpy.random.PCG64(master_seed))
family_priority = tuple(str(name) for name in priority_rng.permutation(sorted_family_names_array))
```

`sorted_family_names_array` makes the result invariant to configuration and
registry input order. Converting each NumPy string scalar to `str` gives the
runtime tuple used by family validation and checkpointing. The temporary
`priority_rng` is then discarded. The dedicated
search RNG is initialized separately as
`rng = numpy.random.Generator(numpy.random.PCG64(master_seed))`, so
priority sampling consumes none of the existing search draw stream.

All genetic search randomness comes from that dedicated search RNG. Its
checkpoint engine identifier is `numpy.random.Generator/PCG64`, and its bit
generator name is `PCG64`. Trafficlab implements its scalar primitives exactly:

- continuous initialization: `rng.random()`;
- Bernoulli probability `p`: one `rng.random()` call and the test `u < p`;
- inclusive integer initialization: `rng.integers(L, U, endpoint=True)`;
- a uniform chromosome index or tournament index:
  `rng.integers(0, d, endpoint=False)`;
- Gaussian mutation: `rng.normal(loc=0.0, scale=sigma)`.

Uniform parent choice is the Bernoulli primitive with `p = 0.5`. The strategy
does not use module-global randomness or `default_rng`. Every call above is a
scalar draw with shape `()`; the family permutation is the sole one-dimensional
array result and preserves the sorted input's exact cardinality.

Mutation reflects, rather than clamps, a finite normalized value back into its
bounds. For `v`, let `r = v mod 2`, where modulo returns `r` in `[0, 2)`, and
define

\[
reflect(v)=
\begin{cases}
r, & 0\le r\le1,\\
2-r, & 1<r<2.
\end{cases}
\]

For example, `reflect(-0.2) = 0.2`, `reflect(1.2) = 0.8`, and
`reflect(2.2) = 0.2`. Defensive family repair may clamp finite externally
supplied or unselected values, but ordinary selected mutation uses reflection.

## Algorithm

```text
initialize family quotas with deterministic master seed
evaluate every candidate on the same trial seeds
checkpoint generation zero
repeat for reproduced/evaluated generations 1 through G:
    retain global elites and the best candidate from each family
    fill remaining slots by tournament selection
    if selected parents share a family:
        apply same-family reproduction
    otherwise:
        apply different-family reproduction
    repair and validate the child without redrawing it
    assign a stable ID and apply bounded duplicate handling
    evaluate all new candidates on the common trial seeds
    atomically checkpoint population, RNG state, generation, and history
reevaluate the global winner with run.final_seed as a fresh simulation seed
publish the winning fitted model and complete history
```

A generation count `G` therefore means evaluated generation zero followed by at
most `G` reproduced/evaluated generations. Selection uses exactly the configured
trial seeds and `generation.trial` limits. Final validation uses exactly the
distinct `run.final_seed` as a fresh simulation seed on the training reference
and the same trial limits; it is not held-out evidence and never reselects a
candidate.

Tournament selection samples `k` individuals uniformly with replacement and
chooses the highest fitness. An exact tie between candidates from different
families uses the earlier family in `family_priority`; a tie within one family
uses the lexicographically smaller stable candidate ID. This competition rule
also ranks global elites, chooses fitter parents and the overall winner, and
handles symmetric invalid candidates with fitness `0`. Tournament size is an
integer in `[2, P]`. For each open child slot in ascending order, two parent
tournaments run in parent A, then parent B order; each scalar sample uses
`rng.integers(0, P, endpoint=False)`.

## Reproduction

Let the child's family have crossover probability `p_c`, per-gene mutation
probability `p_m`, normalized mutation scale `sigma`, and chromosome length `d`.
Genes are always visited in the family's published chromosome order.

For parents from the same family, draw `C ~ Bernoulli(p_c)`. When `C = 1`, use
uniform gene-wise crossover: for every gene `j`, draw
`B_j ~ Bernoulli(1/2)` and copy parent A's gene when `B_j = 0`, otherwise parent
B's gene. When `C = 0`, clone the fitter parent; an exact fitness tie uses the
competition tie rule.

After crossover or cloning, draw `M_j ~ Bernoulli(p_m)` for every gene in
chromosome order. For each selected gene, also in chromosome order, encode its
current value as `z_j`, draw `epsilon_j ~ Normal(0, sigma^2)`, and set

\[
z'_j = reflect(z_j+epsilon_j), \qquad child_j=decode_j(z'_j).
\]

A same-family child may select zero genes for mutation. Elites and family
champions are copied unchanged and consume no crossover or mutation draw.

Parents from different families never cross genes. Clone the fitter parent by
the competition tie rule, then use the cloned family's `p_m`, `sigma`, bounds,
and coordinate kinds. Draw all `M_j` normally. If zero genes are selected, draw
one gene index uniformly and mutate that gene, making mutation mandatory.

If a mandatory integer mutation decodes to its starting integer, move it one
integer step in the sign of `epsilon_j`; exact zero uses the positive direction.
A step above `U` reflects to `U - 1`, and a step below `L` reflects to `L + 1`.
Every selected or forced integer mutation in cross-family reproduction or a
duplicate retry is mandatory under this rule; ordinary same-family mutation is
allowed to decode unchanged.
If finite-precision decoding and repair leave the complete chromosome identical
to its cloned source parent, duplicate handling performs the next forced
mutation even when that parent is not a survivor.

## Repair and duplicate handling

Family repair consumes no RNG. It rejects nonfinite values and accepts selected
mutations only after the operator has reflected and decoded them. It then rounds
integer genes, applies the documented family ordering rule, clamps every finite
ordered value to its named configured bound, and validates every bound and
family invariant. A valid repair returns the canonical ordered gene tuple. A
failed repair leaves the individual in the population as an invalid candidate
with fitness `0` and a reason; it is not silently redrawn.

A population duplicate has the same family name and exact numeric equality of
its repaired gene tuple with a survivor or accepted child. A repaired
cross-family child also requires retry when it exactly equals its cloned source,
regardless of whether that source survived. Equality has no tolerance and does
not compare generated output. Invalid children skip duplicate handling.

For each configured duplicate-mutation attempt, mutate the current valid
duplicate with its family settings. If normal mutation selects zero genes, draw
one uniform gene index and mutate it. Repair and validate the result, accepting
it immediately when it is valid and distinct from every survivor, accepted
child, and, for cross-family reproduction, the cloned source. A valid but still
duplicate result becomes the base for the next attempt; an invalid result leaves
the last valid base unchanged. If all bounded attempts fail, retain the original
valid child and record duplicate exhaustion in its diagnostics. A configured
attempt count of zero immediately takes this exhaustion path. Population size
therefore remains fixed and retries cannot loop forever. Mandatory mutation
promises a bounded attempted change, not a guaranteed distinct final chromosome.

## Deterministic random draws

Conditional random draws occur in this exact order:

1. the same-family crossover decision;
2. one parent-choice draw per gene when crossover occurs;
3. one mutation-selection draw per gene;
4. one Gaussian draw per selected gene, in chromosome order;
5. a forced-gene index draw and its Gaussian draw when required;
6. duplicate attempts, each repeating steps 3--5.

Steps that do not apply consume no random draw. Repair consumes no random draw.
Every listed Bernoulli decision is requested even when its probability is `0`
or `1`; only a branch that does not apply omits its later draws. This fixed order
and the saved RNG state make offspring reproducible across checkpoint resume.

## Invalid candidates and failures

A family-level gene, fit, generation, incomplete-generation guard, or metric
precondition failure gives the candidate fitness `0` and a reason. Nonfinite
component scores are invalid. Docker, filesystem, PCAP parser, checkpoint, or
evaluator infrastructure errors abort the search; they are not evidence that a
mathematical candidate is poor.

An invalid chromosome demonstrates only infeasibility under its declared genes,
settings, and reliability limits. It is not evidence of poor fit by a valid
chromosome or inferiority of the model family.

## Checkpoint and resume

`checkpoint.json` contains the bumped global scientific artifact schema version,
effective experiment hash, generation number, complete population and
diagnostics, common trial seeds, family registry names, `family_priority`,
resolved family operator values, gene bounds, coordinate kinds, chromosome
order, remaining genetic settings, and history through that generation. It also
stores the exact Python version, search RNG engine and bit-generator names, and
the exact JSON-compatible `rng.bit_generator.state`: `bit_generator`, unsigned
128-bit `state` and `inc`, `has_uint32`, and `uinteger`. The discarded priority
RNG has no saved state. The file is written atomically only after the entire generation is
evaluated. Checkpoint publication completes before derived `ga_history.csv`
repair or publication; family rows are lexical for presentation, followed by
one overall row.

With `resume = true`, an absent checkpoint starts a fresh search and a present
checkpoint must be compatible before resuming. With `resume = false`, a present
checkpoint is rejected rather than overwritten.

Resume requires exact agreement on the bumped scientific artifact schema version,
experiment hash, enabled families, derived and stored
`family_priority`, bounds, coordinate kinds, chromosome order, operator values,
Python version, search RNG engine, fitness methods and weights, population size,
and trial seeds. Trafficlab checks every compatibility field before another
random draw or child, then creates an explicit PCG64 generator and assigns the
validated bit-generator state before reproduction. An uninterrupted run and a resumed run
must produce the same priority, children, history, winner, and search RNG state.
This guarantee applies to the same locked Trafficlab and Python runtime, not to
arbitrary RNG implementations.

## Termination and final evaluation

The hard generation count always terminates the algorithm. Optional early stop
counts a best-fitness improvement `<= early_stopping_tolerance` as stagnation
and resets only on an improvement `> early_stopping_tolerance`; setting
`early_stopping_generations = 0` disables it. It cannot extend the hard count.
Candidate evaluation also has packet-count, output-size, and wall-time
reliability guards. They do not shorten `W`.

The overall winner is regenerated and scored with exactly `run.final_seed` as a
fresh simulation seed on the same training reference. It was not used for
selection and uses the same `W` as selection. This score is validation evidence,
not held-out evidence, and does not reopen selection. An incomplete final
generation is a stage error, not a candidate score, and does not publish final
output.

The result depends on the finite population, generation count, gene bounds,
operators, and configured seeds. Similarity scores are not likelihoods and do
not identify a causal traffic mechanism, establish universal model-family
superiority, or demonstrate generalization to unseen programs. A fresh
simulation seed does not remove these inference limits.

## Trafficlab-specific choices

Seeded family priority, family quotas, one champion per family, per-family
operator values, uniform gene-wise crossover, transformed Gaussian mutation,
normalized reflection, fixed draw order, different-family parent handling,
mandatory mutation after such handling, exact duplicate identity and retry
limits, common trial seeds, within-family stable-ID ties, invalid-candidate
score, and checkpoint format are Trafficlab engineering definitions. Tournament
selection itself is established GA practice.

## Computational cost

For \(G\) generations, population \(P\), trial seeds \(S\), and one candidate
fit/generation/evaluation cost \(C\), the dominant work is \(O(GPSC)\). Memory is
\(O(P+H)\) excluding fitted empirical samples, where \(H\) is retained history.
For chromosome length \(d\), reproduction is \(O(d)\) per child, or
`O((A + 1)d)` including the configured bound of \(A\) duplicate attempts.

## Deterministic test examples

- Reordering configuration or registry family inputs leaves `family_priority`,
  quotas, initial slots, children, and results unchanged.
- A predeclared seed set places every family in every priority position across
  its cases; a fixed master seed reproduces its priority, initial quotas, and
  genes.
- One champion from Poisson empirical, Markov Renewal, and MMPP survives a
  generation even when none is a global elite.
- `p_c = 0` clones the fitter same-family parent; `p_c = 1` runs one uniform
  parent choice for every gene.
- `p_m = 0` selects no ordinary mutations; `p_m = 1` selects every gene.
- Reflection gives `0.2`, `0.8`, and `0.2` for inputs `-0.2`, `1.2`, and `2.2`.
- Different-family parents never cross genes and force a mutation when ordinary
  selection chooses none.
- Duplicate attempts stop at their configured bound and retain population size.
- Equal-fitness cross-family candidates and symmetric invalid candidates use
  `family_priority`; equal-fitness candidates within one family use stable IDs.
- Every family receives equal trial seeds, windows, and reliability budgets,
  and a controlled candidate with a known higher score wins regardless of
  configuration or registry input order.
- Resume from generation `g` produces exactly the same `family_priority`,
  children, history, winner, and search RNG state as an uninterrupted run.
- A mathematical candidate error scores zero; a checkpoint write error aborts.

## References

- Brad L. Miller and David E. Goldberg, [“Genetic Algorithms, Tournament
  Selection, and the Effects of Noise”][miller-goldberg], *Complex Systems* 9,
  193–212, 1995.

[miller-goldberg]: https://wpmedia.wolfram.com/sites/13/2018/02/09-3-2.pdf
