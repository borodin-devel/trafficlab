# Traffic Models

Traffic models consume the [canonical trace](../SYSTEM.md#data-flow) and produce
bounded synthetic traces through one shared interface:

```text
fit(reference, genes) -> fitted model
generate(fitted model, seed, observation_window, limits) -> canonical trace
serialize(fitted model) -> JSON-compatible value
```

`fit` is deterministic for a reference and chromosome. `generate` owns a local
random generator initialized only from its seed. Every generator starts at zero
and simulates the complete closed interval `[0, W]`. It emits events at or before
`W` and finishes normally only after the next simulated event would be after
`W`. Packet-count, output-size, and wall-time limits are reliability guards.
Reaching one before full-window completion returns an explicit
incomplete-generation error, not a shortened trace. Timestamps must never
decrease, and frame lengths must remain in bounds.

The output-byte guard counts the sum of canonical Ethernet frame lengths, not
PCAPNG block or file overhead. Generators check every guard before stochastic
decisions, immediately after every stochastic draw, and before emitting an
in-window packet. Events retained on an incomplete result are diagnostics only;
only a complete result may become a reusable trace or generated PCAPNG.

## Serialized model

Every `best_model.json` contains the model family, fitted parameters, winning
genes, reference SHA-256, `observation_window_seconds`, estimator choices,
parameter bounds, seed policy, and enough empirical values to generate without
reopening the reference capture. Numbers must be finite. Loading repeats
structural and bound validation.

## Fair competition

Every candidate is evaluated against the same reference features, same `W`,
trial seeds, reliability guards, and similarity weights. Trial outputs are
regenerated from scratch. A family cannot supply its own evaluator or silently
change a window or guard. The
[genetic strategy](../genetic_models/basic_generational.md) records a champion
for every enabled family as well as the overall winner.

## MVP families

| Model | Dependence represented | Chromosome | Strength | Main limitation | Cost |
|---|---|---|---|---|---|
| [Poisson empirical](poisson_empirical.md) | Independent arrivals and empirical direction/size marks | Rate multiplier | Transparent baseline | No temporal dependence | Linear |
| [Markov Renewal](markov_renewal.md) | Observable state transitions and transition-conditioned timing | Bins, smoothing, support, time scale | Captures sequence structure directly | State sparsity | Linear fit plus state matrix |
| [Two-state MMPP](mmpp.md) | Latent low/high-rate regimes | Two transition and two arrival rates | Captures burst/idle timing compactly | Marks ignore latent regime | Linear mark fit; event simulation |

Only these implemented families belong in the runtime registry. A future family
must arrive with implementation, focused tests, one mathematical document, and a
reason supported by experiment evidence.
