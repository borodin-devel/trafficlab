# 04 Stopping, Validation, Lineage, and Publication

## Stopping

Stopping is checked only after every candidate in the population has completed
evaluation. Three conditions are supported:

- `maximum_generations` is always effective and positive. It counts fully
  evaluated populations, including population `0`.
- Optional `target_score` is finite and valid for the selected similarity
  method. It is reached with `score >= target_score` for higher-is-better and
  `score <= target_score` for lower-is-better.
- Optional `no_improvement_generations` is positive. Its counter starts at zero
  after population `0`, increments after a later population has no candidate
  strictly better than the best score previously seen, and resets only after a
  strict improvement.

The run stops when any enabled condition is satisfied. If conditions become
true together, every satisfied condition is recorded. The winner is the best
valid candidate seen across the complete run, with the lower stable ID used for
a final score tie.

The maximum-generation condition therefore limits complete evaluation cycles,
not partially formed or partially evaluated populations.

## Configuration and Candidate Validation

The [authoritative configuration reference](../../apps/30_genetic_training/CONFIGURATION_SCHEMA.md)
owns the complete setting contract. Before population creation, validation
rejects unknown or unsupported component names, an empty or duplicate
model selection, invalid population or tournament sizes, invalid elitism,
non-finite or incompatible probabilities, a mutation-probability sum above
one, insufficient models for replacement, invalid attempt or stopping values,
an incompatible target score, unknown or non-trainable parameter paths,
baseline values outside search domains, and type-invalid or degenerate search
spaces.

The selected traffic model validates every complete in-memory candidate before
model-owned preparation and validates the distinct generation-ready result
before traffic generation. Cross-parameter constraints are checked on the
complete candidate; a rectangular numeric search domain need not make every
possible combination valid. Invalid randomized candidates and offspring follow
their bounded whole-attempt rules.

No valid candidate, attempt exhaustion, corrupted lineage, or invalid stopping
state fails the run without a successful winner. No genetic operation requires
network access, live capture, or elevated privileges.

## Determinism, Lineage, and Publication

Candidate configuration and prepared-model serialization are canonical and
deterministic. Each builder digest and distinct generation-ready model are
validated and hashed before traffic generation. Retained run data identifies:

- stable candidate ID and generation slot;
- model name, immutable builder digest, and prepared model-file hash;
- elite source or parent candidate IDs;
- effective tournament sizes and tie decisions;
- crossover outcome and any rejected-mix fallback;
- mutation category and changed parameter or replacement model;
- every whole-attempt decision, rejection reason, and attempt count;
- traffic-generation and similarity-result lineage; and
- eligibility and failure reason.

Generation diagnostics retain valid ordering, the best-so-far candidate,
stopping counters, and every satisfied final condition. 30 owns atomic
publication of successful final artifacts and retains failure diagnostics.
