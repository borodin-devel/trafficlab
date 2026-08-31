# TrafficLab Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the optional `trafficlab-dashboard` PySide6 desktop application for interactive, exportable visualization of one canonical TrafficLab run.

**Architecture:** Add `trafficlab_dashboard` to the existing root distribution and lockfile behind a `dashboard` extra. Load immutable run data through TrafficLab's production parsers, calculate registered aspects as pure NumPy data off the GUI thread, and render one cached aspect at a time through an embedded Matplotlib canvas with independent Reference/Generated visibility.

**Tech Stack:** CPython 3.12.3, uv, PySide6, Matplotlib QtAgg, NumPy, SciPy through TrafficLab, Pydantic artifacts, pytest, pytest-qt, Ruff, strict Pyright.

**Spec:** `docs/superpowers/specs/2026-08-31-trafficlab-dashboard-design.md`

## Global Constraints

- Keep one root `pyproject.toml`, root `README.md`, uv lockfile, and build distribution.
- Use executable name `trafficlab-dashboard` and import package `trafficlab_dashboard` under `src/trafficlab_dashboard/`.
- Put new tests under `tests/trafficlab_dashboard/`; do not relocate existing TrafficLab tests.
- PySide6 and Matplotlib remain optional runtime dependencies in the `dashboard` extra; release verification installs all extras.
- The dashboard is read-only and never becomes a capture, fit, generation, or comparison stage.
- Reuse TrafficLab artifact parsing, normalization, alignment, ACF, similarity, model, and configuration semantics; do not add a second PCAPNG or artifact codec.
- Display `outbound` as uplink and `inbound` as downlink without changing persisted schemas.
- Qt widgets and Matplotlib canvases mutate only on the GUI thread; workers return immutable numerical data tagged by generation token.
- Scientific calculations use complete samples; decimation changes rendering only.
- Reference and Generated overlays use identical bins, lags, scales, units, and axes.
- Every public interface is typed; unknown/malformed input fails with the exact artifact and corrective action.
- No Node.js application, web server, database, telemetry, live capture, artifact editing, or model-specific first-release view.

---

### Task 1: [TASK-1-9af6bc33] Root packaging and launchable desktop shell

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/trafficlab_dashboard/__init__.py`
- Create: `src/trafficlab_dashboard/__main__.py`
- Create: `src/trafficlab_dashboard/app.py`
- Create: `tests/trafficlab_dashboard/conftest.py`
- Create: `tests/trafficlab_dashboard/unit/test_package.py`
- Create: `tests/trafficlab_dashboard/integration/test_app_shell.py`

**Interfaces:**
- Produces: `trafficlab_dashboard.app.main(argv: Sequence[str] | None = None) -> int`
- Produces: root console script `trafficlab-dashboard`
- Produces: dashboard test environment with `QT_QPA_PLATFORM=offscreen`

- [x] **[STEP-1-fe8cd3d6] Write packaging and shell tests first**

```python
def test_dashboard_distribution_and_entrypoint_are_declared() -> None:
    metadata = importlib.metadata.metadata("trafficlab")
    scripts = {entry.name: entry.value for entry in importlib.metadata.entry_points(group="console_scripts")}
    assert SpecifierSet(str(metadata["Requires-Python"])) == SpecifierSet(">=3.12,<3.13")
    assert scripts["trafficlab-dashboard"] == "trafficlab_dashboard.app:main"


def test_dashboard_shell_accepts_run_path_and_shows_one_window(qtbot, tmp_path: Path) -> None:
    window = create_window(tmp_path)
    qtbot.addWidget(window)
    window.show()
    assert window.windowTitle() == "TrafficLab Dashboard"
    assert window.centralWidget() is not None
```

Set `QT_QPA_PLATFORM=offscreen` at module import time in dashboard `conftest.py`, before any PySide import.

- [x] **[STEP-2-6785826c] Run the new tests and record RED**

Run:

```bash
uv run --locked pytest -q \
  tests/trafficlab_dashboard/unit/test_package.py \
  tests/trafficlab_dashboard/integration/test_app_shell.py
```

Expected: collection/import failure because `trafficlab_dashboard` and dashboard dependencies do not exist.

- [x] **[STEP-3-1082b2fa] Add the optional extra, build packages, entry point, and minimal shell**

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
dashboard = [
    "matplotlib>=3.10,<4",
    "pyside6>=6.8,<7",
]

[project.scripts]
trafficlab = "trafficlab.cli:entrypoint"
trafficlab-dashboard = "trafficlab_dashboard.app:main"

[tool.hatch.build.targets.wheel]
packages = ["src/trafficlab", "src/trafficlab_dashboard"]
```

Add `"pytest-qt>=4.4,<5"` to the existing `dev` dependency group. Extend strict Pyright's `include` with `src/trafficlab_dashboard` and `tests/trafficlab_dashboard`. Extend coverage source ownership with `trafficlab_dashboard` only after dashboard tests exist in this task.

Implement the initial shell:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trafficlab-dashboard")
    parser.add_argument("run_directory", nargs="?", type=Path)
    return parser


def create_window(initial_path: Path | None = None) -> QMainWindow:
    window = QMainWindow()
    window.setWindowTitle("TrafficLab Dashboard")
    window.resize(1200, 760)
    window.setCentralWidget(QWidget())
    window.setProperty("initial_run_directory", initial_path)
    return window


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = create_window(arguments.run_directory)
    window.show()
    return application.exec()
