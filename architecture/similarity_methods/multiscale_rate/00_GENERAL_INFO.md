# 00 General Information

## Role

`multiscale_rate` is a selectable similarity method that compares packet-rate
and original-byte-rate structure at several time scales for one reference and
one generated supported Ethernet PCAPNG file. It is intended to detect bursts
and idle periods over the configured comparison horizon.

## Shared Behavior

The [temporal Layer 2 rules](../TEMPORAL_L2.md) own supported-PCAPNG
extraction, use of timestamps and original Ethernet frame lengths only,
time-zero alignment, input validation, deterministic processing,
configuration recording, result publication, and offline testing boundaries.
This method adds only binning, rate-distance mathematics, and method-specific
validation.
