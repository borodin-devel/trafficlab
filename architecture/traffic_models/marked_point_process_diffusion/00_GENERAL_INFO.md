# 00 General Information

## Role

`marked_point_process_diffusion` is a selectable Layer 2 marked temporal point
process that generates one fixed-duration traffic window as a whole.  It starts
with seeded noise and denoises an ordered, variable-count sequence of normalized
`(log1p(IAT), frame length)` event pairs.  A generated window may depend only
on its already generated event history and its recorded seed; it has no other
condition.

This owner defines the model-specific representation, diffusion architecture,
fitting, model file, generation, and validation.  The [neural marked-point-
process common rules](../NEURAL_MARKED_POINT_PROCESS.md) own the event domain,
reference source mode, deterministic source enumeration, per-file boundary,
shared preparation and split rules, shared lineage, and genetic-candidate
rules.  The model is registered in [Traffic Models](../README.md).
