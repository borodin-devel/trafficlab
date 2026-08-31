from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, cast, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from trafficlab_dashboard.run_data import DashboardRun

type SeriesDataset = Literal["reference", "generated"] | None
type RenderMode = Literal["scatter", "hexbin"]
type AxisScale = Literal["linear", "log"]
type MetadataValue = str | int | float | bool | None | tuple["MetadataValue", ...] | Mapping[str, "MetadataValue"]
type PlotMetadata = Mapping[str, MetadataValue]


def _owned_float64_array(values: object) -> NDArray[np.float64]:
    if not isinstance(values, np.ndarray):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("plot arrays must be NumPy arrays")
    array = np.asarray(cast(NDArray[np.float64], values), dtype=np.float64)
    if array.ndim != 1:
        raise ValueError("plot arrays must be one-dimensional")
    if not np.all(np.isfinite(array)):
        raise ValueError("plot arrays must be finite")
    return np.frombuffer(np.array(array, dtype=np.float64, copy=True, order="C").tobytes(), dtype=np.float64)


def _require_bounds(bounds: tuple[float, float], *, name: str) -> tuple[float, float]:
    if type(bounds) is not tuple or len(bounds) != 2:
        raise TypeError(f"{name} must be a pair of floats")
    lower, upper = bounds
    if type(lower) is not float or type(upper) is not float:
        raise TypeError(f"{name} must be a pair of floats")
    if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
        raise ValueError(f"{name} must be finite ordered bounds")
    return lower, upper


def _require_string(value: str, *, name: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _require_count(value: int, *, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _require_axis_scale(value: AxisScale, *, name: str) -> AxisScale:
    if value not in {"linear", "log"}:
        raise ValueError(f"{name} must be a supported axis scale")
    return value


def _freeze_metadata_value(value: object, *, name: str) -> MetadataValue:
    if value is None or type(value) is str or type(value) is bool or type(value) is int:
        return cast(MetadataValue, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} must contain only finite floats")
        return value
    if type(value) is tuple:
        return tuple(_freeze_metadata_value(item, name=f"{name}[]") for item in cast(tuple[object, ...], value))
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        frozen: dict[str, MetadataValue] = {}
        for key, item in mapping.items():
            if type(key) is not str or not key:
                raise TypeError(f"{name} keys must be non-empty strings")
            frozen[key] = _freeze_metadata_value(item, name=f"{name}.{key}")
        return MappingProxyType(frozen)
    raise TypeError(f"{name} must contain only immutable scalar, tuple, or mapping values")


def _freeze_metadata(value: PlotMetadata) -> PlotMetadata:
    return cast(PlotMetadata, _freeze_metadata_value(value, name="metadata"))


def _require_requested_lags(
    value: tuple[int, ...] | None,
    *,
    name: str,
) -> tuple[int, ...] | None:
    if value is None:
        return None
    if type(value) is not tuple or not value:
        raise ValueError(f"{name} must be a non-empty tuple")
    if any(type(lag) is not int or lag <= 0 for lag in value):
        raise ValueError(f"{name} must contain positive integers")
    if any(left >= right for left, right in zip(value, value[1:], strict=False)):
        raise ValueError(f"{name} must be strictly increasing")
    return value


def _require_availability(
    value: tuple[bool, ...] | None,
    *,
    name: str,
    expected_length: int,
) -> tuple[bool, ...] | None:
    if value is None:
        return None
    if type(value) is not tuple or len(value) != expected_length or any(type(flag) is not bool for flag in value):
        raise ValueError(f"{name} must be a tuple of {expected_length} bools")
    return value


@dataclass(frozen=True, slots=True)
class TraceVisibility:
    reference: bool
    generated: bool

    def __post_init__(self) -> None:
        if type(self.reference) is not bool or type(self.generated) is not bool:
            raise TypeError("trace visibility flags must be bools")


@dataclass(frozen=True, slots=True)
class CalculationSettings:
    automatic_bin_minimum: int = 500
    automatic_bin_maximum: int = 1500
    acf_lags: tuple[int, ...] = tuple(range(1, 51))
    maximum_display_points: int = 20_000

    def __post_init__(self) -> None:
        if type(self.automatic_bin_minimum) is not int or self.automatic_bin_minimum <= 0:
            raise ValueError("automatic_bin_minimum must be a positive integer")
        if type(self.automatic_bin_maximum) is not int or self.automatic_bin_maximum <= 0:
            raise ValueError("automatic_bin_maximum must be a positive integer")
        if self.automatic_bin_minimum > self.automatic_bin_maximum:
            raise ValueError("automatic_bin_minimum must be no greater than automatic_bin_maximum")
        if type(self.acf_lags) is not tuple or not self.acf_lags:
            raise ValueError("acf_lags must be a non-empty tuple")
        if any(type(lag) is not int or lag <= 0 for lag in self.acf_lags):
            raise ValueError("acf_lags must contain positive integers")
        if any(left >= right for left, right in zip(self.acf_lags, self.acf_lags[1:], strict=False)):
            raise ValueError("acf_lags must be strictly increasing")
        if type(self.maximum_display_points) is not int or self.maximum_display_points < 2:
            raise ValueError("maximum_display_points must be an integer of at least two")

    @classmethod
    def default(cls) -> CalculationSettings:
        return cls()


@dataclass(frozen=True, slots=True, eq=False)
class LineSeries:
    label: str
    x: NDArray[np.float64]
    y: NDArray[np.float64]
    sample_count: int
    dataset: SeriesDataset = None
    line_style: str = "solid"

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _require_string(self.label, name="label"))
        x = _owned_float64_array(self.x)
        y = _owned_float64_array(self.y)
        if len(x) != len(y):
            raise ValueError("line series x and y arrays must have equal lengths")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "sample_count", _require_count(self.sample_count, name="sample_count"))
        if self.dataset is not None and self.dataset not in {"reference", "generated"}:
            raise ValueError("dataset must be 'reference', 'generated', or None")
        _require_string(self.line_style, name="line_style")


