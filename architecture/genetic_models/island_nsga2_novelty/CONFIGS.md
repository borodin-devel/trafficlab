# Island NSGA-II Novelty Configuration

## Known Required Groups

Configuration must define positive island count/population size, seeds and
substream derivation, descriptor fields and positive scales, novelty distance,
neighbor count, bounded archive, migration interval/count/ring behavior,
compatible operators, retries, stopping, failure policy, and Pareto winner policy.

## Unresolved Status

Exact TOML paths, defaults, descriptor formula list, archive insertion/eviction,
migration failure action, stopping, and single-winner policy are not defined in
current architecture. The strategy remains documented but unselectable until
these fields and validation are owned here and integrated into genetic training.