```

- [x] **[STEP-4-8ee62759] Lock dependencies and verify GREEN**

Run:

```bash
uv lock
uv sync --locked --all-groups --all-extras
QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q tests/trafficlab_dashboard
uv run --all-extras ruff check pyproject.toml src/trafficlab_dashboard tests/trafficlab_dashboard
uv run --all-extras pyright src/trafficlab_dashboard tests/trafficlab_dashboard
```

Expected: dashboard tests pass, Ruff clean, Pyright zero errors.

- [x] **[STEP-5-f9e117cf] Self-review package isolation and root CLI compatibility**

Run existing package and CLI smoke tests in addition to dashboard tests:

```bash
uv run --all-extras pytest -q tests/unit/pipeline/test_package.py tests/unit/pipeline/test_cli.py tests/trafficlab_dashboard
```

Confirm `uv run --locked trafficlab --version` remains independent of Qt imports and `uv run --extra dashboard trafficlab-dashboard --help` exits zero without creating a window.

- [x] **[STEP-6-60e84675] Commit Task 1**

```bash
git add pyproject.toml uv.lock src/trafficlab_dashboard tests/trafficlab_dashboard
git commit -m "feat(dashboard): add desktop package shell"
```

### Task 2: [TASK-2-03ffed89] Strict run loading and immutable dashboard data

**Files:**
- Modify: `src/trafficlab/fitting/genetic/checkpoint/history.py`
- Modify: `src/trafficlab/fitting/genetic/checkpoint/__init__.py`
- Modify: `tests/unit/fitting/genetic/checkpoint/test_history.py`
- Create: `src/trafficlab_dashboard/run_data.py`
- Create: `src/trafficlab_dashboard/run_loader.py`
- Create: `tests/trafficlab_dashboard/unit/test_run_loader.py`
- Create: `tests/trafficlab_dashboard/support/dashboard_fixtures.py`

**Interfaces:**
- Produces: `load_history_csv(path: Path, family_names: frozenset[FamilyName]) -> tuple[HistoryRow, ...]` in core TrafficLab
- Produces: immutable `ArtifactAvailability`, `ArtifactIdentities`, and `DashboardRun`
- Produces: `load_dashboard_run(directory: Path) -> DashboardRun`
- Consumes: `load_capture_metadata`, `read_pcapng`, `normalize_reference`, `align_generated`, `load_comparison_result`, `load_best_model`, and `load_experiment`

- [x] **[STEP-7-17ef76e3] Write failing core-history and run-loader tests**

```python
def test_load_dashboard_run_normalizes_and_aligns_required_traces(tmp_path: Path) -> None:
    run = write_complete_dashboard_run(tmp_path, reference_times=(10.0, 11.0, 13.0), generated_times=(20.0, 21.0, 24.0))
    loaded = load_dashboard_run(run)
    assert loaded.window == 3.0
    assert loaded.reference.timestamps.tolist() == [0.0, 1.0, 3.0]
    assert loaded.generated.timestamps.tolist() == [0.0, 1.0]
    assert loaded.reference_packet_count == 3
    assert loaded.generated_packet_count == 2


def test_missing_optional_artifact_disables_only_its_aspect(tmp_path: Path) -> None:
    run = write_complete_dashboard_run(tmp_path)
    (run / "similarity.json").unlink()
    loaded = load_dashboard_run(run)
    assert loaded.similarity is None
    assert loaded.unavailable["similarity_scores"] == "similarity.json is missing"
```

Add core tests proving public history loading rejects a malformed header, unknown family, and noncanonical float and returns immutable `HistoryRow` values for valid CSV.

- [x] **[STEP-8-87506b72] Run loader tests and record RED**

Run:

```bash
uv run --all-extras pytest -q \
  tests/trafficlab_dashboard/unit/test_run_loader.py \
  tests/unit/fitting -k history
```

Expected: imports/functions missing.

- [x] **[STEP-9-f0575be4] Implement public history loading and DashboardRun**

Core wrapper:

```python
def load_history_csv(path: Path, family_names: frozenset[FamilyName]) -> tuple[HistoryRow, ...]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read history artifact {path}: {error}",
            corrective_action="verify ga_history.csv exists and is readable",
        ) from error
    try:
        return _parse_history_csv(content, family_names)
    except ValueError as error:
        raise TrafficlabError(
            f"invalid history artifact {path}: {error}",
            corrective_action="rerun fitting to publish canonical ga_history.csv",
        ) from error
```

Dashboard data:

```python
@dataclass(frozen=True, slots=True)
class ArtifactIdentities:
    reference_sha256: str
    generated_sha256: str
    capture_sha256: str
    similarity_sha256: str | None
    best_model_sha256: str | None
    history_sha256: str | None


@dataclass(frozen=True, slots=True)
class DashboardRun:
    directory: Path
    identities: ArtifactIdentities
    metadata: CaptureMetadata
    reference: TrafficTrace
    generated: TrafficTrace
    window: float
    similarity: ComparisonResult | None
    best_model: BestModel | None
    history: tuple[HistoryRow, ...] | None
    experiment: ExperimentConfig | None
    unavailable: Mapping[str, str]
```

Read exact bytes once where byte-based parsers require them. Hash exact artifacts. Required failures raise `TrafficlabError` naming the artifact. Optional failures populate `unavailable` and leave the loaded run usable.

- [x] **[STEP-10-9b952413] Verify loader GREEN and immutability**

Run the RED command again, then:

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_run_loader.py tests/unit/fitting -k history
uv run --all-extras pyright src/trafficlab_dashboard/run_data.py src/trafficlab_dashboard/run_loader.py
```

