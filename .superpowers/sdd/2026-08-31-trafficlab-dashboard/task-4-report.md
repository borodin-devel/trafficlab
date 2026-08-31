# [TASK-1-6f92d878] Task 4 Report

- Date: `2026-08-31`
- Commit: `82c896e96b7b0c25474d5bf2bb2a0dcbdc0b54bc`
- Scope: six concrete time-domain aspect calculators, registry replacement for the first six ordered aspects, and focused plot-contract coverage for endpoint, tiny, constant, reduction, immutability, and columnar-path behavior

## [STEP-1-77675a1d] Implemented surface

- Added `src/trafficlab_dashboard/aspects/time_domain.py` with `ThroughputAspect`, `PacketRateAspect`, `CumulativeBytesAspect`, `CumulativePacketsAspect`, `FrameSizeTimelineAspect`, and `IatTimelineAspect`.
- Reused Task 3 numerics directly: `choose_time_bin_width`, `shared_time_edges`, and deterministic `minmax_envelope`.
- Kept every calculation on `TrafficTrace` columns only: `timestamps`, `frame_lengths`, and `iats()`, without `to_events()` materialization.
- Replaced the first six `registry.py` placeholders with concrete time-domain aspect instances while preserving the approved registry order.
- Exported the new aspect classes from `src/trafficlab_dashboard/aspects/__init__.py`.
- Added `tests/trafficlab_dashboard/unit/test_time_domain.py` and extended `tests/trafficlab_dashboard/unit/test_aspect_registry.py` to pin formulas, metadata, registry replacement, endpoint semantics, tiny/constant behavior, reduction limits, immutability, and the columnar fast path.

## [STEP-2-2bea6ec8] Verified evidence

- Narrow predecessor verification:
  - `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 5m --kill-after 10s -- uv run --locked --all-extras pytest -q -n 0 tests/trafficlab_dashboard/unit/test_numerics.py tests/trafficlab_dashboard/unit/test_cache.py tests/trafficlab_dashboard/unit/test_aspect_registry.py`
  - Result: `21 passed in 1.31s`.
- RED:
  - `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 5m --kill-after 10s -- uv run --locked --all-extras pytest -q -n 0 tests/trafficlab_dashboard/unit/test_time_domain.py`
  - Result: expected collection failure with `ModuleNotFoundError: No module named 'trafficlab_dashboard.aspects.time_domain'`.
- GREEN:
  - `scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 5m --kill-after 10s -- uv run --locked --all-extras pytest -q -n 0 tests/trafficlab_dashboard/unit/test_time_domain.py tests/trafficlab_dashboard/unit/test_numerics.py tests/trafficlab_dashboard/unit/test_aspect_registry.py`
  - Result: `25 passed in 1.42s`.
- Static gates:
  - `uv run --locked --all-extras ruff format --check src/trafficlab_dashboard/aspects/time_domain.py src/trafficlab_dashboard/aspects/registry.py src/trafficlab_dashboard/aspects/__init__.py tests/trafficlab_dashboard/unit/test_time_domain.py tests/trafficlab_dashboard/unit/test_aspect_registry.py`
  - Result: `5 files already formatted`.
  - `uv run --locked --all-extras ruff check src/trafficlab_dashboard/aspects/time_domain.py src/trafficlab_dashboard/aspects/registry.py src/trafficlab_dashboard/aspects/__init__.py tests/trafficlab_dashboard/unit/test_time_domain.py tests/trafficlab_dashboard/unit/test_aspect_registry.py`
  - Result: `All checks passed!`
  - `uv run --locked --all-extras pyright src/trafficlab_dashboard/aspects/time_domain.py src/trafficlab_dashboard/aspects/registry.py src/trafficlab_dashboard/aspects/__init__.py tests/trafficlab_dashboard/unit/test_time_domain.py tests/trafficlab_dashboard/unit/test_aspect_registry.py`
  - Result: `0 errors, 0 warnings, 0 informations`.
- Commit creation:
  - `git commit -m "feat(dashboard): add time-domain aspects"`
  - Result: created `82c896e96b7b0c25474d5bf2bb2a0dcbdc0b54bc`.

## [STEP-3-03b6cd9d] Self-review notes

- Closed-window semantics are preserved by shared edges and NumPy histogram rules, so packets exactly at `W` stay in the final bin for throughput and packet rate.
- Throughput uses decimal Mbps (`/ 1_000_000.0`) while cumulative bytes use binary MiB (`/ 1_048_576.0`), matching the brief’s mixed-unit contract.
- Plot metadata is explicit: identifier, label, title, axis labels, units, sample counts, bin width, bin edges where relevant, and axis scales all live in immutable `LinePlotData`.
- Display reduction is calculation-independent: y-limits, totals, and sample counts come from the full sample; only returned display coordinates are reduced.
- The tests caught one bad dense-trace oracle during GREEN review; fixing it confirmed the final-bin endpoint packet was included rather than silently dropped.

## [STEP-4-1490b6d5] Deferred scope and concerns

- Later aspect groups remain scaffolded in `registry.py` and still raise `NotImplementedError`; Task 4 intentionally replaced only the six time-domain entries.
- No Critical or Important issues remain from self-review. The main follow-on risk is consistency reuse: Tasks 5 and 6 should continue consuming the same shared numerics and visible-label conventions rather than introducing alternate plotting metadata or directional terminology.
