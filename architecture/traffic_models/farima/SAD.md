# FARIMA Software Architecture Document

## Context, Goals, and Boundaries

Goal is future long-range-dependence modelling. Current architecture defines no
event variable, differencing convention, AR/MA formulation, stationarity/
invertibility domain, fitting algorithm, size model, generation law, or schema.

## Architecture Status and Risks

No runtime structure or public model interface is approved. Selection must
fail as unsupported. Required design must address numerical stability, finite
sample bias, fractional differencing truncation, computational complexity,
deterministic fitting/sampling, and correctness against an independent library.
No implementation detail may be inferred from the model name alone.
