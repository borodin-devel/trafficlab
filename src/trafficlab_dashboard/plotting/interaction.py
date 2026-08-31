from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

type AxisSelection = Literal["both", "x", "y"]


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


def zoom_limits(
    *,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    cursor: tuple[float, float],
    factor: float,
    axes: AxisSelection,
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
        x = (
            cursor[0] + (xlim[0] - cursor[0]) * factor,
            cursor[0] + (xlim[1] - cursor[0]) * factor,
        )
    if axes in {"both", "y"}:
        y = (
            cursor[1] + (ylim[0] - cursor[1]) * factor,
            cursor[1] + (ylim[1] - cursor[1]) * factor,
        )
    return AxisView(x=x, y=y)


def pan_limits(
    *,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    anchor: tuple[float, float] | None,
    current: tuple[float, float] | None,
) -> AxisView | None:
    if not _finite_ordered(xlim) or not _finite_ordered(ylim):
        return None
    if not _finite_point(anchor) or not _finite_point(current):
        return None
    assert anchor is not None
    assert current is not None

    dx = current[0] - anchor[0]
    dy = current[1] - anchor[1]
    return AxisView(
        x=(xlim[0] - dx, xlim[1] - dx),
        y=(ylim[0] - dy, ylim[1] - dy),
    )