@dataclass(frozen=True, slots=True, eq=False)
class LinePlotData:
    identifier: str
    label: str
    title: str
    x_label: str
    y_label: str
    unit: str
    series: tuple[LineSeries, ...]
    x_limits: tuple[float, float]
    y_limits: tuple[float, float]
    x_scale: AxisScale = "linear"
    y_scale: AxisScale = "linear"
    bin_width: float | None = None
    lag_range: tuple[int, int] | None = None
    requested_lags: tuple[int, ...] | None = None
    reference_available: tuple[bool, ...] | None = None
    generated_available: tuple[bool, ...] | None = None
    unavailable_reason: str | None = None
    bin_edges: NDArray[np.float64] | None = None
    reference_sample_count: int = 0
    generated_sample_count: int = 0
    metadata: PlotMetadata = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name in ("identifier", "label", "title", "x_label", "y_label", "unit"):
            object.__setattr__(self, name, _require_string(getattr(self, name), name=name))
        if type(self.series) is not tuple or any(type(series) is not LineSeries for series in self.series):
            raise TypeError("series must be a tuple of LineSeries values")
        object.__setattr__(self, "x_limits", _require_bounds(self.x_limits, name="x_limits"))
        object.__setattr__(self, "y_limits", _require_bounds(self.y_limits, name="y_limits"))
        object.__setattr__(self, "x_scale", _require_axis_scale(self.x_scale, name="x_scale"))
        object.__setattr__(self, "y_scale", _require_axis_scale(self.y_scale, name="y_scale"))
        if self.bin_width is not None:
            if type(self.bin_width) is not float or not math.isfinite(self.bin_width) or self.bin_width <= 0.0:
                raise ValueError("bin_width must be a finite positive float or None")
        if self.lag_range is not None:
            if (
                type(self.lag_range) is not tuple
                or len(self.lag_range) != 2
                or any(type(value) is not int or value <= 0 for value in self.lag_range)
                or self.lag_range[0] > self.lag_range[1]
            ):
                raise ValueError("lag_range must be an ordered positive integer pair or None")
        requested_lags = _require_requested_lags(self.requested_lags, name="requested_lags")
        object.__setattr__(self, "requested_lags", requested_lags)
        if requested_lags is not None:
            if self.lag_range is None:
                object.__setattr__(self, "lag_range", (requested_lags[0], requested_lags[-1]))
            elif self.lag_range != (requested_lags[0], requested_lags[-1]):
                raise ValueError("lag_range must match the requested lag endpoints")
            object.__setattr__(
                self,
                "reference_available",
                _require_availability(
                    self.reference_available,
                    name="reference_available",
                    expected_length=len(requested_lags),
                ),
            )
            object.__setattr__(
                self,
                "generated_available",
                _require_availability(
                    self.generated_available,
                    name="generated_available",
                    expected_length=len(requested_lags),
                ),
            )
        elif self.reference_available is not None or self.generated_available is not None:
            raise ValueError("availability metadata requires requested_lags")
        if self.unavailable_reason is not None:
            object.__setattr__(self, "unavailable_reason", _require_string(self.unavailable_reason, name="unavailable_reason"))
        if self.bin_edges is not None:
            edges = _owned_float64_array(self.bin_edges)
            if len(edges) < 2 or np.any(np.diff(edges) < 0.0):
                raise ValueError("bin_edges must be a nondecreasing float64 edge array")
            object.__setattr__(self, "bin_edges", edges)
        object.__setattr__(
            self, "reference_sample_count", _require_count(self.reference_sample_count, name="reference_sample_count")
        )
        object.__setattr__(
            self, "generated_sample_count", _require_count(self.generated_sample_count, name="generated_sample_count")
        )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True, eq=False)
