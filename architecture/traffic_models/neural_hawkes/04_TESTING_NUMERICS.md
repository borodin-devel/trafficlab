# 04 Determinism, Limits, and Verification

With identical validated inputs, model file, reference artifacts,
configuration, seeds, and supported library versions, preparation and fitting
have deterministic window order, batch order, initialization, checkpoint
selection, canonical weight serialization, and lineage. Given the same valid
model and generation seed, causal sampling and frame output order are
deterministic. Unsupported nondeterministic operations are forbidden unless a
future schema explicitly records and controls them.

The model has no unbounded history, open-ended training, implicit source
discovery, silent data repair, or support for correlations beyond its finite
history context. It can overfit a single capture as diagnosed by the common
owner. It is not a flow, payload, protocol, address, or application model.

Future unit tests cover causal self-attention masking, empty and truncated
histories, non-negative IAT likelihood and sampling including zero IAT,
continuous mark normalization, deterministic 60-through-1514 integer mapping,
finite-context validation, likelihood calculation, checkpoint tie selection,
early stopping, strict model/weight-file validation, learned-weight lineage,
and fixed-seed causal generation. Future integration tests use small offline
Ethernet pcapng fixtures to exercise file-boundary separation, source-mode and
directory validation, training, atomic model publication, and generation.
They require no live capture, network access, sudo, root, or elevated
privilege.

## Computational Complexity

Let `K` be `history_length`, `H` hidden size, and `L` attention-layer count.
Dense causal self-attention for one history has
`O(L * (K^2 * H + K * H^2))` time and
`O(L * K^2 + K * H)` activation memory, excluding learned weights. Fitting
multiplies the batched forward/backward cost by the finite epoch and batch
counts; generation performs one bounded-history forward pass per event. Any
optimized kernel must preserve masks, ordering, numerics, and deterministic
outputs, and resource admission must use the configured dimensions.

## Reading

Read [Traffic Models](../README.md), then the [neural marked-point-process
common rules](../NEURAL_MARKED_POINT_PROCESS.md), before changing this owner.
The surrounding workflow is owned by [Genetic Training](../../apps/30_genetic_training/README.md),
[Model Creation](../../apps/40_model_creation/README.md), and
[Traffic Generation](../../apps/50_traffic_generation/README.md).
