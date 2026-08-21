# Traffic Models

Traffic models consume the [canonical trace](../SYSTEM.md#data-flow) and produce
bounded synthetic traces through one shared interface:

```text
fit(reference, genes) -> fitted model
generate(fitted model, seed, observation_window, limits) -> canonical trace
serialize(fitted model) -> JSON-compatible value
```

`fit` is deterministic for a reference and chromosome. `generate` owns an
explicit `numpy.random.Generator(numpy.random.PCG64(seed))`; model code never
uses NumPy's module-global RNG or `default_rng`. Scalar draw order, primitive,
shape, and endpoint semantics are part of scientific artifact schema 4. Every generator starts at zero
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

Fitted models and checkpoints carry global scientific artifact schema version
4. That version identifies their scientific semantics, not only
their JSON shape. The version with corrected MMPP arrival-epoch initialization
must reject artifacts carrying the older MMPP semantics as incompatible before
generation, resume, or stage reuse. It does not migrate them or add per-family
schema or plug-in mechanisms; models must be refitted under the current global
version. Schema-3 models and checkpoints use the pre-Scapy publication
semantics, while schema-2 artifacts also use the former MT19937 draw protocol.
Both require explicit refitting and are never replayed as schema-4 artifacts.

## Direct scientific evidence

Each family requires bounded direct scientific tests with seeds, sample sizes,
tolerances, and failure messages predeclared before results are inspected. The
oracles must be analytical calculations or small independent test-only
implementations rather than the production generator or similarity functions
being validated.

Serialization round trips, fixed-seed reproducibility, and generation by the
same implementation are necessary engineering checks, but they are not
sufficient scientific evidence. Family documents therefore state the direct
distributional and completion behavior that the bounded validation matrix must
cover. These tests support descriptive inference under their declared finite
protocol; they do not identify a causal traffic mechanism or establish
generalization to unseen programs.

## Fair competition

Every candidate is evaluated against the same reference features, same `W`,
trial seeds, reliability guards, and similarity weights. Trial outputs are
regenerated from scratch. A family cannot supply its own evaluator or silently
change a window or guard. The
[genetic strategy](../genetic_models/basic_generational.md) records a champion
for every enabled family as well as the overall winner.

## MVP families

<table>
  <thead>
    <tr>
      <th>Model</th>
      <th>Dependence represented</th>
      <th>Chromosome</th>
      <th>Strength</th>
      <th>Main limitation</th>
      <th>Cost</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="poisson_empirical.md">Poisson empirical</a></td>
      <td>Independent arrivals and empirical direction/size marks</td>
      <td>Rate multiplier</td>
      <td>Transparent baseline</td>
      <td>No temporal dependence</td>
      <td>Linear</td>
    </tr>
    <tr>
      <td><a href="markov_renewal.md">Markov Renewal</a></td>
      <td>Observable state transitions and transition-conditioned timing</td>
      <td>Bins, smoothing, support, time scale</td>
      <td>Captures sequence structure directly</td>
      <td>State sparsity</td>
      <td>Linear fit plus state matrix</td>
    </tr>
    <tr>
      <td><a href="mmpp.md">Two-state MMPP</a></td>
      <td>Latent low/high-rate regimes</td>
      <td>Two transition and two arrival rates</td>
      <td>Captures burst/idle timing compactly</td>
      <td>Marks ignore latent regime</td>
      <td>Linear mark fit; event simulation</td>
    </tr>
  </tbody>
</table>

Only these implemented families belong in the runtime registry. A future family
must arrive with implementation, focused tests, one mathematical document, and a
reason supported by experiment evidence.
