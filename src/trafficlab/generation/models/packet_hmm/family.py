"""Family contract and strict fitted codec for the categorical packet HMM."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from time import monotonic
from typing import cast

from trafficlab.common.config import FamilyName, GeneCoordinateKind, GenerationLimits, IntegerBounds, PacketHmmConfig
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
    validate_fit_inputs,
)
from trafficlab.generation.models.packet_hmm.generation import generate, validate_model
from trafficlab.generation.models.packet_hmm.inference import BaumWelchDiagnostics
from trafficlab.generation.models.packet_hmm.model import (
    ADDITIVE_SMOOTHING,
    CONVERGENCE_TOLERANCE,
    INITIALIZATION,
    MAXIMUM_ITERATIONS,
    PacketCategory,
    PacketHmmModel,
    PacketSample,
    fit_trace,
)


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(f"invalid packet HMM {detail}", corrective_action=corrective_action)


def _validate_bounds(value: object) -> PacketHmmConfig:
    if type(value) is not PacketHmmConfig:
        raise _invalid("bounds", corrective_action="provide configured integer state_count bounds within 2..4")
    bound = value.state_count
    if (
        type(bound) is not IntegerBounds
        or type(bound.lower) is not int
        or type(bound.upper) is not int
        or bound.lower < 2
        or bound.upper > 4
        or bound.lower >= bound.upper
    ):
        raise _invalid(
            "state_count bounds",
            corrective_action="provide ordered exact integer state_count bounds within 2..4",
        )
    return value


def _canonical_genes(genes: Sequence[Gene], bounds: object) -> tuple[int]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise _invalid("state_count genes", corrective_action="provide one exact integer state_count") from error
    if len(values) != 1 or type(values[0]) is not int:
        raise _invalid("state_count genes", corrective_action="provide one exact integer state_count")
    return (min(max(values[0], checked_bounds.state_count.lower), checked_bounds.state_count.upper),)


def _float_list(value: object, *, context: str) -> tuple[float, ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of finite exact floats")
    items = cast(list[object], value)
    if any(type(item) is not float or not math.isfinite(item) for item in items):
        raise ValueError(f"{context} must be a list of finite exact floats")
    return tuple(cast(list[float], items))


def _float_matrix(value: object, *, context: str) -> tuple[tuple[float, ...], ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of rows")
    return tuple(_float_list(row, context=f"{context} row") for row in cast(list[object], value))


def _marks_document(distribution: MarkDistribution) -> list[dict[str, object]]:
    return [
        {"direction": entry.direction.value, "frame_length": entry.frame_length, "count": entry.count}
        for entry in distribution.entries
    ]


def _load_marks(value: object) -> MarkDistribution:
    if type(value) is not list:
        raise ValueError("initial_marks must be a list")
    entries: list[MarkCount] = []
    for raw in cast(list[object], value):
        if type(raw) is not dict or set(cast(dict[object, object], raw)) != {"direction", "frame_length", "count"}:
            raise ValueError("initial mark entries must contain direction, frame_length, and count")
        item = cast(dict[str, object], raw)
        direction, frame_length, count = item["direction"], item["frame_length"], item["count"]
        if type(direction) is not str or type(frame_length) is not int or type(count) is not int:
            raise ValueError("initial mark entries must use exact scalar types")
        entries.append(MarkCount(Direction(direction), frame_length, count))
    return MarkDistribution(tuple(entries))


class PacketHmmFamily:
    """Fit, serialize, and generate the small categorical packet-HMM family."""

    name: FamilyName = "packet_hmm"
    gene_names: tuple[str, ...] = ("state_count",)
    gene_coordinate_kinds: tuple[GeneCoordinateKind, ...] = ("integer",)
    bounds_type = PacketHmmConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "em": "scaled_baum_welch_bounded_100_tolerance_1e-8",
        "emission": "observed_category_additive_0.001",
        "first_event": "zero_empirical_initial_mark",
        "iat_bins": "zero_plus_type7_terciles",
        "initialization": "fixed_cyclic_v1",
        "reservoirs": "individual_raw_category_members",
        "size_bins": "type7_terciles",
        "state_order": "expected_iat_then_emission_transition",
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
    ) -> PacketHmmModel:
        trace = validate_fit_inputs(reference, W=W)
        state_count = _canonical_genes(genes, bounds)[0]
        try:
            return validate_model(fit_trace(trace, state_count=state_count))
        except (TypeError, ValueError, TrafficlabError) as error:
            raise _invalid(
                f"reference fit: {error}",
                corrective_action="provide a finite normalized reference with at least two renderer-compatible packets",
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
        return generate(cast(PacketHmmModel, model), seed, W, limits, clock=clock)

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        checked = validate_model(model)
        return {
            "additive_smoothing": checked.additive_smoothing,
            "convergence_tolerance": checked.convergence_tolerance,
            "diagnostics": {
                "converged": checked.diagnostics.converged,
                "iterations": checked.diagnostics.iterations,
                "log_likelihoods": list(checked.diagnostics.log_likelihoods),
            },
            "emission_rows": [list(row) for row in checked.emission_rows],
            "iat_quantiles": list(checked.iat_quantiles),
            "iat_thresholds": list(checked.iat_thresholds),
            "initial_marks": _marks_document(checked.initial_marks),
            "initial_probabilities": list(checked.initial_probabilities),
            "initialization": checked.initialization,
            "maximum_iterations": checked.maximum_iterations,
            "reservoirs": [
                [{"iat": sample.iat, "frame_length": sample.frame_length} for sample in reservoir]
                for reservoir in checked.reservoirs
            ],
            "size_quantiles": list(checked.size_quantiles),
            "size_thresholds": list(checked.size_thresholds),
            "state_count": checked.state_count,
            "transition_rows": [list(row) for row in checked.transition_rows],
            "vocabulary": [
                {
                    "iat_bin": category.iat_bin,
                    "direction": category.direction.value,
                    "size_bin": category.size_bin,
                }
                for category in checked.vocabulary
            ],
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> PacketHmmModel:
        state_count = _canonical_genes(genes, bounds)[0]
        expected = {
            "additive_smoothing",
            "convergence_tolerance",
            "diagnostics",
            "emission_rows",
            "iat_quantiles",
            "iat_thresholds",
            "initial_marks",
            "initial_probabilities",
            "initialization",
            "maximum_iterations",
            "reservoirs",
            "size_quantiles",
            "size_thresholds",
            "state_count",
            "transition_rows",
            "vocabulary",
        }
        if type(data) is not dict or set(cast(dict[object, object], data)) != expected:
            raise _invalid(
                "fitted payload",
                corrective_action="provide exactly the documented categorical packet-HMM JSON fields",
            )
        payload = cast(dict[str, object], data)
        if type(payload["state_count"]) is not int or payload["state_count"] != state_count:
            raise _invalid(
                "state_count payload", corrective_action="bind fitted state_count to the repaired outer gene"
            )
        try:
            if type(payload["additive_smoothing"]) is not float or payload["additive_smoothing"] != ADDITIVE_SMOOTHING:
                raise ValueError("additive smoothing must equal 0.001")
            if (
                type(payload["convergence_tolerance"]) is not float
                or payload["convergence_tolerance"] != CONVERGENCE_TOLERANCE
            ):
                raise ValueError("convergence tolerance must equal 1e-8")
            if type(payload["maximum_iterations"]) is not int or payload["maximum_iterations"] != MAXIMUM_ITERATIONS:
                raise ValueError("maximum_iterations must equal 100")
            if type(payload["initialization"]) is not str or payload["initialization"] != INITIALIZATION:
                raise ValueError("initialization must equal fixed_cyclic_v1")
            diagnostics_value = payload["diagnostics"]
            if type(diagnostics_value) is not dict or set(cast(dict[object, object], diagnostics_value)) != {
                "converged",
                "iterations",
                "log_likelihoods",
            }:
                raise ValueError("diagnostics must contain converged, iterations, and log_likelihoods")
            diagnostic = cast(dict[str, object], diagnostics_value)
            if type(diagnostic["converged"]) is not bool or type(diagnostic["iterations"]) is not int:
                raise ValueError("diagnostics must use exact bool and integer scalars")
            diagnostics = BaumWelchDiagnostics(
                diagnostic["converged"],
                diagnostic["iterations"],
                _float_list(diagnostic["log_likelihoods"], context="diagnostic log_likelihoods"),
            )
            if not diagnostics.converged:
                raise ValueError("diagnostics must report estimator convergence")
            vocabulary_value = payload["vocabulary"]
            if type(vocabulary_value) is not list or not vocabulary_value:
                raise ValueError("vocabulary must be a nonempty list")
            vocabulary: list[PacketCategory] = []
            for raw in cast(list[object], vocabulary_value):
                if type(raw) is not dict or set(cast(dict[object, object], raw)) != {
                    "iat_bin",
                    "direction",
                    "size_bin",
                }:
                    raise ValueError("vocabulary entries must contain iat_bin, direction, and size_bin")
                item = cast(dict[str, object], raw)
                if (
                    type(item["iat_bin"]) is not int
                    or type(item["direction"]) is not str
                    or type(item["size_bin"]) is not int
                ):
                    raise ValueError("vocabulary entries must use exact scalar types")
                vocabulary.append(PacketCategory(item["iat_bin"], Direction(item["direction"]), item["size_bin"]))
            reservoirs_value = payload["reservoirs"]
            if type(reservoirs_value) is not list:
                raise ValueError("reservoirs must be a list")
            reservoirs: list[tuple[PacketSample, ...]] = []
            for raw_reservoir in cast(list[object], reservoirs_value):
                if type(raw_reservoir) is not list:
                    raise ValueError("each reservoir must be a list")
                samples: list[PacketSample] = []
                for raw in cast(list[object], raw_reservoir):
                    if type(raw) is not dict or set(cast(dict[object, object], raw)) != {"iat", "frame_length"}:
                        raise ValueError("reservoir entries must contain iat and frame_length")
                    item = cast(dict[str, object], raw)
                    if type(item["iat"]) is not float or type(item["frame_length"]) is not int:
                        raise ValueError("reservoir entries must use exact float and integer scalars")
                    samples.append(PacketSample(item["iat"], item["frame_length"]))
                reservoirs.append(tuple(samples))
            iat_quantiles = _float_list(payload["iat_quantiles"], context="IAT quantiles")
            size_quantiles = _float_list(payload["size_quantiles"], context="size quantiles")
            size_thresholds = _float_list(payload["size_thresholds"], context="size thresholds")
            if len(iat_quantiles) != 2 or len(size_quantiles) != 2 or len(size_thresholds) != 2:
                raise ValueError("quantiles and size thresholds must contain two values")
            return PacketHmmModel(
                additive_smoothing=payload["additive_smoothing"],
                convergence_tolerance=payload["convergence_tolerance"],
                diagnostics=diagnostics,
                emission_rows=_float_matrix(payload["emission_rows"], context="emission_rows"),
                iat_quantiles=iat_quantiles,
                iat_thresholds=_float_list(payload["iat_thresholds"], context="IAT thresholds"),
                initial_marks=_load_marks(payload["initial_marks"]),
                initial_probabilities=_float_list(payload["initial_probabilities"], context="initial probabilities"),
                initialization=payload["initialization"],
                maximum_iterations=payload["maximum_iterations"],
                reservoirs=tuple(reservoirs),
                size_quantiles=size_quantiles,
                size_thresholds=size_thresholds,
                state_count=state_count,
                transition_rows=_float_matrix(payload["transition_rows"], context="transition_rows"),
                vocabulary=tuple(vocabulary),
            )
        except (TypeError, ValueError) as error:
            raise _invalid(
                f"fitted payload: {error}",
                corrective_action="provide finite canonical probability tables and individual category reservoirs",
            ) from error


__all__ = ["PacketHmmFamily"]
