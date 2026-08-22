"""Empirical Markov renewal traffic model with observable direction-size states."""

from __future__ import annotations

import math
from collections.abc import Callable
from time import monotonic

from trafficlab.common.config import GenerationLimits
from trafficlab.common.trace import Direction
from trafficlab.generation.models.common import (
    MARKOV_MODEL_DIAGNOSTIC_KEYS,
    GenerationGuard,
    GenerationResult,
    IncompleteReason,
    make_generation_trace,
)
from trafficlab.generation.models.markov_renewal.model import MarkovRenewalModel
from trafficlab.generation.models.markov_renewal.parameters import invalid_markov
from trafficlab.generation.models.markov_renewal.sampling import (
    MarkovRng,
    empirical_index_from_draw,
    holding_selection,
    probability_index_from_draw,
    weighted_index_from_draw,
)


def _validate_window(window: object) -> float:
    if type(window) is not float or not math.isfinite(window) or window <= 0.0:
        raise invalid_markov(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive normalized observation window",
        )
    return window


def validate_model(model: object) -> MarkovRenewalModel:
    if type(model) is not MarkovRenewalModel:
        raise TypeError("model must be a MarkovRenewalModel")
    try:
        return MarkovRenewalModel(
            alpha=model.alpha,
            conditional_iats=model.conditional_iats,
            global_iats=model.global_iats,
            minimum_support=model.minimum_support,
            states=model.states,
            thresholds=model.thresholds,
            time_scale=model.time_scale,
            transition_rows=model.transition_rows,
        )
    except (TypeError, ValueError) as error:
        raise invalid_markov(
            f"invalid fitted Markov renewal model: {error}",
            corrective_action="load or fit a complete finite Markov renewal model",
        ) from error


def generate_with_rng(
    model: MarkovRenewalModel,
    rng: MarkovRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate with an injected RNG while preserving the documented stochastic order."""
    checked_model = validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    timestamps: list[float] = []
    directions: list[Direction] = []
    frame_lengths: list[int] = []
    output_bytes = 0
    timing_counts: dict[str, int] = {name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS}

    def generation_result(
        *,
        complete: bool,
        reason: IncompleteReason | None = None,
    ) -> GenerationResult:
        return GenerationResult(
            complete=complete,
            trace=make_generation_trace(timestamps, directions, frame_lengths),
            reason=reason,
            model_diagnostics=timing_counts,
        )

    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return generation_result(complete=False, reason=reason)
    raw_state_draw = rng.random()
    reason = guard.post_draw_reason()
    if reason is not None:
        return generation_result(complete=False, reason=reason)
    state_index = weighted_index_from_draw(
        tuple(float(len(state.frame_lengths)) for state in checked_model.states), raw_state_draw
    )
    state = checked_model.states[state_index]

    raw_frame_draw = rng.choice(len(state.frame_lengths))
    reason = guard.post_draw_reason()
    if reason is not None:
        return generation_result(complete=False, reason=reason)
    frame_length = state.frame_lengths[empirical_index_from_draw(len(state.frame_lengths), raw_frame_draw)]
    reason = guard.prospective_reason(0, 0, frame_length)
    if reason is not None:
        return generation_result(complete=False, reason=reason)
    timestamps.append(0.0)
    directions.append(state.direction)
    frame_lengths.append(frame_length)
    output_bytes = frame_length
    current_time = 0.0

    while True:
        reason = guard.pre_draw_reason(len(timestamps), output_bytes)
        if reason is not None:
            return generation_result(complete=False, reason=reason)
        raw_transition_draw = rng.random()
        reason = guard.post_draw_reason()
        if reason is not None:
            return generation_result(complete=False, reason=reason)
        destination_index = probability_index_from_draw(checked_model.transition_rows[state_index], raw_transition_draw)
        timing_tier, holding_sample = holding_selection(
            checked_model.conditional_iats[state_index][destination_index],
            checked_model.states[state_index].source_iats,
            checked_model.global_iats,
            minimum_support=checked_model.minimum_support,
        )
        timing_counts[f"timing_tier_{timing_tier}_count"] += 1
        if state_index in checked_model.timing_diagnostics.unobserved_rows:
            timing_counts["uniform_unobserved_row_count"] += 1

        raw_holding_draw = rng.choice(len(holding_sample))
        reason = guard.post_draw_reason()
        if reason is not None:
            return generation_result(complete=False, reason=reason)
        holding_time = holding_sample[empirical_index_from_draw(len(holding_sample), raw_holding_draw)]
        scaled_holding_time = holding_time * checked_model.time_scale
        next_time = current_time + scaled_holding_time
        if not math.isfinite(scaled_holding_time) or not math.isfinite(next_time):
            raise invalid_markov(
                "invalid Markov renewal arrival time",
                corrective_action="use finite fitted timing samples and scale values that do not overflow",
            )
        if next_time > window:
            return generation_result(complete=True)

        destination = checked_model.states[destination_index]
        raw_destination_frame_draw = rng.choice(len(destination.frame_lengths))
        reason = guard.post_draw_reason()
        if reason is not None:
            return generation_result(complete=False, reason=reason)
        destination_frame_length = destination.frame_lengths[
            empirical_index_from_draw(len(destination.frame_lengths), raw_destination_frame_draw)
        ]
        reason = guard.prospective_reason(len(timestamps), output_bytes, destination_frame_length)
        if reason is not None:
            return generation_result(complete=False, reason=reason)
        timestamps.append(next_time)
        directions.append(destination.direction)
        frame_lengths.append(destination_frame_length)
        output_bytes += destination_frame_length
        current_time = next_time
        state_index = destination_index