Add assertions that every trace array is non-writable, `unavailable` is a `MappingProxyType`, and a failed second load does not mutate a previously returned `DashboardRun`.

- [x] **[STEP-11-ec921485] Run checked-artifact integration loading**

Use a copied checked canonical run from `examples/scientific_stack/example_run_artifacts/` or the smallest complete checked run available in `examples/`. Do not read ignored `runs/` in ordinary tests.

```bash
QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q \
  tests/trafficlab_dashboard/unit/test_run_loader.py \
  tests/trafficlab_dashboard/integration/test_run_loading.py
```

Expected: complete run loads; malformed required artifact rejects; missing optional artifact disables only dependent aspects.

- [x] **[STEP-12-759b453f] Commit Task 2**

```bash
git add src/trafficlab/fitting/genetic/checkpoint/history.py src/trafficlab_dashboard tests/unit/fitting tests/trafficlab_dashboard
git commit -m "feat(dashboard): load canonical run data"
```

### Task 3: [TASK-3-74f8dd1f] Aspect protocol, plot-data records, shared binning, cache, and display reduction

**Files:**
- Create: `src/trafficlab_dashboard/aspects/__init__.py`
- Create: `src/trafficlab_dashboard/aspects/base.py`
- Create: `src/trafficlab_dashboard/aspects/numerics.py`
- Create: `src/trafficlab_dashboard/aspects/registry.py`
- Create: `src/trafficlab_dashboard/cache.py`
- Create: `tests/trafficlab_dashboard/unit/test_aspect_registry.py`
- Create: `tests/trafficlab_dashboard/unit/test_numerics.py`
- Create: `tests/trafficlab_dashboard/unit/test_cache.py`

**Interfaces:**
- Produces: `TraceVisibility`, `CalculationSettings`, `LineSeries`, `LinePlotData`, `HistogramSeries`, `HistogramPlotData`, `BarSeries`, `BarPlotData`, `HexbinPlotData`, and `PlotData`
- Produces: `Aspect` protocol with `calculate(run, settings) -> PlotData`
- Produces: `choose_time_bin_width`, `shared_time_edges`, `shared_histogram_edges`, `ecdf_points`, `minmax_envelope`
- Produces: `AspectCache.get/put/clear`

- [x] **[STEP-13-029c75a9] Write failing numerical invariant, protocol, registry, and cache tests**

```python
def test_choose_time_bin_width_uses_125_sequence_and_target_range() -> None:
    width = choose_time_bin_width(window=53.975692, minimum_bins=500, maximum_bins=1500)
    assert width == 0.05
    assert 500 <= math.ceil(53.975692 / width) <= 1500


def test_minmax_envelope_preserves_bucket_extrema_and_endpoints() -> None:
    x = np.arange(8, dtype=np.float64)
    y = np.array([0, 5, 1, 4, 2, 9, 3, 8], dtype=np.float64)
    reduced = minmax_envelope(x, y, maximum_points=4)
    assert reduced.x[[0, -1]].tolist() == [0.0, 7.0]
    assert set(reduced.y.tolist()) >= {0.0, 9.0}


def test_visibility_change_does_not_change_cache_key(run) -> None:
    settings = CalculationSettings.default()
    assert AspectCache.key(run, "throughput", settings) == AspectCache.key(run, "throughput", settings)
```

- [x] **[STEP-14-9cf8e00a] Run Task 3 tests and record RED**

```bash
uv run --all-extras pytest -q \
  tests/trafficlab_dashboard/unit/test_aspect_registry.py \
  tests/trafficlab_dashboard/unit/test_numerics.py \
  tests/trafficlab_dashboard/unit/test_cache.py
```

Expected: missing package modules.

- [x] **[STEP-15-ebffd09c] Implement immutable plot records and Aspect protocol**

```python
@dataclass(frozen=True, slots=True)
class CalculationSettings:
    automatic_bin_minimum: int = 500
    automatic_bin_maximum: int = 1500
    acf_lags: tuple[int, ...] = tuple(range(1, 51))
    maximum_display_points: int = 20_000


@runtime_checkable
class Aspect(Protocol):
    identifier: str
    label: str
    category: str
    trace_controls: bool

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> PlotData: ...
```

Every NumPy member is copied to an owned C-contiguous array and marked non-writable in `__post_init__`. Store labels/units/sample counts/bin width/lag range in plot-data metadata so renderers never infer scientific facts.

- [x] **[STEP-16-72f68126] Implement shared numerics and cache minimally**

`choose_time_bin_width` enumerates positive `1, 2, 5 × 10ⁿ` widths and chooses the smallest width whose bin count is at most the maximum while preferring counts at least the minimum. `shared_time_edges` includes `0` and `W` and never places an edge above `W` except the histogram's right boundary required to include `W`.

`ecdf_points` sorts the full sample with stable ties, produces cumulative `1..n / n`, and reduces only returned display coordinates while retaining the first and last step. `shared_histogram_edges` always consumes both loaded traces regardless of visibility. `minmax_envelope` emits chronological min/max points per bucket plus endpoints.

Cache key:

```python
type CacheKey = tuple[ArtifactIdentities, str, CalculationSettings]


class AspectCache:
    def get(self, key: CacheKey) -> PlotData | None: ...
    def put(self, key: CacheKey, value: PlotData) -> None: ...
    def clear(self) -> None: ...
```

- [x] **[STEP-17-05f1b1b0] Verify numerical GREEN and property invariants**

