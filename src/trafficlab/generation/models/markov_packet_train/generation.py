"""Deterministic packet-by-packet generation for Markov packet trains."""

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
from trafficlab.generation.models.markov_packet_train.model import (
    MarkovPacketTrainModel,
    inter_train_gap_selection,
)
from trafficlab.generation.models.markov_packet_train.segmentation import position_class
from trafficlab.generation.models.markov_renewal.parameters import invalid_markov
from trafficlab.generation.models.markov_renewal.sampling import (
    MarkovRng,
    empirical_index_from_draw,
    probability_index_from_draw,
)


def _invalid(detail: str, *, corrective_action: str) -> Exception:
    return invalid_markov(f"invalid Markov packet-train {detail}", corrective_action=corrective_action)


def _validate_window(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise _invalid(
            "observation window",
            corrective_action="provide a finite positive normalized observation window",
        )
    return value


def validate_model(model: object) -> MarkovPacketTrainModel:
    """Reconstruct one model so mutated frozen instances cannot bypass validation."""
    if type(model) is not MarkovPacketTrainModel:
        raise TypeError("model must be a MarkovPacketTrainModel")
    try:
        return MarkovPacketTrainModel(
            conditional_inter_train_gaps=model.conditional_inter_train_gaps,
            gap_quantile=model.gap_quantile,
            gap_threshold=model.gap_threshold,
            global_inter_train_gaps=model.global_inter_train_gaps,
            initial_probabilities=model.initial_probabilities,
            inside_train_endpoint=model.inside_train_endpoint,
            length_cap=model.length_cap,
            states=model.states,
            transition_pseudocount=model.transition_pseudocount,
            transition_rows=model.transition_rows,
        )
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"fitted model: {error}",
            corrective_action="load or fit a complete finite Markov packet-train model",
        ) from error


def generate_with_rng(
    model: MarkovPacketTrainModel,
    rng: MarkovRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate individual packets and gaps in the frozen scalar draw order."""
    checked = validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    timestamps: list[float] = []
    directions: list[Direction] = []
    frame_lengths: list[int] = []
    output_bytes = 0
    diagnostics = {name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS}

    def result(complete: bool, reason: IncompleteReason | None = None) -> GenerationResult:
        return GenerationResult(
            complete=complete,
            trace=make_generation_trace(timestamps, directions, frame_lengths),
            reason=reason,
            model_diagnostics=diagnostics,
        )

    def checked_next_time(current_time: float, gap: float) -> float:
        next_time = current_time + gap
        if not math.isfinite(next_time):
            raise _invalid(
                "arrival time",
                corrective_action="use fitted finite gap reservoirs whose sums do not overflow",
            )
        return next_time

    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return result(False, reason)
    state_draw = rng.random()
    reason = guard.post_draw_reason()
    if reason is not None:
        return result(False, reason)
    state_index = probability_index_from_draw(checked.initial_probabilities, state_draw)
    state = checked.states[state_index]

    length_draw = rng.choice(len(state.actual_lengths))
    reason = guard.post_draw_reason()
    if reason is not None:
        return result(False, reason)
    actual_length = state.actual_lengths[empirical_index_from_draw(len(state.actual_lengths), length_draw)]

    direction, frame_length = state.marks.first.sample(rng)
    reason = guard.post_draw_reason()
    if reason is not None:
        return result(False, reason)
    reason = guard.prospective_reason(0, 0, frame_length)
    if reason is not None:
        return result(False, reason)
    timestamps.append(0.0)
    directions.append(direction)
    frame_lengths.append(frame_length)
    output_bytes = frame_length
    current_time = 0.0

    while True:
        for packet_index in range(1, actual_length):
            reason = guard.pre_draw_reason(len(timestamps), output_bytes)
            if reason is not None:
                return result(False, reason)
            position = position_class(packet_index, actual_length)
            gap_sample = state.within_gaps.for_position(position)
            gap_draw = rng.choice(len(gap_sample))
            reason = guard.post_draw_reason()
            if reason is not None:
                return result(False, reason)
            gap = gap_sample[empirical_index_from_draw(len(gap_sample), gap_draw)]
            next_time = checked_next_time(current_time, gap)
            if next_time > window:
                return result(True)

            direction, frame_length = state.marks.for_position(position).sample(rng)
            reason = guard.post_draw_reason()
            if reason is not None:
                return result(False, reason)
            reason = guard.prospective_reason(len(timestamps), output_bytes, frame_length)
            if reason is not None:
                return result(False, reason)
            timestamps.append(next_time)
            directions.append(direction)
            frame_lengths.append(frame_length)
            output_bytes += frame_length
            current_time = next_time

        reason = guard.pre_draw_reason(len(timestamps), output_bytes)
        if reason is not None:
            return result(False, reason)
        transition_draw = rng.random()
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(False, reason)
        destination_index = probability_index_from_draw(checked.transition_rows[state_index], transition_draw)
        tier, gap_sample = inter_train_gap_selection(
            checked.conditional_inter_train_gaps[state_index][destination_index],
            state.source_inter_train_gaps,
            checked.global_inter_train_gaps,
        )
        diagnostics[f"timing_tier_{tier}_count"] += 1
        if state_index in checked.timing_diagnostics.unobserved_rows:
            diagnostics["uniform_unobserved_row_count"] += 1

        gap_draw = rng.choice(len(gap_sample))
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(False, reason)
        gap = gap_sample[empirical_index_from_draw(len(gap_sample), gap_draw)]
        next_time = checked_next_time(current_time, gap)
        if next_time > window:
            return result(True)

        state_index = destination_index
        state = checked.states[state_index]
        length_draw = rng.choice(len(state.actual_lengths))
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(False, reason)
        actual_length = state.actual_lengths[empirical_index_from_draw(len(state.actual_lengths), length_draw)]
        direction, frame_length = state.marks.first.sample(rng)
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(False, reason)
        reason = guard.prospective_reason(len(timestamps), output_bytes, frame_length)
        if reason is not None:
            return result(False, reason)
        timestamps.append(next_time)
        directions.append(direction)
        frame_lengths.append(frame_length)
        output_bytes += frame_length
        current_time = next_time
