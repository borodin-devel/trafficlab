# Basic Generational Genetic Model Design

**Date:** 2026-07-20

**Status:** Approved design

## Goal

Add one basic, selectable genetic strategy that evolves heterogeneous
traffic-model candidates through full generations. The strategy provides
fitness-based selection, elitism, same-model crossover, parameter mutation,
traffic-model replacement, deterministic population formation, and three
configurable stopping conditions.

## Scope

The selectable strategy name is `basic_generational`. This design adds or
updates:

- `architecture/genetic_models/basic_generational/README.md` as the strategy
  owner;
- `configs/30_genetic_training/genetic_strategy.toml` as the versioned
  commented template; and
- `docs/configs/30_genetic_training.md` as the exact configuration reference.

The existing genetic-model registry and 30 genetic-training owner already
route behavior to the selected strategy. They remain unchanged, as required by
the immutability rule for original architecture documents. No amendment is
needed because the new strategy fits their existing extension points.

This design does not implement the strategy, add a genetic-algorithm library,
change a traffic-model equation or model-file schema, define a similarity
formula, generate traffic, or evaluate a PCAPNG file.

## Ownership

The `basic_generational` owner defines the algorithm: candidate ordering,
initial population, selection, elitism, crossover, mutation, replacement, next
population formation, stopping behavior, determinism, and strategy validation.

The configuration reference owns TOML field names, types, defaults, accepted
values, and command-line overrides. The versioned template demonstrates the
shape without claiming that one reference path or search range suits every run.

[30 genetic training](../../../architecture/apps/30_genetic_training/README.md)
owns file and subprocess orchestration. A selected traffic-model owner defines
which parameters are trainable and validates every complete candidate model
file. A selected similarity-method owner defines its primary score range,
direction, and meaning. The genetic strategy never redefines either model
mathematics or similarity mathematics.

## Candidate and Fitness

A candidate remains one allowed traffic-model name with that model's current
parameter values. For each candidate, 30 follows its existing orchestration:
it obtains the model's normal file from 40 and applies any run-specific
preparation required by that model's owner, such as the
[`poisson_empirical`](../../../architecture/traffic_models/poisson_empirical/README.md)
reference size table. The resulting prepared values are that model's run
baseline. Non-trainable settings, including the prepared empirical table,
traffic-generation seed, and stopping controls, are never crossed over or
locally mutated within one model. Model replacement deliberately adopts the
replacement model's complete prepared non-trainable baseline; it does not
translate those settings from the old model.

A candidate becomes eligible only after traffic generation and similarity
evaluation succeed and the resulting primary score validates. The strategy
compares scores using the selected similarity method's declared direction; it
does not assume every score is normalized or higher-is-better and does not
renormalize scores.

One candidate is strictly better than another only when its primary score is
better under that direction. When scores tie, the lower stable candidate ID
wins deterministic elite ordering, reports, and final-winner choice, but an ID
tie-break does not count as a fitness improvement for stagnation stopping.

## Configuration Shape

The combined required `.configs/genetic_strategy.toml` uses this shape:

```toml
schema_version = 1
reference = "/path/to/reference.pcapng"
allowed_traffic_models = ["poisson_uniform", "poisson_empirical"]
similarity_method = "l2_ks_weighted"

[strategy]
name = "basic_generational"
strategy_seed = 0
population_size = 10
elite_count = 1
tournament_size = 3
crossover_probability = 0.8
parameter_mutation_probability = 0.2
model_replacement_probability = 0.05
offspring_attempt_limit = 100

[strategy.stopping]
maximum_generations = 50
target_score = 0.95
no_improvement_generations = 10

[strategy.search.poisson_uniform.arrival_rate_pps]
minimum = 1.0
maximum = 100.0
mutation_step = 5.0

[strategy.search.poisson_empirical.arrival_rate_pps]
minimum = 1.0
maximum = 100.0
mutation_step = 5.0
```

The path, numeric search values, `target_score`, and
`no_improvement_generations` shown above are examples. The last two settings
are disabled when omitted. The scalar values directly under `[strategy]` and
`maximum_generations = 50` are built-in defaults; an effective positive
`maximum_generations` always exists.

The top-level schema version, reference path, nonempty ordered list of unique
allowed model names, selected similarity method, and selected strategy name are
required. The output directory remains the required application argument owned
by 30 genetic training.

## Command-Line Overrides

In addition to `--config-file` and `--output-dir`, 30 genetic training accepts a
repeatable override:

```text
--set DOTTED.PATH=TOML_VALUE
```

For example:

```text
--set strategy.population_size=20
--set strategy.search.poisson_uniform.arrival_rate_pps.maximum=200.0
```