Run Task 3 tests. Add Hypothesis coverage for finite ordered traces, constant samples, ties, zero IATs, very small samples, and random display limits. Assert reduction preserves order/endpoints/extrema, ECDF monotonicity, shared edges, immutability, and maximum display count.

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_numerics.py tests/trafficlab_dashboard/unit/test_cache.py
```

- [x] **[STEP-18-954eac21] Commit Task 3**

```bash
git add src/trafficlab_dashboard/aspects src/trafficlab_dashboard/cache.py tests/trafficlab_dashboard/unit
git commit -m "feat(dashboard): add aspect data contracts"
```

### Task 4: [TASK-4-ea9c48bc] Time-domain aspects

**Files:**
- Create: `src/trafficlab_dashboard/aspects/time_domain.py`
- Create: `tests/trafficlab_dashboard/unit/test_time_domain.py`

**Interfaces:**
- Produces Aspect implementations: `ThroughputAspect`, `PacketRateAspect`, `CumulativeBytesAspect`, `CumulativePacketsAspect`, `FrameSizeTimelineAspect`, `IatTimelineAspect`
- Consumes shared time edges and min/max display envelope from Task 3

- [x] **[STEP-19-e2499cb7] Write failing hand-calculated time-domain tests**

Use a reference trace at times `[0, 1, 2]`, lengths `[100, 200, 300]`, and a generated trace at `[0, 1.5, 2]`, lengths `[50, 100, 150]` over `W=2`.

```python
def test_throughput_uses_shared_edges_and_mbps(run) -> None:
    data = ThroughputAspect().calculate(run, settings_with_bin_width(1.0))
    assert data.bin_width == 1.0
    assert data.series[0].y.tolist() == [0.0008, 0.004]
    assert data.series[1].y.tolist() == [0.0004, 0.002]


def test_cumulative_packets_include_both_window_endpoints(run) -> None:
    data = CumulativePacketsAspect().calculate(run, CalculationSettings.default())
    assert data.series[0].y.tolist() == [1.0, 2.0, 3.0]
```

Add direct tests for packet rate, cumulative MiB, frame-size timeline, IAT timeline, sample counts, units, and display-only envelope reduction.

- [x] **[STEP-20-b0b877d0] Run time-domain tests and record RED**

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_time_domain.py
```

Expected: aspect classes missing.

- [x] **[STEP-21-c7bf4746] Implement time-domain calculations**

Throughput calculation:

```python
def throughput_mbps(trace: TrafficTrace, edges: NDArray[np.float64]) -> NDArray[np.float64]:
    bytes_per_bin, _ = np.histogram(trace.timestamps, bins=edges, weights=trace.frame_lengths)
    widths = np.diff(edges)
    return np.asarray(bytes_per_bin * 8.0 / widths / 1_000_000.0, dtype=np.float64)
```

Packet rate uses unweighted histogram counts divided by widths. Cumulative aspects use exact trace timestamps and cumulative full-sample values. IAT timeline uses `timestamps[1:]` and `trace.iats()`, retaining zeros. Timeline display applies `minmax_envelope`; cumulative display keeps monotone representative points and endpoints.

- [x] **[STEP-22-5eb616fc] Verify time-domain GREEN and overlay invariants**

Run the focused tests. Assert both datasets use byte-identical edges/x coordinates for binned aspects, full samples determine totals, and toggling visibility is absent from calculations.

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_time_domain.py tests/trafficlab_dashboard/unit/test_numerics.py
```

- [x] **[STEP-23-29b1266a] Run strict static checks for Task 4**

```bash
uv run --all-extras ruff format --check src/trafficlab_dashboard/aspects/time_domain.py tests/trafficlab_dashboard/unit/test_time_domain.py
uv run --all-extras ruff check src/trafficlab_dashboard/aspects/time_domain.py tests/trafficlab_dashboard/unit/test_time_domain.py
uv run --all-extras pyright src/trafficlab_dashboard/aspects/time_domain.py tests/trafficlab_dashboard/unit/test_time_domain.py
```

- [x] **[STEP-24-b7d5b3c9] Commit Task 4**

```bash
git add src/trafficlab_dashboard/aspects/time_domain.py tests/trafficlab_dashboard/unit/test_time_domain.py
git commit -m "feat(dashboard): add time-domain aspects"
```

### Task 5: [TASK-5-9d9b782a] Distribution aspects

**Files:**
- Create: `src/trafficlab_dashboard/aspects/distributions.py`
- Create: `tests/trafficlab_dashboard/unit/test_distributions.py`

**Interfaces:**
- Produces: `FrameSizeEcdfAspect`, `IatEcdfAspect`, `FrameSizeHistogramAspect`, `IatHistogramAspect`, `ThroughputEcdfAspect`
- Consumes: `ecdf_points`, `shared_histogram_edges`, `shared_time_edges`, and time-domain throughput

- [x] **[STEP-25-c0d2835f] Write failing distribution tests with literal oracles**

```python
def test_frame_size_ecdf_retains_ties(run) -> None:
    data = FrameSizeEcdfAspect().calculate(run, CalculationSettings.default())
    reference = data.series[0]
    assert reference.x.tolist() == [100.0, 200.0, 300.0]
    assert reference.y.tolist() == [1 / 3, 2 / 3, 1.0]


def test_iat_histogram_annotates_zeros_and_uses_common_log_edges(run_with_zero_iats) -> None:
    data = IatHistogramAspect().calculate(run_with_zero_iats, CalculationSettings.default())
    assert data.series[0].zero_count == 1
    assert data.series[1].zero_count == 2
    assert np.array_equal(data.series[0].edges, data.series[1].edges)
    assert np.all(data.series[0].edges > 0)
