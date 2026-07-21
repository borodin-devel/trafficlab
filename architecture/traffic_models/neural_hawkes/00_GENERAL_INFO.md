# 00 General Information and Causal Representation

## Role

`neural_hawkes` is a selectable, causal Layer 2 neural marked temporal point
process. It generates exactly one Ethernet frame event at a time. For each
next event, it encodes only earlier `(IAT, frame length)` events with
self-attention and produces a conditional distribution for the next IAT and
frame-length mark.

This owner defines the model-specific architecture, probability law, fitting,
model file, generation, and validation. The [neural marked-point-process
common rules](../NEURAL_MARKED_POINT_PROCESS.md) own the event domain,
reference source mode, deterministic directory enumeration, per-file
boundary, window and split preparation, shared lineage, and genetic-candidate
rules. The model is registered in [Traffic Models](../README.md).

## Scope and Causal Representation

The model operates only on Layer 2 event pairs. It does not receive or emit
packet bytes, addresses, IP, TCP, UDP, flows, directions, sessions, protocol
labels, application data, external labels, or a live-network state.

For an event history `H_i = (e_{i-K}, ..., e_{i-1})`, where
`e_j = (iat_j, length_j)` and `K` is the configured finite history length, the
model computes:

```text
x_j = event_embedding(log1p(iat_j), normalized(length_j)) + position_embedding
h_i = causal_self_attention(x_{i-K}, ..., x_{i-1})
p(iat_i, length_i | H_i) = p_time(iat_i | h_i) * p_mark(length_i | iat_i, h_i)
```

Attention uses a strict causal mask, so no representation for `e_i` or any
later event can influence its conditional law. At the beginning of a file or
window, the history is the available earlier events from that same file only;
zero available events use a learned empty-history representation. Histories
are never extended across the common owner's file boundary.

`history_length` is a positive finite event count. `attention_layers`,
`attention_heads`, and `hidden_size` are positive and compatible: the hidden
size is divisible by the number of heads. Positional encoding has a configured
finite maximum equal to the history limit. An event sequence that cannot meet
these configuration constraints fails before fitting or generation.
