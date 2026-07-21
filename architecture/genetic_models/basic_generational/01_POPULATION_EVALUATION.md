# 01 Population and Evaluation

## Initial Population

`population_size` is at least two and at least the number of allowed traffic
models. Population `0` is formed in this order:

1. Add one unchanged normal-builder-value candidate for each allowed model in
   configured model-list order.
2. Fill remaining slots by cycling through that same model list.
3. For every remaining candidate, independently sample each configured search
   parameter in ascending dotted-path order:
   - a floating parameter is uniform within its inclusive bounds;
   - an integer parameter is discrete uniform including both bounds;
   - a choice parameter is uniform over its configured values; and
   - an omitted trainable parameter retains its normal-builder baseline value.
4. Validate the complete candidate. An invalid sample restarts that slot's
   whole sampling attempt, bounded by `offspring_attempt_limit`.

Attempt exhaustion fails the run. Duplicate candidates are valid; the strategy
does not retry only to force uniqueness and bias the declared distributions.

## Generation Evaluation

Every member of a completed population runs through the existing immutable
builder → fully applied candidate → model-owned preparation → 50 → 60 pipeline.
This includes unchanged elite copies in their new generation; this strategy
has no prepared-model or result cache.

The entire population completes before stopping or reproduction. An
unsuccessful child application or invalid result makes only that candidate
ineligible, and training continues with other valid candidates. A generation
with no valid candidate fails the run without a successful winner.

Only the fully evaluated current population supplies parents and elites for the
next population.