```

Add normalized-density integral checks, combined-data edge checks independent of visibility, constant-sample fallback, and throughput ECDF shared-bin tests.

- [x] **[STEP-26-59dbd2e5] Run distribution tests and record RED**

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_distributions.py
```

Expected: distribution aspects missing.

- [x] **[STEP-27-2edce273] Implement ECDF and histogram aspects**

Frame-size histograms call `np.histogram(..., density=True)` with common edges from combined reference/generated lengths. IAT histograms split exact zeros from positive samples, create common logarithmic edges with `np.geomspace`, and store zero annotations. Throughput ECDF first calculates both full binned throughput arrays using common time edges, then uses `ecdf_points` independently.

- [x] **[STEP-28-be63ffe2] Verify distribution GREEN and scientific boundaries**

Run focused tests and existing TrafficLab statistics tests:

```bash
uv run --all-extras pytest -q \
  tests/trafficlab_dashboard/unit/test_distributions.py \
  tests/unit/common/test_statistics.py
```

Expected: all pass; dashboard never uses SciPy p-values or drops zero IATs.

- [x] **[STEP-29-c964cf74] Run Task 5 static checks**

```bash
uv run --all-extras ruff format --check src/trafficlab_dashboard/aspects/distributions.py tests/trafficlab_dashboard/unit/test_distributions.py
uv run --all-extras ruff check src/trafficlab_dashboard/aspects/distributions.py tests/trafficlab_dashboard/unit/test_distributions.py
uv run --all-extras pyright src/trafficlab_dashboard/aspects/distributions.py tests/trafficlab_dashboard/unit/test_distributions.py
```

- [x] **[STEP-30-a9a34691] Commit Task 5**

```bash
git add src/trafficlab_dashboard/aspects/distributions.py tests/trafficlab_dashboard/unit/test_distributions.py
git commit -m "feat(dashboard): add distribution aspects"
```

### Task 6: [TASK-6-04922990] Direction and dependence aspects

**Files:**
- Create: `src/trafficlab_dashboard/aspects/direction.py`
- Create: `src/trafficlab_dashboard/aspects/dependence.py`
- Create: `tests/trafficlab_dashboard/unit/test_direction.py`
- Create: `tests/trafficlab_dashboard/unit/test_dependence.py`

**Interfaces:**
- Produces: `DirectionalThroughputAspect`, `DirectionalPacketRateAspect`, `DirectionBalanceAspect`
- Produces: `FrameSizeAutocorrelationAspect`, `IatAutocorrelationAspect`, `FrameSizeIatHexbinAspect`
- Consumes: `Direction`, `TrafficTrace.direction_mask`, TrafficLab ACF estimator, and Task 3 plot records

- [x] **[STEP-31-dea47607] Write failing direction and dependence tests**

```python
def test_direction_balance_uses_uplink_downlink_labels_and_packet_byte_shares(run) -> None:
    data = DirectionBalanceAspect().calculate(run, CalculationSettings.default())
    assert data.categories == ("Uplink packets", "Downlink packets", "Uplink bytes", "Downlink bytes")
    assert data.series[0].values.tolist() == [2 / 3, 1 / 3, 400 / 600, 200 / 600]


def test_frame_size_acf_uses_trafficlab_estimator(run) -> None:
    # The fixture's Reference frame lengths are exactly [1, 2, 1].
    data = FrameSizeAutocorrelationAspect().calculate(run, settings_with_lags((1, 2)))
    assert data.series[0].y.tolist() == pytest.approx([-2 / 3, 1 / 6])


def test_large_size_iat_relation_uses_hexbin_full_samples(run_large) -> None:
    data = FrameSizeIatHexbinAspect().calculate(run_large, CalculationSettings.default())
    assert len(data.reference_x) == len(run_large.reference) - 1
    assert data.render_mode == "hexbin"
```

- [x] **[STEP-32-47ad27bd] Run Task 6 tests and record RED**

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_direction.py tests/trafficlab_dashboard/unit/test_dependence.py
```

Expected: aspect modules/classes missing.

- [x] **[STEP-33-30aa172b] Implement direction aspects with shared bins**

Map internal `Direction.OUTBOUND` to display label Uplink and `Direction.INBOUND` to Downlink. Directional throughput and packet rate produce four series when both datasets render: Reference/Generated × Uplink/Downlink. Dataset remains color metadata; direction remains dash-style metadata. Direction balance stores packet and byte proportions independently for each dataset.

- [x] **[STEP-34-aece8288] Implement dependence aspects using full arrays**

Call TrafficLab's public ACF function directly for each requested lag; do not copy the estimator. Represent a lag unavailable in either trace as masked/unavailable metadata, not zero. Pair `frame_lengths[1:]` with `iats()` for frame-size/IAT relation. Store complete x/y arrays; renderer chooses raw scatter below the threshold and hexbin above it.

- [x] **[STEP-35-d9c9b21e] Verify Task 6 GREEN and terminology**

```bash
uv run --all-extras pytest -q \
  tests/trafficlab_dashboard/unit/test_direction.py \
  tests/trafficlab_dashboard/unit/test_dependence.py \
  tests/unit/comparison/test_metrics.py
