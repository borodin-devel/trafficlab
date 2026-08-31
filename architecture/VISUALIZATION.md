# Visualization Companion

## Purpose

`trafficlab-dashboard` is an optional read-only desktop companion for exploring
one canonical TrafficLab run directory. It renders interactive statistical
views over existing reference, generated, comparison, and optimization
artifacts without changing pipeline semantics, artifact schemas, or scientific
definitions owned elsewhere in `architecture/`.

## Scope boundaries

The dashboard is an optional package and executable in the root Python
distribution, not a pipeline stage, service, container, plugin host, or
artifact owner. It opens one run at a time in one window, never writes into the
selected run directory, and does not create an alternate parser or scientific
stack.

It does not provide live monitoring, editing, annotation, multi-window
comparison, automatic reports, model-specific exploratory views beyond the
checked first-release catalog, or a web/server subsystem.

## Input contract

The selected directory must be a canonical TrafficLab run artifact directory.

Required artifacts:

- `reference.pcapng`
- `generated.pcapng`
- `capture.json`

Optional artifacts:

- `similarity.json` enables pair-level similarity views.
- `ga_history.csv` enables optimization-history views.
- `best_model.json` supplies fitted-model and stored observation-window
  metadata.
- `experiment.toml` supplies configured scales and optimization settings used
  by retained optional artifacts.

The loader reuses TrafficLab's production metadata, PCAPNG, trace alignment,
configuration, fitted-model, and comparison parsers. Required-artifact failure
rejects the new run. Optional-artifact failure disables only dependent aspects
and records one actionable reason per unavailable aspect.

## Loaded run contract

The loaded value is an immutable `DashboardRun` containing:

- the selected directory and exact input artifact identities;
- validated capture metadata;
- normalized reference `TrafficTrace`;
- aligned, cropped generated `TrafficTrace`;
- one positive shared observation window `W`;
- optional validated similarity, fitted-model, history, and experiment records;
- aspect-unavailability reasons for optional-artifact degradation.

Numerical trace columns remain owned immutable NumPy arrays. Calculation
results are immutable plot-data records rather than Qt or Matplotlib objects.

## Aspect catalog

The first-release registry is explicit, ordered, and non-pluggable.

Time domain:

- Throughput
- Packet rate
- Cumulative bytes
- Cumulative packets
- Frame size versus timestamp
- IAT versus arrival timestamp

Distributions:

- Frame-size ECDF
- IAT ECDF
- Frame-size normalized histogram
- IAT normalized histogram
- Throughput ECDF

Direction:

- Uplink/downlink throughput
- Uplink/downlink packet rate
- Direction balance by packet and byte share

Dependence:

- Frame-size autocorrelation at lags 1 through 50
- IAT autocorrelation at lags 1 through 50
- Frame size versus IAT density

Run-level:

- Similarity component scores plus weighted aggregate
- Multiscale packet/byte discrepancy by configured scale
- GA best-fitness history by generation, family, and overall

Persisted artifact values keep the canonical `outbound` and `inbound`
directions. The dashboard translates them only for display as `uplink` and
`downlink`.

## Interaction contract

The window contains one open-run control, one aspect selector, independent
Reference and Generated visibility toggles, Reset, Export, one interactive
plot, and one status bar. Both trace toggles start enabled and checked. A trace
aspect never permits both traces to become hidden simultaneously. Pair-level
and run-level aspects disable the trace toggles while preserving stored
visibility for later trace aspects.

Artifact loading and aspect calculation run off the GUI thread with monotonic
generation tokens. Stale worker results are discarded immediately. A failed
aspect calculation leaves the previous plot visible. A failed run load leaves
the previous valid run selected. The previous plot remains interactive while
background work is in progress.

Matplotlib owns the plot surface without its default navigation toolbar. The
interaction surface is:

- left-button drag pans both axes;
- mouse wheel zooms around the cursor;
- `Shift + wheel` zooms only the x axis;
- `Ctrl + wheel` zooms only the y axis;
- Reset and double-click restore the complete calculated view.

Changing aspects preserves visibility but resets the viewport to the selected
aspect's complete view. Changing only visibility redraws cached data and
preserves the current viewport.

## Scientific calculations and display reduction

The reference defines the shared closed observation interval `[0, W]`. Generated
timestamps reuse TrafficLab's existing shift-and-crop behavior. Throughput,
packet-rate, histogram, ECDF, autocorrelation, multiscale, similarity, and GA
history plots reuse the same scientific calculations and retained diagnostics
owned by the root package and its checked artifacts.

Scientific calculations always use complete loaded samples. Display reduction is
rendering-only:

- general line displays cap each rendered series at 20,000 points;
- dense time-series views use min/max envelope reduction;
- ECDF reduction preserves endpoints and monotonicity;
- dense paired samples render as hexbin rather than raw scatter;
- histogram and time-bin edges are shared between Reference and Generated
  regardless of current visibility.

## Performance and caching

PCAPNG parsing occurs once per opened run. Aspect results are cached by run
artifact identities, aspect identifier, and calculation settings. Visibility
changes redraw cached data without reparsing or recalculating. Cached redraws
target sub-100 ms behavior on the supported development environment. Opening
large current captures may take several seconds, but the GUI event loop must
remain responsive throughout background loading and calculation.

A replacement run keeps the previously accepted cache entries until its first matching plot calculation succeeds.
That successful first-plot commit invalidates the previous cache atomically. Stale worker results are discarded
immediately and never mutate the accepted run or cached plot state.

## Export and errors

Export writes the current Matplotlib figure as PNG or SVG to a user-selected
destination. It preserves the selected aspect, visible datasets, viewport,
axes, units, annotations, legend, and title. Export never writes into the run
directory unless the user explicitly chooses that destination. Export failure
shows an error dialog and leaves the current run and plot unchanged.

Required-artifact errors reject the requested run and name the exact artifact.
Optional-artifact errors disable only dependent aspects and appear in the status
surface. Worker failures return to the GUI thread as typed failures; stale
results never update the canvas.
