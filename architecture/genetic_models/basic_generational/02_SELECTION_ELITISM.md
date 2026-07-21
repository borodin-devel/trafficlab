# 02 Selection and Elitism

## Tournament Selection

Each tournament samples distinct eligible candidates uniformly without
replacement. Its effective size is:

```text
min(tournament_size, eligible candidate pool size)
```

This reduction applies both after candidate failures and to a small same-model
second-parent pool. The effective size is recorded. The best fitness wins; a
fitness tie is resolved uniformly through the strategy's seeded random stream.
A candidate may participate in separate tournaments and parent more than one
child.

The first-parent tournament uses every eligible candidate, so different
traffic models compete globally. A second-parent tournament, when needed, uses
only eligible candidates with the first parent's exact traffic-model name and
excludes the first parent.

## Elitism

`elite_count` is at least one and less than `population_size`. The best valid
candidates are copied without crossover or mutation into the leading slots of
the next population. A score tie uses the lower stable candidate ID.

If fewer valid candidates remain than `elite_count`, every valid candidate is
copied and the reduced effective elite count is recorded. An elite retains an
unchanged model file but is evaluated normally under its new generation and
slot identity.
