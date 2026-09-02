# [TASK-1-05764bb8] Coordinate metadata and required-family config value types

Status: DONE

Implemented the Task 1 shared contract while retaining the three-family runtime
registry and scientific artifact schema 4.

`GeneCoordinateKind` now belongs to common configuration. `ModelFamily` exposes
one declared kind per canonical gene name; Poisson, Markov Renewal, and MMPP
declare their existing transforms. Genetic coordinate construction and registry
bound plumbing derive exact bound types from that metadata rather than checking
Markov Renewal family identity. Registry construction now validates named bounds
through the registered family's own strict config model.

Added strict standalone config value types for packet HMM state count (2..4),
Markov packet-train length cap (3..8), ACD order (1..3), and NHPP bin count
(2..16). They have one-coordinate operator defaults and are deliberately not
exposed through `ModelsConfig`, `FamilyName`, or `REGISTRY`.

Changed files:

- `src/trafficlab/common/config.py`
- `src/trafficlab/generation/models/common.py`
- `src/trafficlab/generation/models/poisson.py`
- `src/trafficlab/generation/models/markov_renewal/family.py`
- `src/trafficlab/generation/models/mmpp.py`
- `src/trafficlab/generation/models/registry.py`
- `src/trafficlab/fitting/genetic/coordinates.py`
- `tests/unit/common/test_config_schema.py`
- `tests/unit/common/test_config_validation.py`
- `tests/unit/generation/models/test_registry.py`
- `architecture/genetic_models/basic_generational.md`

RED evidence, before production edits:

```text
uv run --locked pytest -q tests/unit/common/test_config_schema.py tests/unit/common/test_config_validation.py tests/unit/generation/models/test_registry.py tests/unit/fitting/genetic/test_coordinates.py
ERROR: cannot import name 'AcdConfig' from trafficlab.common.config

uv run --locked pytest -q tests/unit/generation/models/test_registry.py::test_existing_families_declare_coordinate_kinds
FAILED: AttributeError: 'PoissonFamily' object has no attribute 'gene_coordinate_kinds'
```

GREEN and verification evidence:

```text
uv run --locked pytest -q tests/unit/common/test_config_schema.py tests/unit/common/test_config_validation.py tests/unit/generation/models/test_registry.py tests/unit/fitting/genetic/test_coordinates.py
286 passed

uv run --locked ruff check src/trafficlab/common/config.py src/trafficlab/generation/models src/trafficlab/fitting/genetic/coordinates.py
All checks passed!

uv run --locked pyright src/trafficlab/common/config.py src/trafficlab/generation/models/common.py src/trafficlab/generation/models/registry.py src/trafficlab/fitting/genetic/coordinates.py
0 errors, 0 warnings, 0 informations

uv run --locked pytest -q tests/unit/common tests/unit/generation/models/test_registry.py tests/unit/fitting/genetic
955 passed

uv run --locked pytest -q tests/integration/generation/test_model_pipeline.py -k 'poisson or markov or mmpp'
1 deselected (exit 0; its only test has a generic name)

uv run --locked pytest -q tests/integration/generation/test_model_pipeline.py
1 passed
```

Self-review: confirmed the registry remains exactly Poisson empirical, Markov
Renewal, and MMPP; `FamilyName` remains the same three literals; no similarity
weights or artifact-schema values changed; coordinate order and RNG primitives
are unchanged; the generic metadata length and kind checks reject malformed
family declarations; and `git diff --check` passed. No concerns remain.
