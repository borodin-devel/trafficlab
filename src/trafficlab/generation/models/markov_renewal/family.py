"""Empirical Markov renewal traffic model with observable direction-size states."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from time import monotonic
from typing import cast

from trafficlab.common.config import FamilyName, GeneCoordinateKind, GenerationLimits, MarkovRenewalConfig
from trafficlab.common.trace import Direction, TrafficTrace
from trafficlab.generation.models.common import (
    FamilyBounds,
    FittedModel,
    Gene,
    GenerationResult,
    Genes,
    make_rng,
    validate_fit_inputs,
)
from trafficlab.generation.models.markov_renewal.generation import generate_with_rng, validate_model
from trafficlab.generation.models.markov_renewal.model import (
    MarkovRenewalModel,
    MarkovState,
    MarkovTimingDiagnostics,
    fit_trace,
)
from trafficlab.generation.models.markov_renewal.parameters import (
    canonical_genes,
    invalid_markov,
    repair_with_trace,
    type7_quantile,
)
from trafficlab.generation.models.markov_renewal.sampling import TIMING_TIERS


def _load_float_list(value: object, *, context: str) -> tuple[float, ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of finite exact floats")
    items = cast(list[object], value)
    if any(type(item) is not float or not math.isfinite(item) for item in items):
        raise ValueError(f"{context} must be a list of finite exact floats")
    return tuple(cast(list[float], items))


def _load_int_list(value: object, *, context: str) -> tuple[int, ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of exact integers")
    items = cast(list[object], value)
    if any(type(item) is not int for item in items):
        raise ValueError(f"{context} must be a list of exact integers")
    return tuple(cast(list[int], items))


def _timing_diagnostics_document(diagnostics: MarkovTimingDiagnostics) -> dict[str, object]:
    return {
        "reference_usage_counts": {
            "global": diagnostics.reference_global_count,
            "source": diagnostics.reference_source_count,
            "transition": diagnostics.reference_transition_count,
        },
        "transition_tiers": [list(row) for row in diagnostics.transition_tiers],
        "unobserved_rows": list(diagnostics.unobserved_rows),
    }


def _matches_timing_diagnostics(value: object, expected: MarkovTimingDiagnostics) -> bool:
    if type(value) is not dict:
        return False
    document = cast(dict[str, object], value)
    if set(document) != {"reference_usage_counts", "transition_tiers", "unobserved_rows"}:
        return False
    counts = document["reference_usage_counts"]
    if type(counts) is not dict:
        return False
    count_values = cast(dict[object, object], counts)
    if (
        len(count_values) != len(TIMING_TIERS)
        or any(type(name) is not str or name not in TIMING_TIERS for name in count_values)
        or any(type(item) is not int or item < 0 for item in count_values.values())
    ):
        return False
    rows = document["transition_tiers"]
    if type(rows) is not list or any(
        type(row) is not list
        or any(type(tier) is not str or tier not in TIMING_TIERS for tier in cast(list[object], row))
        for row in cast(list[object], rows)
    ):
        return False
    unobserved = document["unobserved_rows"]
    if type(unobserved) is not list or any(
        type(index) is not int or index < 0 for index in cast(list[object], unobserved)
    ):
        return False
    return document == _timing_diagnostics_document(expected)


def _load_state(value: object) -> MarkovState:
    if type(value) is not dict:
        raise ValueError("each state must be an object")
    state = cast(dict[str, object], value)
    if set(state) != {"direction", "frame_lengths", "size_bin", "source_iats"}:
        raise ValueError("each state must contain exactly direction, frame_lengths, size_bin, and source_iats")
    direction = state["direction"]
    size_bin_value = state["size_bin"]
    if type(direction) is not str or type(size_bin_value) is not int:
        raise ValueError("state direction and size_bin must use exact JSON scalar types")
    try:
        parsed_direction = Direction(direction)
    except ValueError as error:
        raise ValueError("state direction must be outbound or inbound") from error
    return MarkovState(
        direction=parsed_direction,
        size_bin=size_bin_value,
        frame_lengths=_load_int_list(state["frame_lengths"], context="state frame_lengths"),
        source_iats=_load_float_list(state["source_iats"], context="state source_iats"),
    )


class MarkovRenewalFamily:
    """Fit, serialize, and generate the observable-state Markov renewal family."""

    name: FamilyName = "markov_renewal"
    gene_names: tuple[str, ...] = ("q1", "q2", "alpha", "r", "c_t")
    gene_coordinate_kinds: tuple[GeneCoordinateKind, ...] = ("linear", "linear", "linear", "integer", "log")
    bounds_type = MarkovRenewalConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "first_event": "zero",
        "quantile": "type7_linear",
        "state_order": "first_appearance",
        "timing": "conditional_source_global",
        "transition": "additive_uniform_empty_row",
    }

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: TrafficTrace) -> Genes:
        """Return a canonical chromosome whose quantiles form distinct reference thresholds."""
        if type(reference) is not TrafficTrace or len(reference) < 2:
            raise invalid_markov(
                "invalid Markov renewal reference",
                corrective_action="provide at least two canonical nondecreasing reference events",
            )
        return repair_with_trace(genes, bounds, reference)

    def fit(
        self, reference: TrafficTrace, genes: Sequence[Gene], *, W: float, bounds: FamilyBounds
    ) -> MarkovRenewalModel:
        """Fit active states, a complete transition matrix, and aligned empirical IAT samples."""
        trace = validate_fit_inputs(reference, W=W)
        repaired = repair_with_trace(genes, bounds, trace)
        return fit_trace(trace, repaired)

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> GenerationResult:
        """Generate reproducibly from one locally owned random stream."""
        if type(seed) is not int or seed < 0:
            raise invalid_markov(
                "invalid Markov renewal seed: it must be a nonnegative exact integer",
                corrective_action="provide a nonnegative integer generation seed",
            )
        return generate_with_rng(cast(MarkovRenewalModel, model), make_rng(seed), W=W, limits=limits, clock=clock)

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        """Return the strict JSON-compatible fitted payload."""
        checked_model = validate_model(model)
        return {
            "alpha": checked_model.alpha,
            "conditional_iats": [[list(sample) for sample in row] for row in checked_model.conditional_iats],
            "global_iats": list(checked_model.global_iats),
            "minimum_support": checked_model.minimum_support,
            "states": [
                {
                    "direction": state.direction.value,
                    "frame_lengths": list(state.frame_lengths),
                    "size_bin": state.size_bin,
                    "source_iats": list(state.source_iats),
                }
                for state in checked_model.states
            ],
            "thresholds": list(checked_model.thresholds),
            "time_scale": checked_model.time_scale,
            "timing_diagnostics": _timing_diagnostics_document(checked_model.timing_diagnostics),
            "transition_rows": [list(row) for row in checked_model.transition_rows],
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> MarkovRenewalModel:
        """Load and validate one strict fitted payload bound to its outer genes."""
        repaired = canonical_genes(genes, bounds)
        expected_keys = {
            "alpha",
            "conditional_iats",
            "global_iats",
            "minimum_support",
            "states",
            "thresholds",
            "time_scale",
            "timing_diagnostics",
            "transition_rows",
        }
        if type(data) is not dict:
            raise invalid_markov(
                "invalid fitted Markov renewal payload",
                corrective_action="provide exactly the documented fitted Markov renewal JSON fields",
            )
        payload = cast(dict[str, object], data)
        if set(payload) != expected_keys:
            raise invalid_markov(
                "invalid fitted Markov renewal payload",
                corrective_action="provide exactly the documented fitted Markov renewal JSON fields",
            )
        alpha = payload["alpha"]
        minimum_support = payload["minimum_support"]
        time_scale = payload["time_scale"]
        if (
            type(alpha) is not float
            or type(minimum_support) is not int
            or type(time_scale) is not float
            or alpha != repaired[2]
            or minimum_support != repaired[3]
            or time_scale != repaired[4]
        ):
            raise invalid_markov(
                "invalid fitted Markov renewal parameters",
                corrective_action="bind alpha, minimum_support, and time_scale to the repaired outer genes",
            )
        try:
            thresholds = _load_float_list(payload["thresholds"], context="thresholds")
            if len(thresholds) != 2:
                raise ValueError("thresholds must contain exactly two values")
            states_data = payload["states"]
            if type(states_data) is not list or not states_data:
                raise ValueError("states must be a nonempty list")
            state_items = cast(list[object], states_data)
            states = tuple(_load_state(value) for value in state_items)
            frame_lengths = tuple(frame_length for state in states for frame_length in state.frame_lengths)
            expected_thresholds = (
                type7_quantile(frame_lengths, repaired[0]),
                type7_quantile(frame_lengths, repaired[1]),
            )
            if thresholds != expected_thresholds:
                raise ValueError("thresholds must equal Type 7 quantiles from the repaired outer q genes")
            state_count = len(states)
            conditional_data = payload["conditional_iats"]
            if type(conditional_data) is not list:
                raise ValueError("conditional_iats must contain K rows")
            conditional_items = cast(list[object], conditional_data)
            if len(conditional_items) != state_count:
                raise ValueError("conditional_iats must contain K rows")
            conditional_rows: list[tuple[tuple[float, ...], ...]] = []
            for row in conditional_items:
                if type(row) is not list:
                    raise ValueError("conditional_iats must be a K x K array")
                samples = cast(list[object], row)
                if len(samples) != state_count:
                    raise ValueError("conditional_iats must be a K x K array")
                conditional_rows.append(
                    tuple(_load_float_list(sample, context="conditional IAT sample") for sample in samples)
                )
            transition_data = payload["transition_rows"]
            if type(transition_data) is not list:
                raise ValueError("transition_rows must contain K rows")
            transition_items = cast(list[object], transition_data)
            if len(transition_items) != state_count:
                raise ValueError("transition_rows must contain K rows")
            transition_rows: list[tuple[float, ...]] = []
            for row in transition_items:
                values = _load_float_list(row, context="transition row")
                if len(values) != state_count:
                    raise ValueError("transition_rows must be a K x K array")
                transition_rows.append(values)
            model = MarkovRenewalModel(
                alpha=alpha,
                conditional_iats=tuple(conditional_rows),
                global_iats=_load_float_list(payload["global_iats"], context="global_iats"),
                minimum_support=minimum_support,
                states=states,
                thresholds=thresholds,
                time_scale=time_scale,
                transition_rows=tuple(transition_rows),
            )
            if not _matches_timing_diagnostics(payload["timing_diagnostics"], model.timing_diagnostics):
                raise ValueError("timing_diagnostics must exactly match the fitted sparse timing evidence")
            return model
        except (TypeError, ValueError) as error:
            raise invalid_markov(
                f"invalid fitted Markov renewal payload: {error}",
                corrective_action="provide a complete finite dimensionally aligned fitted Markov renewal payload",
            ) from error
