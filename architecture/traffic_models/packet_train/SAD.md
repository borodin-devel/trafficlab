# Packet-Train Software Architecture Document

## Context, Goals, and Boundaries

Goal is future explicit burst-train modelling. Current architecture does not
define whether trains overlap, how start times/counts/gaps/sizes are distributed,
how incomplete trains interact with duration stops, how fitting works, or the
model-file schema.

## Architecture Status and Risks

Selection remains unsupported. Research must address zero gaps/equal timestamps,
burst size limits, partial train policy, deterministic order, resource bounds,
and correctness/statistical tests. No fixed/Poisson train assumption is implied.
