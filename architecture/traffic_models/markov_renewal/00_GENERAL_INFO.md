# 00 General Information

## Role

`markov_renewal` generates Layer 2 Ethernet traffic as a sequence of paired
inter-arrival times (IATs) and original Ethernet frame lengths. A state is a
combined IAT category and frame-size category. The model learns a first-order
transition distribution between states and emits an observed pair from each
selected state.

The model is selectable in [Traffic Models](../README.md). Read the
[architecture map](../../README.md) and the common traffic-model rules before
this owner.

## Boundaries

This model is for Layer 2 Ethernet simulation only. It uses timestamps and
original Ethernet frame lengths in the inclusive range 60 through 1514 bytes.
It does not model IP, TCP, UDP, addresses, payloads, flows, direction,
sessions, or higher protocols.

Raw IEEE 802.11/Radiotap inputs are outside this model. A future converter may
make a supported Ethernet representation, but that is a separate contract.
Captured bytes are not an emission feature: a truncated capture can retain a
frame length while omitting bytes, and the retained bytes must not influence
this model.

No operation requires network access, live capture, or elevated privilege.
