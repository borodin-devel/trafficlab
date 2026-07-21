# 00 General Information and Components

## Role

`l2_ks_weighted` is a selectable combined similarity method. It evaluates both
inter-arrival-time and original Layer 2 frame-length similarity for one
reference Ethernet PCAPNG and one generated Ethernet PCAPNG, then publishes one
weighted primary score.

## Component Owners

The [IAT KS method](../iat_ks/README.md) owns IAT extraction, its minimum input,
validation, distance, similarity, sample counts, and limitations. The
[frame-size KS method](../frame_size_ks/README.md) owns the equivalent rules for
original frame length. The shared [KS family](../KS.md) owns behavior common to
both components.

This document owns only weight configuration, component-success requirements,
the combined calculation, and combined result details. It does not redefine or
weaken either component.

## Configuration

The normal method settings are:

```toml
iat_weight = 0.5
frame_size_weight = 0.5
```

Both weights are finite and non-negative, and their sum must be `1`. Boundary
pairs such as `0` and `1` are valid. The sum rule is evaluated from the exact
decimal configuration values so binary floating-point rounding cannot reject a
mathematically valid pair.

Invalid weights fail configuration validation. They are never silently
normalized. These settings belong to the selected method configuration used by
[60 similarity evaluation](../../apps/60_similarity_evaluation/README.md).
