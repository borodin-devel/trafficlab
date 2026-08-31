# TrafficLab Dashboard Design

## Purpose

`trafficlab-dashboard` is an optional read-only desktop companion for exploring
the reference, generated, comparison, and optimization artifacts in one
canonical TrafficLab run directory. It provides interactive statistical plots
without changing capture, fitting, generation, comparison, or artifact
semantics.

The dashboard is part of the root Python distribution and lockfile. Its
executable name contains a hyphen, while its import package uses the valid
Python name `trafficlab_dashboard`.

## Goals

- Open one canonical TrafficLab run directory.
- Display one interactive plot in one window.
- Select one aspect at a time.
- Independently show or hide Reference and Generated trace data.
- Overlay both traces with identical calculations and axes.
- Pan by dragging and zoom around the mouse cursor with the wheel.
- Export the exact current plot and viewport as PNG or SVG.
- Display pair-level similarity and GA history aspects.
- Remain responsive while parsing and calculating large captures.
- Reuse TrafficLab's production artifact and scientific boundaries.

## Non-goals

- Live capture monitoring
- Artifact editing or annotation
- Multiple windows, tabs, or simultaneous canvases
- Cross-run comparison
- Model-specific plots in the first release
- Automatic PDF, HTML, or multi-plot report generation
- A web server, database, telemetry subsystem, or persistent service
- A second PCAPNG parser or alternate scientific definitions

## Repository layout

The root project continues to own packaging, locking, documentation, and test
configuration:

```text
pyproject.toml
README.md
src/
├── trafficlab/
└── trafficlab_dashboard/
    ├── __init__.py
    ├── __main__.py
    ├── app.py
    ├── window.py
    ├── run_loader.py
    ├── run_data.py
    ├── aspects/
    │   ├── base.py
    │   ├── time_domain.py
    │   ├── distributions.py
    │   ├── direction.py
    │   ├── dependence.py
    │   └── run_level.py
    └── plotting/
        ├── canvas.py
        ├── interaction.py
        └── export.py
tests/
└── trafficlab_dashboard/
    ├── unit/
    ├── integration/
    └── support/
```

Existing TrafficLab tests remain in their current directories. Moving them is
outside scope because their path-sensitive fixtures and repository-root
calculations would create a large unrelated migration.

## Packaging and invocation

The root `pyproject.toml` adds an optional dashboard extra:

```toml
[project.optional-dependencies]
dashboard = [
    "matplotlib>=3.10,<4",
    "pyside6>=6.8,<7",
]

[project.scripts]
trafficlab = "trafficlab.cli:entrypoint"
trafficlab-dashboard = "trafficlab_dashboard.__main__:main"
```

Development and release environments install the extra explicitly:

```bash
uv sync --locked --all-groups --all-extras
```

Launch with a run directory:

```bash
uv run --extra dashboard trafficlab-dashboard RUN_DIRECTORY
```

With no positional directory, the application starts and opens a native
directory chooser. Supplying more than one positional path is an error.

## Input contract

The selected directory is a canonical TrafficLab run artifact directory.

Required artifacts:

- `reference.pcapng`
- `generated.pcapng`
- `capture.json`

Optional artifacts:

- `similarity.json` enables similarity aspects.
- `ga_history.csv` enables GA fitness history.
- `best_model.json` supplies model and observation-window metadata.
- `experiment.toml` supplies configured scales and settings.

The loader uses TrafficLab's existing metadata, PCAPNG, trace normalization,
generated alignment, comparison, best-model, and configuration parsers. It does
not trust filename presence alone. Required artifact failure rejects the new
run. Optional artifact failure disables only the dependent aspects and retains
an actionable reason.

Opening a failed run leaves the previously loaded valid run and plot unchanged.
The dashboard never writes into the selected directory.

## Terminology

Persisted TrafficLab artifacts retain their existing `outbound` and `inbound`
values. The dashboard translates them only for display:

- `outbound` becomes **uplink**.
- `inbound` becomes **downlink**.

Uplink means the Ethernet source equals `capture.json.target_mac`. Downlink
means every other accepted Ethernet frame under TrafficLab's current trace
contract. The dashboard does not claim that inferred external-capture metadata
is authoritative provenance.

## Loaded data model

`DashboardRun` is an immutable value containing:

- artifact directory and exact input identities;
- validated capture metadata;
- normalized reference `TrafficTrace`;
- aligned and cropped generated `TrafficTrace`;
- positive reference observation window `W`;
- optional validated similarity result;
- optional validated best model;
- optional parsed GA history;
- optional realized experiment settings;
- unavailable-aspect reasons.

Numerical trace columns remain owned, immutable NumPy arrays. Calculation
results are immutable aspect-specific records rather than Matplotlib or Qt
objects.

