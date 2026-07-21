# Traffic-Model Protocol Scope Design

**Date:** 2026-07-20  
**Status:** approved design, pending architecture documentation

## Purpose

Make the intended protocol depth of Trafficlab traffic models explicit.

## Decision

Traffic models primarily simulate Layer 2 behavior, especially Ethernet frame
timing and frame size. Basic models remain entirely at this level.

Explicitly advanced traffic models may additionally model IPv4, IPv6, TCP, or
UDP behavior when later work requires it. Application-layer protocols and
other higher-level protocol simulation are outside the traffic-model scope.

## Alternatives

- A strict Layer-2-only boundary was rejected because it would prevent later
  IP, TCP, or UDP models.
- A protocol-neutral boundary was rejected because it would obscure the
  project's Layer 2 priority and allow unnecessary application-level modeling.
- A Layer 2 primary scope with a narrow advanced-model exception was selected.

## Documentation Change

`architecture/traffic_models/README.md` will own this policy in a concise
`Protocol Scope` section. Individual model documents reference the registry
scope rather than restating it. No existing model behavior, schema, or
implementation changes.
