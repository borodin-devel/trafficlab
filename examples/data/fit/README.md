# Deterministic offline fitting fixture

This Docker-free fixture exercises production codecs and the real heterogeneous fitting path, including
checkpoint-compatible artifacts. Regenerate it with `uv run --locked python scripts/generate_fit_fixtures.py`;
verify every expected path and byte with `uv run --locked python scripts/generate_fit_fixtures.py --check`.

The reference contains 21 Ethernet events from timestamp 20.0 through 30.0, so the one normalized observation
window is exactly `W = 10.0` seconds. Registry metadata remains lexical for display, while master seed 73 derives
the neutral family priority `mmpp`, `markov_renewal`, `poisson_empirical` before any search draw. Population size is
6, with quota 2 per family, elite count 1, generation count 1 (evaluated generations 0 and 1), tournament size 2,
duplicate mutation attempts 1, selection seeds `[17]`, and the distinct final-validation seed 97. Resume is enabled
and early stopping is disabled.

Every family deliberately uses nondefault operators:

- `markov_renewal`: crossover 1.0, mutation 0.0, normalized scale 0.06.
- `mmpp`: crossover 0.45, mutation 0.0, normalized scale 0.08.
- `poisson_empirical`: crossover 0.35, mutation 0.0, normalized scale 0.07.

Zero ordinary mutation makes the different-family forced-mutation boundary directly observable in the integration
trace. Trial guards are 500 packets, 1,000,000 bytes, and 5.0 seconds; final guards are 1,000 packets, 2,000,000
bytes, and 10.0 seconds. The checked checkpoint is terminal generation 1, `ga_history.csv` is its exact derived
projection, and `best_model.json` is the independently final-validated winner.