## Application architecture

PySide6 owns the window, controls, file chooser, progress state, status bar,
dialogs, and GUI lifecycle. Matplotlib's QtAgg canvas owns rendering and export.

Artifact loading and expensive aspect calculation run in a Qt thread pool. All
widget and Matplotlib mutation occurs on the GUI thread. Every load or aspect
request carries a monotonically increasing generation token. A result whose
token is older than current state is discarded.

The previous plot remains visible during background work. Controls that would
start conflicting work are disabled only as long as needed; pan, zoom, reset,
and export remain available for the current plot.

## Aspect interface and registry

The aspect registry is explicit and ordered. There is no dynamic plugin
discovery in the first release.

Each aspect implements a typed contract containing:

- stable identifier;
- display label and category;
- required optional artifacts;
- whether Reference/Generated toggles apply;
- pure calculation over `DashboardRun` and visibility-independent settings;
- immutable plot-data result;
- rendering into one Matplotlib `Axes`;
- default axis bounds and scale behavior;
- units, sample counts, bin widths, and lag metadata.

Calculation functions never access Qt widgets. Renderers never parse artifacts
or recompute statistics.

## Window layout

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Open Run │ Aspect ▼ │ Reference ✓ │ Generated ✓ │ Reset │ Export ▼ │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│                       Interactive plot                              │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│ Run name · packets · observation window · calculation status        │
└─────────────────────────────────────────────────────────────────────┘
```

- Reference and Generated are independent checkable buttons.
- Both are enabled by default.
- The last visible trace cannot be disabled for trace aspects.
- Pair-level and optimization aspects disable both trace buttons.
- Changing aspects preserves trace visibility but resets the new aspect's
  viewport.
- Changing trace visibility preserves the current viewport.
- Reset restores calculated complete-data limits.
- Export offers PNG and SVG.

## Visual encoding

- Reference dataset: blue.
- Generated dataset: orange.
- Uplink direction: solid.
- Downlink direction: dashed.
- Histograms and dense data use transparency.
- Legends always distinguish dataset and, where applicable, direction.
- Every title includes aspect name, units, sample counts, and relevant bin
  width or lag range.

Dataset is encoded by color. Direction is encoded by line style. This avoids
overloading a dashed Reference convention when four directional series are
visible together.

## Interaction

- Left-button drag pans both axes.
- Mouse wheel zooms both axes around the cursor.
- `Shift + wheel` zooms only the x axis.
- `Ctrl + wheel` zooms only the y axis.
- Double-click and Reset restore the complete view.
- Interaction works with either one or both trace datasets visible.
- Export captures current visibility, viewport, title, axes, and legend.

Matplotlib's default navigation toolbar is not shown; dashboard controls own the
defined interaction surface.

## First-release aspect catalog

### Time domain

- Throughput over time, Mbps
- Packet rate over time, packets/second
- Cumulative bytes, MiB
- Cumulative packets
- Frame size versus timestamp
- IAT versus arrival timestamp

### Distributions

- Frame-size ECDF
- IAT ECDF
- Frame-size normalized histogram
- IAT normalized histogram
- Throughput ECDF

### Direction

- Uplink/downlink throughput
- Uplink/downlink packet rate
- Direction balance using packet and byte proportions

### Dependence

- Frame-size autocorrelation, lags 1 through 50
- IAT autocorrelation, lags 1 through 50
- Frame size versus IAT hexbin density

### Comparative and optimization

- Similarity component scores and weighted aggregate
- Multiscale packet/byte discrepancy by configured scale
- GA best fitness by generation, family, and overall

Similarity, multiscale-discrepancy, and GA-history aspects disable trace
visibility controls because they are pair-level or run-level data.

## Scientific calculations

The reference defines the common closed observation interval `[0, W]`.
Generated timestamps use TrafficLab's existing shift and crop behavior.

Throughput is captured frame length multiplied by eight and divided by bin
duration, displayed in Mbps. Packet rate is count divided by bin duration.

Shared time-bin edges are selected from the `1, 2, 5 × 10ⁿ` sequence to produce
approximately 500 to 1,500 bins across `W`. Reference and Generated always use
identical edges.

Histogram edges derive from the combined loaded Reference and Generated trace
data regardless of current button visibility, then apply identically to both
datasets. This keeps visibility toggles as cached redraws rather than statistical
recalculations. When both traces are visible, histograms show density rather
than raw count. Frame-size edges use NumPy's Freedman–Diaconis rule with
deterministic fallbacks for constant or very small samples.

Positive IATs use logarithmic histogram edges. Zero IATs remain part of the
scientific sample and are reported as separate annotated counts instead of
being silently removed or placed on a logarithmic axis.

ECDFs retain ties and zero IAT values. Statistical calculations use complete
samples.

ACF uses TrafficLab's existing estimator and common lags 1 through 50. A lag
unavailable for either sample is represented as unavailable, not zero.

Similarity plots read canonical values and diagnostics from `similarity.json`.
They do not recompute an alternate similarity definition. Multiscale plots use
the stored configured scales and diagnostics. GA history uses canonical rows
from `ga_history.csv`.

## Display reduction and performance

Scientific calculations always use full input arrays. Display reduction is a
rendering concern only.

- General line plots target at most 20,000 displayed points.
- Dense time series use a min/max envelope per display bucket.
- ECDF display reduction preserves endpoints and monotonicity.
- Raw scatter is used only below a small point threshold; larger samples use
  Matplotlib hexbin.
- PCAPNG files parse once per opened run.
- Aspect results are cached by run artifact identities, aspect identifier, and
  calculation settings.
- Reference/Generated visibility redraws cached data without reparsing or
  recalculating.
- Cached redraw targets less than 100 ms on the current supported environment.
- Opening the largest current capture may take several seconds but must never
  block GUI event processing.

Opening a different run invalidates every cache entry and pending worker token.

## Export

PNG and SVG export operate on the current Matplotlib figure. They preserve:

- selected aspect;
- visible datasets;
- current pan/zoom viewport;
- axes, scales, units, annotations, and legend;
- dashboard plot title.

Export never writes into the run artifact directory unless the user explicitly
chooses that location. Export failure produces an error dialog and leaves the
current dashboard state unchanged.

## Error handling

- Required artifact errors reject the new run and name the exact artifact and
  corrective action.
- Optional artifact errors disable only dependent aspects and appear in the
  status bar.
- Worker exceptions return to the GUI thread as typed failures.
- A failed aspect calculation leaves the previous plot visible.
- A failed run load leaves the previous valid run selected.
- Stale worker results never update the canvas.
- Export errors do not change plot or run state.

## Testing strategy

Dashboard tests live under `tests/trafficlab_dashboard/` and use
`QT_QPA_PLATFORM=offscreen`.

Unit tests cover:

- strict run loading and optional-artifact availability;
- aspect registry order and capability declarations;
- hand-calculated throughput, rate, cumulative, ECDF, histogram, direction,
  ACF, hexbin-input, multiscale, similarity, and GA-history data;
- identical bins, lags, units, and axes for overlays;
- zero-IAT and constant-sample edge cases;
- display reduction invariants;
- cache identity and stale-generation rejection;
- cursor-centered zoom and drag-pan coordinate transforms.

Headless integration tests cover:

- command-line run loading and no-argument directory chooser flow;
- Reference/Generated toggle invariants;
- aspect switching and disabled pair-level controls;
- worker completion, stale-result rejection, and error dialogs;
- reset behavior;
- PNG dimensions and nonempty output;
- parseable SVG with expected titles and labels;
- loading a checked canonical run fixture.

Tests avoid pixel-perfect screenshots because Qt, Matplotlib, fonts, and DPI can
change raster output without changing behavior.

Large-trace tests prove that calculations use complete samples while rendered
series respect display bounds. Cached visibility redraw tests prove parsing and
calculation are not repeated.

## Documentation and architecture changes

- Add `architecture/VISUALIZATION.md` for the stable dashboard contract.
- Update `architecture/README.md` to list the optional companion.
- Update `architecture/DEVELOPMENT.md` for `--all-extras`, dashboard static
  checks, and release commands.
- Update `architecture/TESTING.md` for headless Qt and dashboard evidence.
- Update the root `README.md` with installation, launch, controls, artifact
  requirements, and screenshots only after implementation exists.

The dashboard is an optional read-only companion process, not a pipeline stage
or artifact owner. The existing one-process pipeline remains unchanged.

## Acceptance criteria

- One root project and lockfile own both packages.
- `trafficlab-dashboard` launches with a canonical run or chooser.
- One window contains one aspect selector, two trace toggles, one interactive
  canvas, reset, and export.
- Every first-release aspect follows the calculation contract above.
- Both traces can be shown independently or overlaid.
- Pair-level aspects disable trace toggles.
- Drag-pan, cursor-centered wheel zoom, modifier-axis zoom, reset, PNG, and SVG
  work under headless integration tests where applicable.
- Large current captures remain responsive through background work, caching,
  and display reduction.
- Required and optional artifact failures follow the stated behavior.
- Root TrafficLab scientific and artifact semantics are reused rather than
  duplicated.
- Root and dashboard static, unit, integration, coverage, and applicable
  external gates pass under the locked all-extras environment.