```

Search dashboard UI strings and assert no visible `inbound` or `outbound` labels remain; internal enum references are allowed.

- [x] **[STEP-36-40c0e3a4] Commit Task 6**

```bash
git add src/trafficlab_dashboard/aspects/direction.py src/trafficlab_dashboard/aspects/dependence.py tests/trafficlab_dashboard/unit
git commit -m "feat(dashboard): add direction and dependence views"
```

### Task 7: [TASK-7-73951906] Pair-level aspects and complete registry

**Files:**
- Create: `src/trafficlab_dashboard/aspects/run_level.py`
- Modify: `src/trafficlab_dashboard/aspects/registry.py`
- Create: `tests/trafficlab_dashboard/unit/test_run_level.py`
- Modify: `tests/trafficlab_dashboard/unit/test_aspect_registry.py`

**Interfaces:**
- Produces: `SimilarityScoresAspect`, `MultiscaleDiscrepancyAspect`, `GaFitnessHistoryAspect`
- Produces: `ASPECTS: tuple[Aspect, ...]`, `aspect_by_id(identifier: str) -> Aspect`
- Pair/run-level aspects declare `trace_controls = False`

- [x] **[STEP-37-37c7d332] Write failing canonical-artifact and registry tests**

```python
def test_similarity_scores_use_stored_values_without_recomputation(loaded_run) -> None:
    data = SimilarityScoresAspect().calculate(loaded_run, CalculationSettings.default())
    assert data.categories == ("Frame-size KS", "IAT KS", "Autocorrelation", "Multiscale", "Aggregate")
    assert data.values.tolist() == pytest.approx([0.2, 0.4, 0.6, 0.8, 0.6])


def test_registry_order_is_complete_and_pair_aspects_disable_trace_controls() -> None:
    assert [aspect.identifier for aspect in ASPECTS] == EXPECTED_ASPECT_IDS
    assert all(not aspect.trace_controls for aspect in ASPECTS[-3:])
```

Build literal `ComparisonResult` and `HistoryRow` fixtures through public schemas, not ad-hoc dictionaries.

- [x] **[STEP-38-a778cea3] Run run-level tests and record RED**

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_run_level.py tests/trafficlab_dashboard/unit/test_aspect_registry.py
```

Expected: run-level aspects and complete registry missing.

- [x] **[STEP-39-d07ffe35] Implement canonical similarity and GA-history projections**

Read all similarity component scores, aggregate, configured method order, and multiscale per-scale packet/byte discrepancies from the validated `ComparisonResult`. Never call `compare_traces` in dashboard code. Group GA `HistoryRow` records into ordered Reference-independent line series for each family and overall; x is generation and y is best fitness.

If the required optional artifact is absent, the registry retains the aspect while `DashboardRun.unavailable` disables it with the exact reason.

- [x] **[STEP-40-e6e2d056] Complete the explicit ordered registry**

Declare all 20 first-release identifiers verbatim:

```python
EXPECTED_ASPECT_IDS = (
    "throughput",
    "packet_rate",
    "cumulative_bytes",
    "cumulative_packets",
    "frame_size_timeline",
    "iat_timeline",
    "frame_size_ecdf",
    "iat_ecdf",
    "frame_size_histogram",
    "iat_histogram",
    "throughput_ecdf",
    "directional_throughput",
    "directional_packet_rate",
    "direction_balance",
    "frame_size_acf",
    "iat_acf",
    "frame_size_iat_hexbin",
    "similarity_scores",
    "multiscale_discrepancy",
    "ga_fitness_history",
)
```

`aspect_by_id` raises `KeyError(identifier)` for unknown identifiers; UI catches this only as an internal defect.

- [x] **[STEP-41-91e5e0c0] Verify Task 7 GREEN and optional availability**

```bash
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit/test_run_level.py tests/trafficlab_dashboard/unit/test_aspect_registry.py tests/trafficlab_dashboard/unit/test_run_loader.py
```

Assert missing similarity disables two aspects, missing history disables GA history, and every trace aspect remains usable.

- [x] **[STEP-42-08ecb17a] Commit Task 7**

```bash
git add src/trafficlab_dashboard/aspects tests/trafficlab_dashboard/unit
git commit -m "feat(dashboard): add run-level aspects"
```

### Task 8: [TASK-8-e32a0497] Matplotlib rendering, pan/zoom/reset, and export

**Files:**
- Create: `src/trafficlab_dashboard/plotting/__init__.py`
- Create: `src/trafficlab_dashboard/plotting/canvas.py`
- Create: `src/trafficlab_dashboard/plotting/interaction.py`
- Create: `src/trafficlab_dashboard/plotting/export.py`
- Create: `tests/trafficlab_dashboard/unit/test_interaction.py`
- Create: `tests/trafficlab_dashboard/integration/test_export.py`
- Create: `tests/trafficlab_dashboard/integration/test_canvas.py`

**Interfaces:**
- Produces: `DashboardCanvas.render(data: PlotData, visibility: TraceVisibility) -> None`
- Produces: `DashboardCanvas.reset_view() -> None`
- Produces: pure `zoom_limits`, `pan_limits`
- Produces: `export_figure(figure: Figure, destination: Path, format: Literal["png", "svg"]) -> None`

- [x] **[STEP-43-7feb81ec] Write failing render, interaction, and export tests**

```python
def test_zoom_is_centered_at_cursor_and_shift_limits_x_only() -> None:
    limits = zoom_limits(xlim=(0, 10), ylim=(0, 100), cursor=(2, 20), factor=0.5, axes="x")
    assert limits.x == pytest.approx((1, 6))
    assert limits.y == (0, 100)


def test_export_svg_contains_current_title_and_visible_series(tmp_path: Path, canvas) -> None:
    canvas.render(line_data(), TraceVisibility(reference=True, generated=False))
    destination = tmp_path / "plot.svg"
    export_figure(canvas.figure, destination, format="svg")
    root = ElementTree.parse(destination).getroot()
    text = destination.read_text(encoding="utf-8")
    assert root.tag.endswith("svg")
    assert "Throughput" in text
    assert "Reference" in text
    assert "Generated" not in text
```

