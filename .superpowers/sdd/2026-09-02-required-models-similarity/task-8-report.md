# Task 8 NHPP report [TASK-8-b32a2c75]

## RED/GREEN

RED was observed with the Task 8 command after adding the focused NHPP test:
`ModuleNotFoundError: trafficlab.generation.models.nhpp`. GREEN followed after
the deterministic family, strict payload, configuration, and registry work.

## Delivered files

- Added `src/trafficlab/generation/models/nhpp.py` and its unit tests.
- Registered `nhpp` in the family/config/bounds/payload/best-model paths.
- Added explicit non-default test configuration and independent scientific
  oracle coverage.
- Added `architecture/traffic_models/nhpp.md` and updated model/testing docs.

## Formula and oracle

With equal width `h = W/B`, fit uses `lambda_b = N_b/h`, excluding only the
conditioned `t=0` packet from `N_b`; `t=W` belongs to the final bin. The
scientific oracle independently checks bin means `lambda_b h`, integrated mean
`sum(lambda_b h)`, zero-bin absence, and active-bin mark frequencies for rates
`(2, 0, 4)`, width `400`, window `1200`, seeds `8101, 8209, 8303, 8411`, and
the predeclared 10%/0.03 tolerances.

## Commands and results

- `uv run --locked pytest -q tests/unit/generation/models/test_nhpp.py tests/scientific/generation/test_model_validation.py -k nhpp` RED: missing NHPP module.
- Focused NHPP/config/registry/scientific suite: `330 passed`.
- Required Small tier: `102 passed, 21 deselected`; Ruff passed; Pyright reported `0 errors`.
- `git diff --check` passed.

## Self-review and concerns

Reviewed rate endpoint allocation, zero rates, bin/global mark fallback, strict
payload discrimination, scalar PCG64 draw order, no mark draw on crossings, and
guard placement. No known in-scope concerns. NHPP is registered but is not
added to the default example configuration or default enabled-family list.