class HistogramSeries:
    label: str
    edges: NDArray[np.float64]
    values: NDArray[np.float64]
    sample_count: int
    dataset: SeriesDataset = None
    zero_count: int = 0
    positive_sample_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _require_string(self.label, name="label"))
        edges = _owned_float64_array(self.edges)
        values = _owned_float64_array(self.values)
        if len(edges) == 0:
            if len(values) != 0:
                raise ValueError("empty histogram edges require empty values")
        elif len(edges) != len(values) + 1:
            raise ValueError("histogram edges must be one longer than histogram values")
        if len(edges) > 1 and np.any(np.diff(edges) <= 0.0):
            raise ValueError("histogram edges must be strictly increasing")
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "sample_count", _require_count(self.sample_count, name="sample_count"))
        if self.dataset is not None and self.dataset not in {"reference", "generated"}:
            raise ValueError("dataset must be 'reference', 'generated', or None")
        object.__setattr__(self, "zero_count", _require_count(self.zero_count, name="zero_count"))
        if self.positive_sample_count is not None:
            object.__setattr__(
                self,
                "positive_sample_count",
                _require_count(self.positive_sample_count, name="positive_sample_count"),
            )


@dataclass(frozen=True, slots=True, eq=False)
class HistogramPlotData:
    identifier: str
    label: str
    title: str
    x_label: str
    y_label: str
    unit: str
    series: tuple[HistogramSeries, ...]
    x_limits: tuple[float, float]
    y_limits: tuple[float, float]
    x_scale: AxisScale = "linear"
    y_scale: AxisScale = "linear"
    reference_sample_count: int = 0
    generated_sample_count: int = 0
    metadata: PlotMetadata = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name in ("identifier", "label", "title", "x_label", "y_label", "unit"):
            object.__setattr__(self, name, _require_string(getattr(self, name), name=name))
        if type(self.series) is not tuple or any(type(series) is not HistogramSeries for series in self.series):
            raise TypeError("series must be a tuple of HistogramSeries values")
        object.__setattr__(self, "x_limits", _require_bounds(self.x_limits, name="x_limits"))
        object.__setattr__(self, "y_limits", _require_bounds(self.y_limits, name="y_limits"))
        object.__setattr__(self, "x_scale", _require_axis_scale(self.x_scale, name="x_scale"))
        object.__setattr__(self, "y_scale", _require_axis_scale(self.y_scale, name="y_scale"))
        object.__setattr__(
            self, "reference_sample_count", _require_count(self.reference_sample_count, name="reference_sample_count")
        )
        object.__setattr__(
            self, "generated_sample_count", _require_count(self.generated_sample_count, name="generated_sample_count")
        )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True, eq=False)
class BarSeries:
    label: str
    values: NDArray[np.float64]
    sample_count: int
    dataset: SeriesDataset = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _require_string(self.label, name="label"))
        object.__setattr__(self, "values", _owned_float64_array(self.values))
        object.__setattr__(self, "sample_count", _require_count(self.sample_count, name="sample_count"))
        if self.dataset is not None and self.dataset not in {"reference", "generated"}:
            raise ValueError("dataset must be 'reference', 'generated', or None")


