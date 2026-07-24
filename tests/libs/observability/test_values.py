# pyright: reportArgumentType=false
"""Tests for immutable bounded observability values."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from trafficlab.libs.observability import (
    InvalidEventError,
    InvalidPolicyError,
    ObservabilityPolicy,
    Severity,
    StructuredEvent,
    severity_rank,
)


def _event(
    *, fields: tuple[tuple[str, object], ...] = (("count", 1),)
) -> StructuredEvent:
    return StructuredEvent(
        datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        Severity.INFO,
        "convert",
        "run-1",
        "input_validated",
        fields,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_event_is_frozen_and_canonicalizes_field_order() -> None:
    event = _event(fields=(("zeta", None), ("alpha", "ok"), ("ratio", 0.5)))

    assert event.fields == (("alpha", "ok"), ("ratio", 0.5), ("zeta", None))
    assert not hasattr(event, "__dict__")
    with pytest.raises(FrozenInstanceError):
        event.run_id = "other"  # type: ignore[misc]


@pytest.mark.unit
def test_severity_has_explicit_filter_and_sort_order() -> None:
    assert tuple(severity_rank(value) for value in Severity) == (10, 20, 30, 40, 50)
    assert severity_rank(Severity.ERROR) > severity_rank(Severity.WARNING)


@pytest.mark.unit
def test_policy_defaults_to_info_and_requires_positive_bounds() -> None:
    policy = ObservabilityPolicy(3, 2, 1)

    assert policy.minimum_severity is Severity.INFO
    with pytest.raises(InvalidPolicyError):
        ObservabilityPolicy(0, 2, 1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    (
        pytest.param(
            lambda: StructuredEvent(
                datetime(2026, 7, 24, 12, 0),
                Severity.INFO,
                "convert",
                "run-1",
                "start",
                (),
            ),
            id="naive-time",
        ),
        pytest.param(
            lambda: StructuredEvent(
                datetime(2026, 7, 24, 15, 0, tzinfo=timezone(timedelta(hours=3))),
                Severity.INFO,
                "convert",
                "run-1",
                "start",
                (),
            ),
            id="non-utc-time",
        ),
        pytest.param(lambda: _event(fields=(("payload", b"x"),)), id="bytes"),
        pytest.param(lambda: _event(fields=(("items", ("x",)),)), id="container"),
        pytest.param(lambda: _event(fields=(("api_token", "x"),)), id="secret-name"),
        pytest.param(lambda: _event(fields=(("line\nbreak", "x"),)), id="control-name"),
        pytest.param(
            lambda: _event(fields=(("same", 1), ("same", 2))),
            id="duplicate-field",
        ),
    ),
)
def test_event_rejects_unsafe_or_unbounded_values(factory: object) -> None:
    with pytest.raises(InvalidEventError):
        factory()  # type: ignore[operator]
