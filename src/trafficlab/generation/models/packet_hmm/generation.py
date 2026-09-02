"""Guarded packet-by-packet generation from a categorical packet HMM."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from time import monotonic
from typing import Protocol, cast

from trafficlab.common.config import GenerationLimits
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction
from trafficlab.generation.models.common import (
    GenerationGuard,
    GenerationResult,
    IncompleteReason,
    make_generation_trace,
)
from trafficlab.generation.models.packet_hmm.model import PacketHmmModel


class HmmRng(Protocol):
    """Scalar NumPy-compatible primitives in the persisted draw order."""

    def random(self) -> float:
        """Return one float in [0, 1)."""
        ...

    def choice(self, a: int) -> int:
        """Return one integer in [0, population)."""
        ...


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(f"invalid packet HMM {detail}", corrective_action=corrective_action)


def _probability_index(probabilities: Sequence[float], draw: object) -> int:
    if type(draw) is not float or not math.isfinite(draw) or not 0.0 <= draw < 1.0:
        raise _invalid(
            "random draw",
            corrective_action="use a random generator returning finite continuous values in [0, 1)",
        )
    threshold = draw
    cumulative = 0.0
    values = tuple(probabilities)
    for index, probability in enumerate(values[:-1]):
        cumulative += probability
        if threshold < cumulative:
            return index
    return len(values) - 1


def _empirical_index(population: int, draw: object) -> int:
    if type(draw) is not int or not 0 <= draw < population:
        raise _invalid(
            "empirical random draw",
            corrective_action="use a random generator returning integer choices in [0, population)",
        )
    return draw


def validate_model(model: object) -> PacketHmmModel:
    """Reconstruct a model so direct attribute corruption cannot reach generation."""
    if type(model) is not PacketHmmModel:
        raise TypeError("model must be a PacketHmmModel")
    try:
        return PacketHmmModel(
            additive_smoothing=model.additive_smoothing,
            convergence_tolerance=model.convergence_tolerance,
            diagnostics=model.diagnostics,
            emission_rows=model.emission_rows,
            iat_quantiles=model.iat_quantiles,
            iat_thresholds=model.iat_thresholds,
            initial_marks=model.initial_marks,
            initial_probabilities=model.initial_probabilities,
            initialization=model.initialization,
            maximum_iterations=model.maximum_iterations,
            reservoirs=model.reservoirs,
            size_quantiles=model.size_quantiles,
            size_thresholds=model.size_thresholds,
            state_count=model.state_count,
            transition_rows=model.transition_rows,
            vocabulary=model.vocabulary,
        )
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"fitted model: {error}",
            corrective_action="load or fit a complete finite canonical categorical packet HMM",
        ) from error


def _validate_window(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise _invalid(
            "observation window",
            corrective_action="provide a finite positive normalized observation window",
        )
    return value


def generate_with_rng(
    model: PacketHmmModel,
    rng: HmmRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate in initial-mark, hidden-state, category, raw-member scalar order."""
    checked = validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    timestamps: list[float] = []
    directions: list[Direction] = []
    frame_lengths: list[int] = []
    output_bytes = 0
    diagnostics = {
        **{f"hidden_state_{state}_count": 0 for state in range(checked.state_count)},
        **{f"category_{category}_count": 0 for category in range(len(checked.vocabulary))},
    }

    def result(*, complete: bool, reason: IncompleteReason | None = None) -> GenerationResult:
        return GenerationResult(
            complete=complete,
            trace=make_generation_trace(timestamps, directions, frame_lengths),
            reason=reason,
            model_diagnostics=diagnostics,
        )

    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return result(complete=False, reason=reason)
    direction, frame_length = checked.initial_marks.sample(rng)
    reason = guard.post_draw_reason()
    if reason is not None:
        return result(complete=False, reason=reason)
    reason = guard.prospective_reason(0, 0, frame_length)
    if reason is not None:
        return result(complete=False, reason=reason)
    timestamps.append(0.0)
    directions.append(direction)
    frame_lengths.append(frame_length)
    output_bytes = frame_length

    reason = guard.pre_draw_reason(len(timestamps), output_bytes)
    if reason is not None:
        return result(complete=False, reason=reason)
    state_draw = rng.random()
    reason = guard.post_draw_reason()
    if reason is not None:
        return result(complete=False, reason=reason)
    state = _probability_index(checked.initial_probabilities, state_draw)
    current_time = 0.0

    while True:
        reason = guard.pre_draw_reason(len(timestamps), output_bytes)
        if reason is not None:
            return result(complete=False, reason=reason)
        emission_draw = rng.random()
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(complete=False, reason=reason)
        category_index = _probability_index(checked.emission_rows[state], emission_draw)
        reservoir = checked.reservoirs[category_index]
        raw_member_draw = rng.choice(len(reservoir))
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(complete=False, reason=reason)
        member = reservoir[_empirical_index(len(reservoir), raw_member_draw)]
        next_time = current_time + member.iat
        if not math.isfinite(next_time):
            raise _invalid(
                "arrival time",
                corrective_action="use finite fitted raw IAT members whose cumulative sum does not overflow",
            )
        if next_time > window:
            return result(complete=True)
        category = checked.vocabulary[category_index]
        reason = guard.prospective_reason(len(timestamps), output_bytes, member.frame_length)
        if reason is not None:
            return result(complete=False, reason=reason)
        timestamps.append(next_time)
        directions.append(category.direction)
        frame_lengths.append(member.frame_length)
        output_bytes += member.frame_length
        current_time = next_time
        diagnostics[f"hidden_state_{state}_count"] += 1
        diagnostics[f"category_{category_index}_count"] += 1

        reason = guard.pre_draw_reason(len(timestamps), output_bytes)
        if reason is not None:
            return result(complete=False, reason=reason)
        transition_draw = rng.random()
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(complete=False, reason=reason)
        state = _probability_index(checked.transition_rows[state], transition_draw)


def generate(
    model: PacketHmmModel,
    seed: int,
    W: float,
    limits: GenerationLimits,
    *,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate with one local PCG64 stream."""
    from trafficlab.generation.models.common import make_rng

    if type(seed) is not int or seed < 0:
        raise _invalid("seed", corrective_action="provide a nonnegative exact integer generation seed")
    return generate_with_rng(model, cast(HmmRng, make_rng(seed)), W=W, limits=limits, clock=clock)


__all__ = ["HmmRng", "generate", "generate_with_rng", "validate_model"]