@dataclass(frozen=True, slots=True, eq=False)
class BarPlotData:
    identifier: str
    label: str
    title: str
    categories: tuple[str, ...]
    series: tuple[BarSeries, ...]
    y_label: str
    unit: str
    y_limits: tuple[float, float]
    x_scale: AxisScale = "linear"
    y_scale: AxisScale = "linear"
    metadata: PlotMetadata = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name in ("identifier", "label", "title", "y_label", "unit"):
            object.__setattr__(self, name, _require_string(getattr(self, name), name=name))
        if type(self.categories) is not tuple or not self.categories:
            raise TypeError("categories must be a non-empty tuple")
        if any(type(category) is not str or not category for category in self.categories):
            raise TypeError("categories must contain non-empty strings")
        if type(self.series) is not tuple or any(type(series) is not BarSeries for series in self.series):
            raise TypeError("series must be a tuple of BarSeries values")
        if any(len(series.values) != len(self.categories) for series in self.series):
            raise ValueError("each bar series must align with every category")
        object.__setattr__(self, "x_scale", _require_axis_scale(self.x_scale, name="x_scale"))
        object.__setattr__(self, "y_scale", _require_axis_scale(self.y_scale, name="y_scale"))
        object.__setattr__(self, "y_limits", _require_bounds(self.y_limits, name="y_limits"))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    @property
    def values(self) -> NDArray[np.float64]:
        if len(self.series) != 1:
            raise AttributeError("values is available only for single-series bar plots")
        return self.series[0].values


@dataclass(frozen=True, slots=True, eq=False)
class HexbinPlotData:
    identifier: str
    label: str
    title: str
    x_label: str
    y_label: str
    unit: str
    reference_x: NDArray[np.float64]
    reference_y: NDArray[np.float64]
    generated_x: NDArray[np.float64]
    generated_y: NDArray[np.float64]
    x_limits: tuple[float, float]
    y_limits: tuple[float, float]
    reference_sample_count: int
    generated_sample_count: int
    x_scale: AxisScale = "linear"
    y_scale: AxisScale = "linear"
    render_mode: RenderMode = "scatter"
    metadata: PlotMetadata = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name in ("identifier", "label", "title", "x_label", "y_label", "unit"):
            object.__setattr__(self, name, _require_string(getattr(self, name), name=name))
        reference_x = _owned_float64_array(self.reference_x)
        reference_y = _owned_float64_array(self.reference_y)
        generated_x = _owned_float64_array(self.generated_x)
        generated_y = _owned_float64_array(self.generated_y)
        if len(reference_x) != len(reference_y) or len(generated_x) != len(generated_y):
            raise ValueError("hexbin coordinates must have matching x and y lengths")
        object.__setattr__(self, "reference_x", reference_x)
        object.__setattr__(self, "reference_y", reference_y)
        object.__setattr__(self, "generated_x", generated_x)
        object.__setattr__(self, "generated_y", generated_y)
        object.__setattr__(self, "x_limits", _require_bounds(self.x_limits, name="x_limits"))
        object.__setattr__(self, "y_limits", _require_bounds(self.y_limits, name="y_limits"))
        object.__setattr__(self, "x_scale", _require_axis_scale(self.x_scale, name="x_scale"))
        object.__setattr__(self, "y_scale", _require_axis_scale(self.y_scale, name="y_scale"))
        object.__setattr__(
            self, "reference_sample_count", _require_count(self.reference_sample_count, name="reference_sample_count")
        )
        object.__setattr__(
            self, "generated_sample_count", _require_count(self.generated_sample_count, name="generated_sample_count")
        )
        if self.render_mode not in {"scatter", "hexbin"}:
            raise ValueError("render_mode must be 'scatter' or 'hexbin'")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


type PlotData = LinePlotData | HistogramPlotData | BarPlotData | HexbinPlotData


@runtime_checkable
class Aspect(Protocol):
    @property
    def identifier(self) -> str: ...

    @property
    def label(self) -> str: ...

    @property
    def category(self) -> str: ...

    @property
    def trace_controls(self) -> bool: ...

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> PlotData: ...