- [x] **[STEP-44-e18f144f] Run plotting tests and record RED**

```bash
QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q \
  tests/trafficlab_dashboard/unit/test_interaction.py \
  tests/trafficlab_dashboard/integration/test_canvas.py \
  tests/trafficlab_dashboard/integration/test_export.py
```

Expected: plotting modules missing.

- [x] **[STEP-45-968824d9] Implement plot renderers and fixed visual encoding**

`DashboardCanvas` owns one `Figure`, one `Axes`, and one `FigureCanvasQTAgg`. It clears axes only on aspect render, records calculated complete bounds, and renders plot-data unions by type. Dataset colors are blue/orange; direction styles are solid/dashed. Pair-level bars ignore visibility. Every render sets title, labels, units, legend, grid, and complete-data bounds from metadata.

Set `matplotlib.rcParams["svg.fonttype"] = "none"` in the dashboard plotting initialization so exported SVG titles and legend labels remain accessible text for both users and integration tests.

- [x] **[STEP-46-0f817563] Implement pure pan/zoom and connect mouse events**

`zoom_limits` uses cursor-relative affine scaling and rejects nonfinite cursor/limits. Wheel factor is `0.8` for zoom-in and `1.25` for zoom-out. `Shift` selects x only, `Control` selects y only, otherwise both. Left press stores data coordinates and limits; motion applies `pan_limits`; release clears drag state. Double-click calls reset. Ignore events outside axes or while axes lack finite limits.

- [x] **[STEP-47-788645f5] Implement exclusive PNG/SVG export and verify GREEN**

Validate suffix/format agreement, write to a same-directory temporary file, ensure nonempty output, and publish with no overwrite so an existing research figure is never replaced silently. Surface `OSError` with destination and corrective action. Run plotting tests; assert PNG signature/dimensions via Pillow supplied by Matplotlib and parse SVG XML/text.

- [x] **[STEP-48-e8395864] Commit Task 8**

```bash
git add src/trafficlab_dashboard/plotting tests/trafficlab_dashboard/unit/test_interaction.py tests/trafficlab_dashboard/integration
git commit -m "feat(dashboard): add interactive plotting"
```

### Task 9: [TASK-9-942d3401] PySide6 window, workers, state machine, and complete CLI

**Files:**
- Modify: `src/trafficlab_dashboard/app.py`
- Create: `src/trafficlab_dashboard/window.py`
- Create: `src/trafficlab_dashboard/workers.py`
- Create: `src/trafficlab_dashboard/state.py`
- Modify: `tests/trafficlab_dashboard/integration/test_app_shell.py`
- Create: `tests/trafficlab_dashboard/integration/test_window.py`
- Create: `tests/trafficlab_dashboard/integration/test_worker_state.py`

**Interfaces:**
- Produces: `DashboardWindow.open_run(path: Path) -> None`
- Produces: immutable `DashboardState` and reducer-style state transitions
- Produces: `LoadRunWorker`, `CalculateAspectWorker` carrying generation token
- Consumes: `load_dashboard_run`, `ASPECTS`, `AspectCache`, `DashboardCanvas`, `export_figure`

- [x] **[STEP-49-9f3e68f2] Write failing window and worker-state tests**

```python
def test_trace_buttons_prevent_empty_trace_plot(qtbot, loaded_window) -> None:
    loaded_window.reference_button.setChecked(False)
    qtbot.mouseClick(loaded_window.generated_button, Qt.MouseButton.LeftButton)
    assert loaded_window.generated_button.isChecked()
    assert "At least one trace" in loaded_window.status_bar.currentMessage()


def test_pair_aspect_disables_trace_buttons(qtbot, loaded_window) -> None:
    select_aspect(loaded_window, "similarity_scores")
    assert not loaded_window.reference_button.isEnabled()
    assert not loaded_window.generated_button.isEnabled()


def test_stale_worker_result_cannot_replace_newer_aspect(window) -> None:
    old = calculation_result(token=1, aspect="throughput")
    window.state = replace(window.state, generation=2, requested_aspect="iat_ecdf")
    window.accept_calculation(old)
    assert window.canvas.current_aspect != "throughput"
```

- [x] **[STEP-50-fff9e117] Run window tests and record RED**

```bash
QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q \
  tests/trafficlab_dashboard/integration/test_app_shell.py \
  tests/trafficlab_dashboard/integration/test_window.py \
  tests/trafficlab_dashboard/integration/test_worker_state.py
```

Expected: window/state/worker APIs missing.

- [x] **[STEP-51-88fed181] Implement immutable state and generation-token workers**

```python
@dataclass(frozen=True, slots=True)
class DashboardState:
    generation: int = 0
    run: DashboardRun | None = None
    selected_aspect: str = "throughput"
    visibility: TraceVisibility = TraceVisibility(True, True)
    loading_run: bool = False
    calculating: bool = False
```

Workers emit typed success/failure objects containing their request token. Worker functions do no Qt or Matplotlib mutation. Window slots compare token with current generation before accepting. Opening a run, selecting an aspect, and invalidating current work increment generation.

- [x] **[STEP-52-11f7803b] Build the approved single-window controls and behavior**

Create the horizontal control row in exact order: Open Run, Aspect combo, Reference toggle, Generated toggle, Reset, Export. Canvas fills remaining central space; status bar shows run name, packet counts, `W`, and progress. Both trace toggles default on. Trace aspects reject the last-off action. Pair aspects disable both. Aspect change resets viewport; visibility redraw preserves viewport and uses cache. Failed load/calculation leaves previous plot. Optional unavailable aspects remain listed but disabled with tooltip/status reason.

