"""Family contract and strict fitted codec for Markov packet trains."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from time import monotonic
from typing import cast

from trafficlab.common.config import (
    FamilyName,
    GeneCoordinateKind,
    GenerationLimits,
    IntegerBounds,
    MarkovPacketTrainConfig,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TrafficTrace
from trafficlab.generation.models.common import (
    FamilyBounds,
    FittedModel,
    Gene,
    GenerationResult,
    Genes,
    MarkCount,
    MarkDistribution,
    make_rng,
    validate_fit_inputs,
)
from trafficlab.generation.models.markov_packet_train.generation import generate_with_rng, validate_model
from trafficlab.generation.models.markov_packet_train.model import (
    GAP_QUANTILE,
    GAP_THRESHOLD_TOLERANCE,
    INSIDE_TRAIN_ENDPOINT,
    TRANSITION_PSEUDOCOUNT,
    MarkovPacketTrainModel,
    PositionMarkPools,
    TrainState,
    TrainTimingDiagnostics,
    WithinGapPools,
    fit_trace,
)
from trafficlab.generation.models.markov_renewal.parameters import type7_quantile


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(f"invalid Markov packet-train {detail}", corrective_action=corrective_action)


def _validate_bounds(value: object) -> MarkovPacketTrainConfig:
    if type(value) is not MarkovPacketTrainConfig:
        raise _invalid(
            "bounds",
            corrective_action="provide configured integer length_cap bounds within 3..8",
        )
    bound = value.length_cap
    if (
        type(bound) is not IntegerBounds
        or type(bound.lower) is not int
        or type(bound.upper) is not int
        or bound.lower < 3
        or bound.upper > 8
        or bound.lower >= bound.upper
    ):
        raise _invalid(
            "length_cap bounds",
            corrective_action="provide ordered exact integer bounds within 3..8",
        )
    return value


def _canonical_genes(genes: Sequence[Gene], bounds: object) -> tuple[int]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise _invalid(
            "length_cap genes",
            corrective_action="provide one exact integer length_cap coordinate",
        ) from error
    if len(values) != 1 or type(values[0]) is not int:
        raise _invalid(
            "length_cap genes",
            corrective_action="provide one exact integer length_cap coordinate",
        )
    return (min(max(values[0], checked_bounds.length_cap.lower), checked_bounds.length_cap.upper),)


def _float_list(value: object, *, context: str) -> tuple[float, ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of finite exact floats")
    items = cast(list[object], value)
    if any(type(item) is not float or not math.isfinite(item) for item in items):
        raise ValueError(f"{context} must be a list of finite exact floats")
    return tuple(cast(list[float], items))


def _int_list(value: object, *, context: str) -> tuple[int, ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of exact integers")
    items = cast(list[object], value)
    if any(type(item) is not int for item in items):
        raise ValueError(f"{context} must be a list of exact integers")
    return tuple(cast(list[int], items))


def _mark_document(distribution: MarkDistribution | None) -> list[dict[str, object]]:
    if distribution is None:
        return []
    return [
        {"direction": entry.direction.value, "frame_length": entry.frame_length, "count": entry.count}
        for entry in distribution.entries
    ]


def _load_marks(value: object, *, context: str, required: bool) -> MarkDistribution | None:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of strict empirical marks")
    entries: list[MarkCount] = []
    for raw in cast(list[object], value):
        if type(raw) is not dict:
            raise ValueError(f"{context} entries must be objects")
        item = cast(dict[str, object], raw)
        if set(item) != {"direction", "frame_length", "count"}:
            raise ValueError(f"{context} entries must contain direction, frame_length, and count")
        direction, frame_length, count = item["direction"], item["frame_length"], item["count"]
        if type(direction) is not str or type(frame_length) is not int or type(count) is not int:
            raise ValueError(f"{context} entries must use exact scalar types")
        entries.append(MarkCount(Direction(direction), frame_length, count))
    if not entries:
        if required:
            raise ValueError(f"{context} must not be empty")
        return None
    return MarkDistribution(tuple(entries))


def _timing_document(diagnostics: TrainTimingDiagnostics) -> dict[str, object]:
    return {
        "reference_usage_counts": {
            "global": diagnostics.reference_global_count,
            "source": diagnostics.reference_source_count,
            "transition": diagnostics.reference_transition_count,
        },
        "transition_tiers": [list(row) for row in diagnostics.transition_tiers],
        "unobserved_rows": list(diagnostics.unobserved_rows),
    }


def _load_state(value: object) -> TrainState:
    if type(value) is not dict:
        raise ValueError("each train state must be an object")
    state = cast(dict[str, object], value)
    expected = {
        "actual_lengths",
        "length_state",
        "marks",
        "source_inter_train_gaps",
        "within_gaps",
    }
    if set(state) != expected:
        raise ValueError("each train state must contain exactly the documented individual reservoirs")
    length_state = state["length_state"]
    if type(length_state) is not int:
        raise ValueError("length_state must be an exact integer")
    marks_value = state["marks"]
    gaps_value = state["within_gaps"]
    if type(marks_value) is not dict or set(cast(dict[object, object], marks_value)) != {"first", "interior", "last"}:
        raise ValueError("state marks must contain exactly first, interior, and last")
    if type(gaps_value) is not dict or set(cast(dict[object, object], gaps_value)) != {"interior", "last"}:
        raise ValueError("within_gaps must contain exactly interior and last")
    marks = cast(dict[str, object], marks_value)
    gaps = cast(dict[str, object], gaps_value)
    return TrainState(
        length_state=length_state,
        actual_lengths=_int_list(state["actual_lengths"], context="state actual_lengths"),
        marks=PositionMarkPools(
            first=cast(MarkDistribution, _load_marks(marks["first"], context="first marks", required=True)),
            interior=_load_marks(marks["interior"], context="interior marks", required=False),
            last=_load_marks(marks["last"], context="last marks", required=False),
        ),
        within_gaps=WithinGapPools(
            interior=_float_list(gaps["interior"], context="interior within gaps"),
            last=_float_list(gaps["last"], context="last within gaps"),
        ),
        source_inter_train_gaps=_float_list(state["source_inter_train_gaps"], context="source inter-train gaps"),
    )


class MarkovPacketTrainFamily:
    """Fit, serialize, and generate the capped-length packet-train family."""

    name: FamilyName = "markov_packet_train"
    gene_names: tuple[str, ...] = ("length_cap",)
    gene_coordinate_kinds: tuple[GeneCoordinateKind, ...] = ("integer",)
    bounds_type = MarkovPacketTrainConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "first_event": "zero",
        "gap_endpoint": "less_than_or_equal",
        "gap_quantile": "type7_linear_0.90",
        "inter_train_timing": "transition_source_global_nonempty",
        "marks": "state_position_joint_empirical_first_appearance",
        "state": "capped_actual_train_length",
        "state_order": "first_appearance",
        "transition": "additive_1_uniform_empty_row",
        "within_train_timing": "state_destination_position_empirical",
    }

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: TrafficTrace) -> Genes:
        del reference
        return _canonical_genes(genes, bounds)

    def fit(
        self,
        reference: TrafficTrace,
        genes: Sequence[Gene],
        *,
        W: float,
        bounds: FamilyBounds,
    ) -> MarkovPacketTrainModel:
        trace = validate_fit_inputs(reference, W=W)
        length_cap = _canonical_genes(genes, bounds)[0]
        try:
            return fit_trace(trace, length_cap=length_cap)
        except (TypeError, ValueError) as error:
            raise _invalid(
                f"reference segmentation: {error}",
                corrective_action="provide a reference whose Type-7 q90 leaves at least one separating gap",
            ) from error

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> GenerationResult:
        if type(seed) is not int or seed < 0:
            raise _invalid(
                "seed",
                corrective_action="provide a nonnegative exact integer generation seed",
            )
        return generate_with_rng(cast(MarkovPacketTrainModel, model), make_rng(seed), W=W, limits=limits, clock=clock)

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        checked = validate_model(model)
        return {
            "conditional_inter_train_gaps": [
                [list(sample) for sample in row] for row in checked.conditional_inter_train_gaps
            ],
            "gap_quantile": checked.gap_quantile,
            "gap_threshold": checked.gap_threshold,
            "global_inter_train_gaps": list(checked.global_inter_train_gaps),
            "initial_probabilities": list(checked.initial_probabilities),
            "inside_train_endpoint": checked.inside_train_endpoint,
            "length_cap": checked.length_cap,
            "states": [
                {
                    "actual_lengths": list(state.actual_lengths),
                    "length_state": state.length_state,
                    "marks": {
                        "first": _mark_document(state.marks.first),
                        "interior": _mark_document(state.marks.interior),
                        "last": _mark_document(state.marks.last),
                    },
                    "source_inter_train_gaps": list(state.source_inter_train_gaps),
                    "within_gaps": {
                        "interior": list(state.within_gaps.interior),
                        "last": list(state.within_gaps.last),
                    },
                }
                for state in checked.states
            ],
            "timing_diagnostics": _timing_document(checked.timing_diagnostics),
            "transition_pseudocount": checked.transition_pseudocount,
            "transition_rows": [list(row) for row in checked.transition_rows],
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> MarkovPacketTrainModel:
        length_cap = _canonical_genes(genes, bounds)[0]
        expected = {
            "conditional_inter_train_gaps",
            "gap_quantile",
            "gap_threshold",
            "global_inter_train_gaps",
            "initial_probabilities",
            "inside_train_endpoint",
            "length_cap",
            "states",
            "timing_diagnostics",
            "transition_pseudocount",
            "transition_rows",
        }
        if type(data) is not dict or set(cast(dict[object, object], data)) != expected:
            raise _invalid(
                "fitted payload",
                corrective_action="provide exactly the documented fitted packet-train JSON fields",
            )
        payload = cast(dict[str, object], data)
        if payload["length_cap"] != length_cap or type(payload["length_cap"]) is not int:
            raise _invalid(
                "length_cap payload",
                corrective_action="bind the fitted length_cap to the repaired outer gene",
            )
        try:
            gap_quantile = payload["gap_quantile"]
            gap_threshold = payload["gap_threshold"]
            endpoint = payload["inside_train_endpoint"]
            pseudocount = payload["transition_pseudocount"]
            if type(gap_quantile) is not float or gap_quantile != GAP_QUANTILE:
                raise ValueError("gap_quantile must equal 0.9")
            if type(gap_threshold) is not float:
                raise ValueError("gap_threshold must be an exact float")
            if endpoint != INSIDE_TRAIN_ENDPOINT or type(endpoint) is not str:
                raise ValueError("endpoint must equal less_than_or_equal")
            if type(pseudocount) is not float or pseudocount != TRANSITION_PSEUDOCOUNT:
                raise ValueError("transition_pseudocount must equal 1.0")
            states_value = payload["states"]
            if type(states_value) is not list or not states_value:
                raise ValueError("states must be a nonempty list")
            states = tuple(_load_state(value) for value in cast(list[object], states_value))
            state_count = len(states)
            conditional_value = payload["conditional_inter_train_gaps"]
            if type(conditional_value) is not list:
                raise ValueError("conditional inter-train gaps must contain K rows")
            conditional_items = cast(list[object], conditional_value)
            if len(conditional_items) != state_count:
                raise ValueError("conditional inter-train gaps must contain K rows")
            conditional: list[tuple[tuple[float, ...], ...]] = []
            for row in conditional_items:
                if type(row) is not list:
                    raise ValueError("conditional inter-train gaps must be a K x K array")
                sample_items = cast(list[object], row)
                if len(sample_items) != state_count:
                    raise ValueError("conditional inter-train gaps must be a K x K array")
                conditional.append(
                    tuple(_float_list(sample, context="conditional inter-train gap sample") for sample in sample_items)
                )
            rows_value = payload["transition_rows"]
            if type(rows_value) is not list:
                raise ValueError("transition_rows must contain K rows")
            row_items = cast(list[object], rows_value)
            if len(row_items) != state_count:
                raise ValueError("transition_rows must contain K rows")
            transition_rows = tuple(_float_list(row, context="transition row") for row in row_items)
            if any(len(row) != state_count for row in transition_rows):
                raise ValueError("transition_rows must be a K x K array")
            model = MarkovPacketTrainModel(
                conditional_inter_train_gaps=tuple(conditional),
                gap_quantile=gap_quantile,
                gap_threshold=gap_threshold,
                global_inter_train_gaps=_float_list(
                    payload["global_inter_train_gaps"], context="global inter-train gaps"
                ),
                initial_probabilities=_float_list(payload["initial_probabilities"], context="initial probabilities"),
                inside_train_endpoint=INSIDE_TRAIN_ENDPOINT,
                length_cap=length_cap,
                states=states,
                transition_pseudocount=pseudocount,
                transition_rows=transition_rows,
            )
            all_iats = (
                tuple(gap for state in states for gap in (*state.within_gaps.interior, *state.within_gaps.last))
                + model.global_inter_train_gaps
            )
            if not all_iats or not math.isclose(
                model.gap_threshold,
                type7_quantile(all_iats, GAP_QUANTILE),
                rel_tol=0.0,
                abs_tol=GAP_THRESHOLD_TOLERANCE,
            ):
                raise ValueError("gap_threshold must equal the Type-7 q90 of all fitted reference gaps")
            if payload["timing_diagnostics"] != _timing_document(model.timing_diagnostics):
                raise ValueError("timing_diagnostics must exactly match fitted gap fallback evidence")
            return model
        except (TypeError, ValueError) as error:
            raise _invalid(
                f"fitted payload: {error}",
                corrective_action="provide complete finite aligned individual packet and gap reservoirs",
            ) from error
