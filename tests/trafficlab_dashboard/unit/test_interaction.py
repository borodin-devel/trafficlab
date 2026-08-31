from __future__ import annotations

import pytest

from trafficlab_dashboard.plotting.interaction import AxisView, pan_limits, zoom_limits


def test_zoom_is_centered_at_cursor_and_shift_limits_x_only() -> None:
    limits = zoom_limits(xlim=(0.0, 10.0), ylim=(0.0, 100.0), cursor=(2.0, 20.0), factor=0.5, axes="x")

    assert limits == AxisView(x=(1.0, 6.0), y=(0.0, 100.0))


def test_zoom_limits_y_only_when_control_requests_it() -> None:
    limits = zoom_limits(xlim=(0.0, 10.0), ylim=(0.0, 100.0), cursor=(2.0, 20.0), factor=1.25, axes="y")

    assert limits == AxisView(x=(0.0, 10.0), y=(-5.0, 120.0))


def test_zoom_rejects_unknown_axis_selection() -> None:
    with pytest.raises(ValueError, match="axes must"):
        zoom_limits(
            xlim=(0.0, 10.0),
            ylim=(0.0, 10.0),
            cursor=(1.0, 1.0),
            factor=0.8,
            axes="unknown",  # type: ignore[arg-type]
        )


def test_log_zoom_preserves_cursor_fraction_and_positive_bounds() -> None:
    limits = zoom_limits(
        xlim=(0.1, 100.0),
        ylim=(0.0, 10.0),
        cursor=(1.0, 5.0),
        factor=0.5,
        axes="x",
        x_scale="log",
        y_scale="linear",
    )

    assert limits is not None
    assert limits.x == pytest.approx((10.0**-0.5, 10.0))
    assert limits.y == (0.0, 10.0)
    before_fraction = (0.0 - (-1.0)) / (2.0 - (-1.0))
    after_fraction = (0.0 - -0.5) / (1.0 - -0.5)
    assert after_fraction == pytest.approx(before_fraction)
    assert limits.x[0] > 0.0


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


def test_log_pan_translates_in_axis_space_and_never_proposes_nonpositive_bounds() -> None:
    limits = pan_limits(
        xlim=(0.1, 100.0),
        ylim=(5.0, 15.0),
        anchor=(1.0, 8.0),
        current=(10.0, 10.0),
        x_scale="log",
        y_scale="linear",
    )

    assert limits is not None
    assert limits.x == pytest.approx((0.01, 10.0))
    assert limits.y == (3.0, 13.0)
    assert limits.x[0] > 0.0


def test_log_transform_rejects_nonpositive_bounds_or_points() -> None:
    assert (
        zoom_limits(
            xlim=(0.0, 10.0),
            ylim=(0.0, 1.0),
            cursor=(1.0, 0.5),
            factor=0.8,
            axes="x",
            x_scale="log",
        )
        is None
    )
    assert (
        pan_limits(
            xlim=(0.1, 10.0),
            ylim=(0.0, 1.0),
            anchor=(0.0, 0.5),
            current=(1.0, 0.5),
            x_scale="log",
        )
        is None
    )


def test_axis_transform_rejects_unknown_scales_and_overflowing_inverse() -> None:
    with pytest.raises(ValueError, match="axis scale"):
        zoom_limits(
            xlim=(1.0, 10.0),
            ylim=(0.0, 1.0),
            cursor=(2.0, 0.5),
            factor=0.8,
            axes="x",
            x_scale="unsupported",  # type: ignore[arg-type]
        )
    assert (
        zoom_limits(
            xlim=(1.0, 10.0),
            ylim=(0.0, 1.0),
            cursor=(1.0, 0.5),
            factor=1e308,
            axes="x",
            x_scale="log",
        )
        is None
    )
    assert (
        pan_limits(
            xlim=(1.0, 1e308),
            ylim=(0.0, 1.0),
            anchor=(1e308, 0.5),
            current=(1.0, 0.5),
            x_scale="log",
        )
        is None
    )


def test_zoom_rejects_invalid_log_y_axis_after_valid_x_update() -> None:
    assert (
        zoom_limits(
            xlim=(1.0, 10.0),
            ylim=(0.0, 1.0),
            cursor=(2.0, 0.5),
            factor=0.8,
            axes="both",
            y_scale="log",
        )
        is None
    )


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
