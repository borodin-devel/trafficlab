# 00 General Information

## Role

`joint_sinkhorn_wasserstein` is a selectable similarity method that compares
the joint empirical timing--size distributions of one reference and one
generated supported Ethernet PCAPNG file. It detects whether particular frame
lengths occur with particular inter-arrival times (IATs).

## Shared Behavior

The [temporal Layer 2 rules](../TEMPORAL_L2.md) own supported-PCAPNG
extraction, use of timestamps and original Ethernet frame lengths only,
time-zero alignment, IAT derivation, input validation, deterministic
processing, configuration recording, result publication, and offline testing
boundaries. This method adds only its joint-distribution mathematics and
method-specific validation.