Overrides are applied after the selected file and before full validation, in
accordance with the shared configuration precedence. Each path must identify
one documented leaf value; replacing a TOML table is not supported. A TOML
array is a leaf value and is replaced atomically rather than merged. Values are
parsed as TOML data, never evaluated as shell or Python code. Unknown paths,
unknown keys, duplicate overrides of the same path, malformed TOML values,
type mismatches, and values that make the complete configuration invalid fail
before training. Because only leaves are accepted, ancestor/descendant
override collisions cannot occur.

The versioned template keeps project-specific required values and search ranges
commented where necessary so copying the template does not silently select an
unintended capture or optimization domain.

## Search-Space Entries

Search entries are nested below
`strategy.search.<model-name>.<model-parameter-path>`. The parameter path is the
path in that model's TOML file. For example,
`packet_size.minimum_bytes` is represented by nested TOML tables below the
model name.

Only model-declared trainable parameters may have search entries. An omitted
trainable parameter remains at its prepared run-baseline value, normally the
model's normal value, and is not changed by initialization or mutation in this
run.

Numeric parameters provide inclusive `minimum` and `maximum` values plus a
positive `mutation_step`. Integer parameters use integer bounds and step;
floating parameters use finite floating values. A future choice-valued
trainable parameter provides at least two distinct `values` instead. The
parameter kind comes from the traffic-model schema and is not duplicated in
the strategy configuration.

Every entry must be type-compatible with its model schema, contain its prepared
run-baseline parameter value, and describe at least two possible values. When
parameter mutation probability is positive, every allowed model must have at
least one configured mutable parameter.

All complete candidate model files still pass the traffic model's own
validation. Search bounds do not override model validation or authorize
clamping, repair, or omission of an invalid value.

## Randomness and Canonical Order

All selection, crossover, mutation, replacement, and randomized initialization
draws use one deterministic pseudo-random stream initialized from
`strategy_seed`, whose normal value is `0`.

Random draws occur in canonical generation number, population-slot number,
offspring-attempt number, and operator order. 30 waits for a generation's
candidate evaluations and restores stable candidate-ID order before checking
stopping or drawing the next population. Subprocess completion order therefore
cannot alter later genetic decisions.

Before a random operation, candidate collections are in ascending stable-ID
order, model collections retain `allowed_traffic_models` order, choice values
retain configuration order, and parameter collections are in ascending dotted
parameter-path order. Mapping iteration order never controls random draws.
Retained run diagnostics identify the strategy implementation and
random-generator name and version needed to reproduce the stream.

Candidate IDs encode stable generation and slot identity. ID ordering is the
numeric tuple `(generation, slot)`, independent of its textual serialization.
All parents, variation decisions, attempt counts, selected model names, and
artifact hashes are recorded so a run can be audited. Traffic-model generation
seeds remain non-trainable model controls and are not replaced by the strategy
seed.

## Initial Population

`population_size` defaults to `10`. It is at least `2` and at least the number
of allowed traffic models.

Population `0` first contains one unchanged run-baseline candidate for every
allowed model, in configured model-list order. Remaining slots cycle through
that same ordered model list. For each remaining candidate, every configured
search parameter is sampled independently:

- a floating parameter uses a uniform sample within its inclusive bounds;
- an integer parameter uses a discrete uniform sample including both bounds;
- a choice parameter uses a uniform choice from its configured values; and
- an omitted parameter retains its prepared run-baseline value.

Every complete randomized model is validated. An invalid sample restarts that
candidate's complete sampling attempt, up to `offspring_attempt_limit`; limit
exhaustion fails the run. Duplicate candidates are valid; the strategy does
not retry merely to force uniqueness and thereby bias the declared
distributions.

## Generation Evaluation

Every member of a completed population is evaluated through the existing 30 →
50 → 60 pipeline. This includes elite copies in their new generation; the
basic strategy introduces no result cache.

A failed child application or invalid result makes only that candidate
ineligible. Training continues with the remaining valid candidates. If a
generation has no valid candidate, the run fails and publishes no successful
winner. 30 retains the candidate directories and diagnostics required by its
application owner.

Stopping is checked only after the whole population has finished evaluation.
When the run continues, the evaluated population is the only parent and elite
source for the next population.

## Tournament Selection

`tournament_size` defaults to `3`, is at least `2`, and does not exceed the
configured population size. Each parent tournament samples distinct eligible
candidates uniformly without replacement and selects the candidate with the
best fitness.

