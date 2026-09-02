"""Packet-category construction, fitting, and no-replay tests."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields

import pytest

from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.packet_hmm.model import (
    IAT_QUANTILES,
    SIZE_QUANTILES,
    PacketCategory,
    build_observations,
    fit_trace,
)


def _reference() -> TrafficTrace:
    return TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 50),
            TraceEvent(0.0, Direction.INBOUND, 60),
            TraceEvent(1.0, Direction.INBOUND, 90),
            TraceEvent(3.0, Direction.OUTBOUND, 120),
            TraceEvent(6.0, Direction.INBOUND, 150),
            TraceEvent(10.0, Direction.OUTBOUND, 180),
        )
    )


def test_observation_vocabulary_has_explicit_zero_iat_and_only_observed_categories() -> None:
    """Binning zero with positive gaps or materializing the Cartesian product creates false emissions."""
    encoded = build_observations(_reference())

    assert IAT_QUANTILES == (1.0 / 3.0, 2.0 / 3.0)
    assert SIZE_QUANTILES == (1.0 / 3.0, 2.0 / 3.0)
    assert encoded.iat_thresholds == pytest.approx((2.0, 3.0))
    assert encoded.size_thresholds == pytest.approx((100.0, 140.0))
    assert encoded.observation_indices == (0, 1, 2, 3, 4)
    assert encoded.vocabulary == (
        PacketCategory(0, Direction.INBOUND, 0),
        PacketCategory(1, Direction.INBOUND, 0),
        PacketCategory(1, Direction.OUTBOUND, 1),
        PacketCategory(2, Direction.INBOUND, 2),
        PacketCategory(3, Direction.OUTBOUND, 2),
    )
    assert len(encoded.vocabulary) == 5
    assert len(encoded.vocabulary) < 4 * 2 * 3
    assert encoded.reservoirs[0][0].iat == 0.0


def test_fit_keeps_individual_category_members_and_excludes_subsequence_templates() -> None:
    """A stored observation path or multi-packet sample would permit replay instead of category sampling."""
    model = fit_trace(_reference(), state_count=2)

    assert sum(len(reservoir) for reservoir in model.reservoirs) == len(_reference()) - 1
    assert Counter(sample.iat for reservoir in model.reservoirs for sample in reservoir) == Counter(
        (0.0, 1.0, 2.0, 3.0, 4.0)
    )
    forbidden = ("sequence", "path", "template", "subsequence", "trace")
    assert all(not any(fragment in item.name for fragment in forbidden) for item in fields(type(model)))
    assert all(not isinstance(member, tuple) for reservoir in model.reservoirs for member in reservoir)
    assert model.initial_marks.entries[0].direction is Direction.OUTBOUND
    assert model.initial_marks.entries[0].frame_length == 50


def test_all_zero_iats_retain_zero_category_without_fake_positive_thresholds() -> None:
    """Inventing positive-IAT thresholds when no positive gap exists obscures an important empirical case."""
    trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(0.0, Direction.INBOUND, 70),
            TraceEvent(0.0, Direction.OUTBOUND, 80),
        )
    )

    encoded = build_observations(trace)

    assert encoded.iat_thresholds == ()
    assert all(category.iat_bin == 0 for category in encoded.vocabulary)
