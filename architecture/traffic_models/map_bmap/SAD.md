# MAP/BMAP Software Architecture Document

## Context, Goals, and Boundaries

Goal is future Markovian arrival-process support including batches. No approved
matrix representation, generator constraints, identifiability policy,
stationary initialization, batch timing/order, fitting, mark law, or file schema
exists. It cannot be implemented or selected from its title alone.

## Architecture Status and Risks

Selection fails as unsupported. Research must address valid generator matrices,
matrix exponential stability, likelihood/EM choice, label non-identifiability,
batch semantics in PCAPNG timestamps, deterministic canonicalization,
complexity, and independent mathematical tests.
