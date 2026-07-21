# Markov Registry Correction Design

## Decision

While Trafficlab's architecture is still under development, `markov_renewal`
belongs directly in the traffic-model registry. It does not require an
architecture amendment.

## Changes

- Delete `architecture/amendments/001_markov_renewal_registry.md`.
- Add `markov_renewal` and its owner link to the model table in
  `architecture/traffic_models/README.md`.
- Replace the Markov Renewal owner's amendment reference with a direct
  reference to the common traffic-model registry.

## Constraints

The Markov Renewal model behavior and model-file schema do not change. The
correction only changes how its selectable-name registration is documented.

## Verification

Verify that no architecture document refers to Amendment 001, the registry
links to the owner, all relative links resolve, and the embedded TOML examples
still parse.