For every tournament, the effective size is the smaller of `tournament_size`
and that tournament's eligible candidate pool. This handles both failed
candidates and a small same-model second-parent pool. The effective size is
recorded. A tournament fitness tie is resolved uniformly with the strategy's
seeded random stream. A candidate may participate in multiple separate
tournaments and may parent multiple children.

The first parent is selected from all eligible candidates, so traffic models
compete globally. When crossover is attempted, the second-parent tournament is
restricted to eligible candidates with the first parent's traffic-model name
and excludes the first parent itself.

## Elitism

`elite_count` defaults to `1`, is at least `1`, and is less than
`population_size`. The best valid candidates are copied into the same leading
number of slots in the next population without crossover or mutation. For a
fitness tie, the lower stable candidate ID wins.

If fewer valid candidates remain than `elite_count`, every valid candidate is
copied and the effective elite count is recorded. An elite retains an unchanged
model file but is evaluated normally as a new-generation candidate.

## Same-Model Crossover

`crossover_probability` defaults to `0.8` and is in `[0, 1]`. One child is
created for each non-elite population slot.

After selecting the first parent, the strategy independently decides whether
to attempt crossover. If it does not, the child begins as a complete clone of
the first parent. If no distinct eligible same-model second parent exists, the
child also begins as a clone and the skipped crossover is recorded.

With two parents of the same traffic model, every model-declared trainable
parameter independently takes the complete value from either parent with equal
probability. Non-trainable settings remain those of the first parent. The
second parent must have identical non-trainable settings; an incompatible pair
invalidates and restarts the complete offspring attempt.

The mixed model is validated as a whole. If independent choices violate a
cross-parameter constraint, the strategy uses the fitter complete parent
instead; for a fitness tie, it uses the parent with the lower stable candidate
ID. It records the rejected mix and fallback and never repairs individual
values. Crossover between different traffic-model names never occurs.

## Mutation Decision

Parameter mutation and model replacement are mutually exclusive child events.
Their configured probabilities are each in `[0, 1]` and their sum is at most
`1`. Normal values are:

```text
model replacement:  0.05
parameter mutation: 0.20
no mutation:        0.75
```

This single categorical draw happens after crossover or cloning. Elites never
reach this decision.

## Local Parameter Mutation

When parameter mutation is selected, exactly one configured search parameter
of the child's current model is chosen uniformly.

- A floating parameter adds a uniform seeded delta from
  `[-mutation_step, +mutation_step]`.
- An integer parameter adds a uniformly selected nonzero integer delta from
  that inclusive step interval.
- A choice parameter uniformly selects one configured value different from its
  current value.

The proposed value stays inside the configured inclusive search bounds, must
differ from the current value, and must make the complete model valid. An
out-of-range or invalid proposal invalidates that offspring attempt; it is not
clamped, reflected, or repaired.

## Traffic-Model Replacement

`model_replacement_probability` defaults to `0.05`. A nonzero value requires
at least two allowed traffic models. A run allowing only one model must
explicitly override this probability to `0`.

When replacement is selected, the strategy chooses uniformly among allowed
model names other than the child's current name. 30 invokes 40 model creation
for that model's normal file, applies any run-specific preparation required by
the traffic-model owner, then independently samples every configured search
parameter using the initial-population rules. Omitted parameters retain their
new run-baseline values.

The replacement candidate is validated as a complete model. Parameter mutation
does not additionally run for that child. Replacement is mutation, not
cross-model crossover, and no parameter value is translated between model
schemas.

## New Population Formation

After evaluation and stopping checks, the next population is formed as follows:

1. Copy the effective elites into leading slots.
2. For each remaining slot, begin offspring attempt `1`.
3. Select a first parent through a global tournament.
4. Apply the independent crossover decision and same-model rules.
5. Apply exactly one mutation-category decision.
6. Validate the resulting complete model.
7. On success, assign the stable generation/slot candidate ID and fill the
   slot.
8. On incompatible parent baselines or an invalid mutation or replacement
   result, discard the complete attempt, select parents again, and repeat for
   that slot.

An invalid crossover mix uses its documented parent fallback within the same
attempt; it does not by itself restart the attempt. A slot permits at most
`offspring_attempt_limit` complete attempts, normally `100`. Exhausting the
limit fails the training run rather than shrinking the population or silently
inserting an unreproduced clone.

The population size is constant across every fully formed generation.

## Stopping

The strategy supports three conditions:

- required effective `maximum_generations`, normally `50`;
- optional `target_score`; and
- optional positive `no_improvement_generations`.

`maximum_generations` counts fully evaluated populations, including population
`0`. A value of `50` therefore evaluates populations `0` through `49` at most.

`target_score` must be finite and valid for the selected similarity method's
declared range. The target is reached with `score >= target_score` for a
higher-is-better method and `score <= target_score` for a lower-is-better
method.

