# 05 Testing and Reading

## Testing

Future deterministic, unprivileged functional-core tests cover initial model
coverage and sampling, duplicate candidates, canonical seeded ordering,
tournament reduction and score ties, elitism and failed-candidate exclusion,
every crossover branch, each mutation kind, model replacement, whole-attempt
retry and exhaustion, fixed population size, both score directions, every
stopping condition, simultaneous stopping, final-winner ties, validation, and
complete lineage.

Imperative-shell tests use temporary directories and fake 40, 50, and 60 child
applications to verify argument vectors, full-generation barriers, retained
failures, hashes, elite reevaluation, and atomic final publication. No test uses
a real network, live capture, or elevated privileges.

## Computational Complexity

Let `P` be population size, `K` the largest trainable-parameter count, `T` the
effective tournament size, and `A` the offspring-attempt limit. Each generation
executes exactly `P` candidate evaluations. Excluding those child costs, a
deterministic sorted implementation requires at most
`O(P log P + P * A * (T + K))` time. Live population state is `O(P * K)`;
retained decision lineage grows with completed generations and attempted
offspring and is therefore bounded by configured generation and attempt limits.

## Reading

Follow the [architecture governance](../../README.md), the [genetic-model
registry](../README.md), the 30 application owner, this strategy owner, the
configuration reference, and the selected traffic-model and similarity-method
owners before changing genetic-training behavior.

## Verification Properties

Property tests assert fixed population size, complete model validity, same-model crossover, exclusive mutation branches, bounded attempts, and deterministic replay. Integration tests additionally verify resource ordering and atomic winner publication.
