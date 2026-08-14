# Genetic Models

Genetic models search model-family chromosomes against a common behavioral
fitness. A strategy owns population construction, selection, reproduction,
checkpointing, and termination. Traffic models continue to own chromosome
meaning, repair, fitting, and generation.

The MVP enables one strategy:

- [Basic generational](basic_generational.md): a bounded heterogeneous
  population with tournament selection, elitism, and family champions.

The registry contains only implemented strategies. A new strategy needs a clear
scientific reason, deterministic tests, and its own concise mathematical and
operational description; a backlog idea does not receive a placeholder module.

The selected result depends on the finite population, generation count, gene
bounds, genetic operators, and configured seeds. Fitness scores are descriptive
similarity scores, not likelihoods. They do not identify a causal traffic
mechanism, establish universal model-family superiority, or demonstrate
generalization to unseen programs. Final evaluation with a fresh simulation
seed on the training reference does not change those inference limits.