The no-improvement counter starts at zero after population `0`. It increments
after a later fully evaluated population that contains no candidate strictly
better than the best score previously seen, and resets to zero when strict
improvement occurs. Candidate-ID tie-breaking never resets it.

After each complete evaluation, the run stops when any enabled condition is
satisfied. If several become true together, every satisfied condition is
recorded. The published winner is the best valid candidate seen across the
entire run; a final score tie uses stable candidate ID.

## Validation and Failure

Complete configuration validation occurs before population creation. It
rejects unknown keys, unsupported schema or component names, duplicate models,
invalid types, invalid population or tournament sizes, invalid elitism,
non-finite or incompatible probabilities, mutation probabilities whose sum
exceeds `1`, insufficient models for replacement, invalid attempt or stopping
values, incompatible target score, unknown or non-trainable parameter paths,
invalid or degenerate search spaces, prepared run-baseline values outside their
configured ranges, and search entries incompatible with the parameter's
declared type and individual bounds. Cross-parameter constraints are checked
when each complete candidate is validated; a rectangular configured search
space need not make every possible combination valid.

Candidate model validation occurs before traffic generation. Invalid
offspring follow the bounded retry rules. A failed candidate evaluation remains
in diagnostics but never enters selection or elitism. No valid candidate,
offspring-attempt exhaustion, invalid stopping state, or corrupted lineage
fails the run without publishing a successful winner.

No genetic operation requires network access, live capture, or elevated
privileges.

## Diagnostics and Lineage

Run diagnostics identify the strategy implementation and random-generator name
and version.

For each candidate, retained run data identifies:

- stable candidate ID and generation slot;
- model name and validated model-file hash;
- elite source or parent candidate IDs;
- effective tournament sizes and tie decisions;
- crossover attempted, skipped, accepted, or rejected with fallback;
- mutation category and mutated parameter or replacement model;
- offspring attempt count;
- the decisions and rejection reason for every discarded initialization or
  offspring attempt;
- traffic-generation and similarity-result lineage; and
- eligibility and failure reason.

The ranking report records every generation's valid ordering, best-so-far
candidate, stopping counters, and all conditions satisfied when training stops.
The existing startup record contains the fully resolved configuration,
including `--set` overrides.

## Testing Expectations

Future deterministic, unprivileged functional-core tests cover:

- model-covering initial populations and round-robin fill;
- numeric and choice initialization, omitted parameters, and valid duplicates;
- seeded reproducibility and independence from evaluation completion order;
- tournament selection, seeded fitness ties, and reduced effective size;
- elite count, deterministic elite ties, and failed-candidate exclusion;
- crossover probability, same-model pairing, per-parameter choices,
  incompatible-parent rejection, invalid-mix fallback, and cross-model
  prohibition;
- the mutually exclusive mutation draw and exact probabilities at boundaries;
- floating, integer, and choice local mutation;
- model replacement, new-model initialization, and one-model rejection;
- invalid offspring retries, exact population size, and attempt exhaustion;
- maximum-generation, target-score, and stagnation stopping independently and
  when several trigger together;
- higher- and lower-is-better score directions;
- final-winner selection and stable score ties;
- strict configuration and `--set` parsing and validation; and
- complete candidate and generation lineage.

Imperative-shell tests use temporary directories and fake 40, 50, and 60 child
applications to verify deterministic argument vectors, retained failures, full
generation barriers, elite reevaluation, and atomic final publication. No test
uses a real network, live capture, or elevation.

## Alternatives Considered

### Rank selection

Rank-based selection is also independent of raw score scale, but it requires an
additional probability-distribution rule over the full sorted population.
Tournament selection was chosen because its selection pressure is controlled
by one intuitive size and it works directly with method-defined ordering.

### Fitness-proportional selection

Fitness-proportional selection was rejected because raw score scale, sign, and
spacing directly affect selection probability. That conflicts with the
registry's allowance for interchangeable similarity methods with different
mathematically honest ranges.

### Steady-state replacement

Replacing candidates one at a time was rejected for this basic strategy. Full
generation barriers make stopping, lineage, deterministic parallel evaluation,
and complete population reports easier to understand.

### Random-reset parameter mutation

Resetting a parameter anywhere in its range was rejected as the default because
it can repeatedly destroy good local values. Explicit traffic-model replacement
already supplies a wider exploration mechanism; local mutation supplies
refinement.

### Elite result caching

Reusing an elite's old generated traffic and score could save work, but it would
add cache identity and cross-generation artifact rules. The basic strategy
reevaluates every generation member and leaves caching to a future design.
