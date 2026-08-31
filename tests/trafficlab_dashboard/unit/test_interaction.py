from __future__ import annotations

import pytest

from trafficlab_dashboard.plotting.interaction import AxisView, pan_limits, zoom_limits


def test_zoom_is_centered_at_cursor_and_shift_limits_x_only() -> None:
    limits = zoom_limits(xlim=(0.0, 10.0), ylim=(0.0, 100.0), cursor=(2.0, 20.0), factor=0.5, axes="x")

    assert limits == AxisView(x=(1.0, 6.0), y=(0.0, 100.0))


def test_zoom_limits_y_only_when_control_requests_it() -> None:
    limits = zoom_limits(xlim=(0.0, 10.0), ylim=(0.0, 100.0), cursor=(2.0, 20.0), factor=1.25, axes="y")

    assert limits == AxisView(x=(0.0, 10.0), y=(-5.0, 120.0))


@pytest.mark.parametrize(
    ("cursor", "xlim", "ylim", "factor"),
    [
        ((float("nan"), 2.0), (0.0, 10.0), (0.0, 20.0), 0.8),
        ((1.0, float("inf")), (0.0, 10.0), (0.0, 20.0), 0.8),
        ((1.0, 2.0), (4.0, 3.0), (0.0, 20.0), 0.8),
        ((1.0, 2.0), (0.0, 10.0), (8.0, 7.0), 0.8),
        ((1.0, 2.0), (0.0, 10.0), (0.0, 20.0), 0.0),
    ],
)
def test_zoom_limits_rejects_invalid_inputs(
    cursor: tuple[float, float],
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    factor: float,
) -> None:
    assert zoom_limits(xlim=xlim, ylim=ylim, cursor=cursor, factor=factor, axes="both") is None


def test_pan_limits_translate_from_anchor_delta() -> None:
    limits = pan_limits(
        xlim=(0.0, 10.0),
        ylim=(5.0, 15.0),
        anchor=(4.0, 8.0),
        current=(6.5, 10.0),
    )

    assert limits == AxisView(x=(-2.5, 7.5), y=(3.0, 13.0))


@pytest.mark.parametrize(
    ("xlim", "ylim", "anchor", "current"),
    [
        ((0.0, 10.0), (0.0, 20.0), None, (1.0, 2.0)),
        ((0.0, 10.0), (0.0, 20.0), (1.0, 2.0), None),
        ((4.0, 3.0), (0.0, 20.0), (1.0, 2.0), (2.0, 3.0)),
        ((0.0, 10.0), (7.0, 6.0), (1.0, 2.0), (2.0, 3.0)),
        ((0.0, 10.0), (0.0, 20.0), (float("nan"), 2.0), (2.0, 3.0)),
        ((0.0, 10.0), (0.0, 20.0), (1.0, 2.0), (float("inf"), 3.0)),
    ],
)
def test_pan_limits_rejects_invalid_inputs(
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    anchor: tuple[float, float] | None,
    current: tuple[float, float] | None,
) -> None:
    assert pan_limits(xlim=xlim, ylim=ylim, anchor=anchor, current=current) is None
