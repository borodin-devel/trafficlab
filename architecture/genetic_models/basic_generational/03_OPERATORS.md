# 03 Crossover, Mutation, Replacement, and Retries

## Same-Model Uniform Crossover

For every non-elite slot, the strategy first selects one parent globally and
then makes the independent `crossover_probability` draw. When crossover is not
selected, the child begins as a complete clone of the first parent.

When crossover is selected, the strategy forms the restricted second-parent
pool described above. An empty pool skips crossover, begins with a clone, and
records the reason. Otherwise, it selects a distinct second parent through a
tournament.

Both parents must have the same traffic-model name and identical non-trainable
settings. An incompatible pair invalidates the whole offspring attempt. For a
compatible pair, each model-declared trainable parameter independently takes
its complete value from either parent with probability `0.5`, in ascending
dotted-path order. Non-trainable settings remain those of the first parent.
Crossover between different model names never occurs.

The complete mixed model is validated. If only the independent combination
violates a cross-parameter constraint, the child instead uses the fitter
complete parent, or the lower-ID parent on a score tie. The rejected mix and
fallback are recorded. The strategy never clamps, repairs, or mixes individual
parts of a parameter value.

## Mutation Decision

After crossover or cloning, each non-elite child receives one categorical
decision: model replacement, local parameter mutation, or no mutation. Both
configured mutation probabilities are finite values in `[0, 1]`, their sum is
at most `1`, and the no-mutation probability is one minus that sum. Elites do
not receive this decision.

## Local Parameter Mutation

When local mutation is selected, exactly one configured search parameter of the
child's current model is chosen uniformly. Only parameters present in that
model's configured search space can be locally mutated.

The proposal depends on the model-declared parameter kind:

```text
float:   current + Uniform(-mutation_step, +mutation_step)
integer: current + UniformInteger({-mutation_step, ..., -1, 1, ..., +mutation_step})
choice:  one configured value other than the current value, uniformly
```

The proposed value must differ from the current value, stay within its search
domain, and produce a valid complete model. An invalid proposal invalidates the
whole offspring attempt. It is never clamped, reflected, repaired, or replaced
with an unrelated random value.

## Traffic-Model Replacement

When model replacement is selected, the strategy chooses uniformly among
allowed model names other than the child's current name. A nonzero replacement
probability therefore requires at least two allowed models.

30 obtains the selected model's immutable normal builder through its existing
40 orchestration. The strategy independently samples the replacement model's
configured search parameters by the initial-population rules, retains builder
baseline values for omitted parameters, and validates the complete candidate
configuration. Model-owned preparation runs only after those values are fixed.
Local parameter mutation does not additionally run. Replacement never
translates parameter or non-trainable values between model schemas.

## New Population Formation and Retries

After generation evaluation and stopping checks, the next population is formed
in this order:

1. Copy effective elites into leading slots.
2. For each remaining slot, begin a whole offspring attempt.
3. Select the first parent through a global tournament.
4. Apply the independent crossover decision and same-model rules.
5. Apply exactly one mutation-category decision.
6. Validate the resulting complete model.
7. On success, assign its `(generation, slot)` candidate ID and fill the slot.
8. On incompatible parent builder contexts or invalid mutation or replacement,
   discard the whole attempt, select parents again, and repeat for that slot.

An invalid crossover mix uses its documented complete-parent fallback within
the current attempt. Every slot permits at most `offspring_attempt_limit` whole
attempts. Exhaustion fails the run instead of shrinking the population or
silently inserting an unrecorded clone. Every fully formed generation has the
configured population size.
