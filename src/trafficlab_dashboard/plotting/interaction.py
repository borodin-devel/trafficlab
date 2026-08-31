from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

type AxisSelection = Literal["both", "x", "y"]
type AxisScale = Literal["linear", "log"]


@dataclass(frozen=True, slots=True)
class AxisView:
    x: tuple[float, float]
    y: tuple[float, float]


def _finite_ordered(bounds: tuple[float, float]) -> bool:
    lower, upper = bounds
    return math.isfinite(lower) and math.isfinite(upper) and lower <= upper


def _finite_point(point: tuple[float, float] | None) -> bool:
    if point is None:
        return False
    return math.isfinite(point[0]) and math.isfinite(point[1])


def _axis_value(value: float, scale: AxisScale) -> float | None:
    if scale == "linear":
        return value if math.isfinite(value) else None
    if scale != "log":
        raise ValueError("axis scale must be 'linear' or 'log'")
    if not math.isfinite(value) or value <= 0.0:
        return None
    return math.log10(value)


def _data_value(value: float, scale: AxisScale) -> float | None:
    if scale == "linear":
        return value if math.isfinite(value) else None
    try:
        result = math.pow(10.0, value)
    except OverflowError:
        return None
    return result if math.isfinite(result) and result > 0.0 else None


def _zoom_axis(
    bounds: tuple[float, float],
    cursor: float,
    factor: float,
    scale: AxisScale,
) -> tuple[float, float] | None:
    lower = _axis_value(bounds[0], scale)
    upper = _axis_value(bounds[1], scale)
    center = _axis_value(cursor, scale)
    if lower is None or upper is None or center is None or lower > upper:
        return None
    transformed = (center + (lower - center) * factor, center + (upper - center) * factor)
    resolved = (_data_value(transformed[0], scale), _data_value(transformed[1], scale))
    if resolved[0] is None or resolved[1] is None:
        return None
    return resolved[0], resolved[1]


def _pan_axis(
    bounds: tuple[float, float],
    anchor: float,
    current: float,
    scale: AxisScale,
) -> tuple[float, float] | None:
    lower = _axis_value(bounds[0], scale)
    upper = _axis_value(bounds[1], scale)
    start = _axis_value(anchor, scale)
    end = _axis_value(current, scale)
    if lower is None or upper is None or start is None or end is None or lower > upper:
        return None
    delta = end - start
    resolved = (_data_value(lower - delta, scale), _data_value(upper - delta, scale))
    if resolved[0] is None or resolved[1] is None:
        return None
    return resolved[0], resolved[1]


def zoom_limits(
    *,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    cursor: tuple[float, float],
    factor: float,
    axes: AxisSelection,
    x_scale: AxisScale = "linear",
    y_scale: AxisScale = "linear",
) -> AxisView | None:
    if axes not in {"both", "x", "y"}:
        raise ValueError("axes must be 'both', 'x', or 'y'")
    if not _finite_ordered(xlim) or not _finite_ordered(ylim) or not _finite_point(cursor):
        return None
    if not math.isfinite(factor) or factor <= 0.0:
        return None

    x = xlim
    y = ylim
    if axes in {"both", "x"}:
        updated_x = _zoom_axis(xlim, cursor[0], factor, x_scale)
        if updated_x is None:
            return None
        x = updated_x
    if axes in {"both", "y"}:
        updated_y = _zoom_axis(ylim, cursor[1], factor, y_scale)
        if updated_y is None:
            return None
        y = updated_y
    return AxisView(x=x, y=y)


def pan_limits(
    *,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    anchor: tuple[float, float] | None,
    current: tuple[float, float] | None,
    x_scale: AxisScale = "linear",
    y_scale: AxisScale = "linear",
) -> AxisView | None:
    if not _finite_ordered(xlim) or not _finite_ordered(ylim):
        return None
    if not _finite_point(anchor) or not _finite_point(current):
        return None
    assert anchor is not None
    assert current is not None

    x = _pan_axis(xlim, anchor[0], current[0], x_scale)
    y = _pan_axis(ylim, anchor[1], current[1], y_scale)
    if x is None or y is None:
        return None
    return AxisView(x=x, y=y)
