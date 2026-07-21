# 00 General Information

## Role

`sequence_kernel_mmd` is a selectable similarity method that compares the
whole ordered timing--size sequences found in fixed time windows of one
reference and one generated supported Ethernet PCAPNG file. It is intended to
detect differences in within-window packet ordering and timing--size patterns
that point clouds, rates, and one-step transitions can lose.

## Shared Behavior

The [temporal Layer 2 rules](../TEMPORAL_L2.md) own supported-PCAPNG
extraction, use of timestamps and original Ethernet frame lengths only,
time-zero alignment, IAT derivation, input validation, deterministic
processing, configuration recording, result publication, and offline testing
boundaries. This method adds only window-sequence construction, the
signature-kernel MMD, and method-specific validation.