- [x] **[STEP-53-66f0b0ad] Complete CLI, chooser, dialogs, and export flow**

If CLI path is present, schedule load after the window is shown. Without path, call `QFileDialog.getExistingDirectory`. Open Run repeats chooser. Error dialogs contain artifact path and corrective action. Export dialog offers `.png` and `.svg`, then calls `export_figure`. Add a nonmodal progress overlay that does not replace the current canvas.

- [x] **[STEP-54-f894250e] Verify GUI GREEN and cached redraw behavior**

```bash
QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q tests/trafficlab_dashboard/integration
uv run --all-extras pytest -q tests/trafficlab_dashboard/unit
uv run --all-extras ruff check src/trafficlab_dashboard tests/trafficlab_dashboard
uv run --all-extras pyright src/trafficlab_dashboard tests/trafficlab_dashboard
```

Inject parser/calculation counters and prove visibility changes do not parse or calculate again. Prove failed second run leaves first selected. Verify `trafficlab-dashboard --help` without display and one offscreen launch with a checked run.

- [x] **[STEP-55-8f931598] Self-review event-loop ownership and commit Task 9**

Inspect every Qt signal target and Matplotlib call. Confirm worker threads emit data only, stale results are ignored, and application/window lifetimes remain strongly referenced through `main`.

```bash
git add src/trafficlab_dashboard tests/trafficlab_dashboard
git commit -m "feat(dashboard): add application window"
```

### Task 10: [TASK-10-4b91488b] Documentation, architecture, retained validation, performance evidence, and final gates

**Files:**
- Create: `architecture/VISUALIZATION.md`
- Modify: `architecture/README.md`
- Modify: `architecture/DEVELOPMENT.md`
- Modify: `architecture/TESTING.md`
- Modify: `README.md`
- Create: `tests/trafficlab_dashboard/integration/test_checked_run.py`
- Create: `tests/trafficlab_dashboard/integration/test_large_trace.py`
- Create or modify: `tests/unit/tooling/test_repository_layout.py`

**Interfaces:**
- Documents stable dashboard artifact, aspect, interaction, performance, terminology, and non-goal contracts
- Produces final release evidence under the root locked all-extras environment

- [x] **[STEP-56-169a4422] Write failing checked-run, performance, and layout tests**

```python
def test_checked_run_supports_every_available_aspect(checked_dashboard_run: Path) -> None:
    run = load_dashboard_run(checked_dashboard_run)
    available = [aspect.identifier for aspect in ASPECTS if aspect.identifier not in run.unavailable]
    assert available == list(EXPECTED_ASPECT_IDS)


def test_large_trace_calculates_full_totals_but_bounds_display(run_with_200k_packets) -> None:
    data = FrameSizeTimelineAspect().calculate(run_with_200k_packets, CalculationSettings.default())
    assert data.reference_sample_count == 200_000
    assert len(data.series[0].x) <= 20_000
```

Extend layout tests to include `src/trafficlab_dashboard`, dashboard tests, module line limits, and the new architecture document.

- [x] **[STEP-57-619d74b4] Run Task 10 RED and add stable architecture documents**

Run the new tests and record missing docs/behavior. Write `architecture/VISUALIZATION.md` from the approved spec as stable behavior only—no task checklist or completion ledger. Update architecture index, development all-extras commands, headless Qt evidence, root install/launch/control docs, and supported optional companion boundaries.

- [x] **[STEP-58-f1226925] Verify checked run, large trace, export, and user command**

```bash
uv sync --locked --all-groups --all-extras
QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q tests/trafficlab_dashboard
uv run --all-extras trafficlab-dashboard --help
```

Use a checked canonical run fixture, not ignored local `runs/`. Record load duration and cached redraw duration without creating a universal performance benchmark gate; assert event-loop responsiveness, full-sample totals, and display bounds.

- [x] **[STEP-59-862559b3] Run complete release gates and independent final review**

Run, serializing heavy gates:

```bash
uv sync --locked --all-groups --all-extras
uv run --all-extras ruff format --check .
uv run --all-extras ruff check .
uv run --all-extras pyright
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 10m --kill-after 10s -- \
  uv run --all-extras pytest -q -n 4 --dist worksteal -m "not docker and not internet" --durations=50
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G --wall-time 20m --kill-after 10s -- \
  uv run --all-extras pytest -q -n 4 --dist worksteal -m "not docker and not internet" \
  --cov=trafficlab --cov=trafficlab_dashboard --cov-branch --cov-report=term-missing --cov-fail-under=90 --durations=50
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M --wall-time 20m --kill-after 10s -- \
  uv run --all-extras pytest -vv -n 0 -m "docker or internet" --internet-url https://example.com
```

Request independent phase reviews throughout implementation and one most-capable whole-branch review. Fix all Critical and Important findings, rerun affected focused tests, then rerun the complete final gates after the last source change.

- [x] **[STEP-60-d4d4b2d4] Commit documentation/evidence and finish the branch**

```bash
git add architecture README.md pyproject.toml uv.lock src/trafficlab src/trafficlab_dashboard tests
git commit -m "docs(dashboard): document visualization companion"
git status --short
```

Expected: clean working tree, all implementation commits retained locally, no dashboard-owned run artifacts or exported figures in Git. Use `superpowers:finishing-a-development-branch` for merge/push/keep choice.
