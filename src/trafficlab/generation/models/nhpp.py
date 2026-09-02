"""Piecewise-constant nonhomogeneous Poisson traffic model."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, cast

from trafficlab.common.config import FamilyName, GeneCoordinateKind, GenerationLimits, IntegerBounds, NhppConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.common import (
    FamilyBounds,
    FittedModel,
    Gene,
    GenerationGuard,
    GenerationResult,
    Genes,
    IncompleteReason,
    MarkCount,
    MarkDistribution,
    make_generation_trace,
    make_rng,
    validate_fit_inputs,
)


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _validate_window(window: object) -> float:
    if type(window) is not float or not math.isfinite(window) or window <= 0.0:
        raise _invalid(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive normalized observation window",
        )
    return window


def _validate_bounds(bounds: object) -> NhppConfig:
    if type(bounds) is not NhppConfig:
        raise _invalid("invalid NHPP bounds", corrective_action="provide configured NHPP bin_count bounds")
    bin_count = bounds.bin_count
    if type(bin_count) is not IntegerBounds or type(bin_count.lower) is not int or type(bin_count.upper) is not int:
        raise _invalid("invalid NHPP bin_count bounds", corrective_action="provide exact integer bin_count bounds")
    if bin_count.lower < 2 or bin_count.upper > 16 or bin_count.lower > bin_count.upper:
        raise _invalid("invalid NHPP bin_count bounds", corrective_action="provide bin_count bounds within 2..16")
    return bounds


def _repair_genes(genes: Sequence[Gene], bounds: object) -> tuple[int]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise _invalid(
            "invalid NHPP genes: exactly one bin_count value is required",
            corrective_action="provide one exact integer bin_count gene",
        ) from error
    if len(values) != 1 or type(values[0]) is not int:
        raise _invalid(
            "invalid NHPP genes: exactly one exact integer bin_count value is required",
            corrective_action="provide one exact integer bin_count gene",
        )
    return (min(max(values[0], checked_bounds.bin_count.lower), checked_bounds.bin_count.upper),)


@dataclass(frozen=True, slots=True)
class NhppModel:
    """Equal-width rates plus bin-conditioned joint marks and a global fallback."""

    rates: tuple[float, ...]
    bin_marks: tuple[MarkDistribution | None, ...]
    global_marks: MarkDistribution

    def __post_init__(self) -> None:
        if type(self.rates) is not tuple or not self.rates:
            raise ValueError("rates must be a nonempty tuple")
        if type(self.bin_marks) is not tuple or len(self.bin_marks) != len(self.rates):
            raise ValueError("bin_marks must have exactly one table per rate")
        if any(type(rate) is not float or not math.isfinite(rate) or rate < 0.0 for rate in self.rates):
            raise ValueError("rates must be finite nonnegative floats")
        if type(self.global_marks) is not MarkDistribution:
            raise TypeError("global_marks must be a MarkDistribution")
        MarkDistribution(self.global_marks.entries)
        for marks in self.bin_marks:
            if marks is not None:
                if type(marks) is not MarkDistribution:
                    raise TypeError("bin marks must be MarkDistribution values or None")
                MarkDistribution(marks.entries)

    @property
    def family(self) -> FamilyName:
        return "nhpp"


def _validate_model(model: object) -> NhppModel:
    if type(model) is not NhppModel:
        raise TypeError("model must be an NhppModel")
    try:
        return NhppModel(model.rates, model.bin_marks, model.global_marks)
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid fitted NHPP model: {error}",
            corrective_action="load or fit finite nonnegative NHPP rates and empirical marks",
        ) from error


class _NhppRng(Protocol):
    def exponential(self, scale: float) -> float:
        """Return one finite nonnegative delay for a positive scale."""
        ...

    def choice(self, a: int) -> int:
        """Return one empirical-mark index below a positive total."""
        ...


def _validate_delay(delay: object) -> float:
    if type(delay) is not float or not math.isfinite(delay) or delay < 0.0:
        raise _invalid(
            "invalid NHPP random delay",
            corrective_action="use a random generator that returns finite nonnegative exponential delays",
        )
    return delay


def _mark_for_bin(model: NhppModel, bin_index: int) -> MarkDistribution:
    return model.bin_marks[bin_index] or model.global_marks


def _generate_with_rng(
    model: NhppModel,
    rng: _NhppRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate one closed complete window while preserving scalar PCG64 draw order."""
    checked_model = _validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    timestamps: list[float] = []
    directions: list[Direction] = []
    frame_lengths: list[int] = []
    output_bytes = 0

    def result(complete: bool, reason: IncompleteReason | None = None) -> GenerationResult:
        return GenerationResult(
            complete=complete,
            trace=make_generation_trace(timestamps, directions, frame_lengths),
            reason=reason,
        )

    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return result(False, reason)
    direction, frame_length = _mark_for_bin(checked_model, 0).sample(rng)
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

    bin_width = window / len(checked_model.rates)
    current_time = 0.0
    for bin_index, rate in enumerate(checked_model.rates):
        bin_end = window if bin_index == len(checked_model.rates) - 1 else (bin_index + 1) * bin_width
        if rate == 0.0:
            current_time = bin_end
            continue
        while True:
            reason = guard.pre_draw_reason(len(timestamps), output_bytes)
            if reason is not None:
                return result(False, reason)
            raw_delay = rng.exponential(1.0 / rate)
            reason = guard.post_draw_reason()
            if reason is not None:
                return result(False, reason)
            next_time = current_time + _validate_delay(raw_delay)
            if not math.isfinite(next_time):
                raise _invalid(
                    "invalid NHPP arrival time",
                    corrective_action="use finite generation parameters and random delays",
                )
            if next_time > bin_end:
                current_time = bin_end
                break
            if next_time == bin_end and bin_index != len(checked_model.rates) - 1:
                current_time = bin_end
                break
            direction, frame_length = _mark_for_bin(checked_model, bin_index).sample(rng)
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
    return result(True)


def _mark_payload(marks: MarkDistribution) -> list[dict[str, object]]:
    return [
        {"direction": entry.direction.value, "frame_length": entry.frame_length, "count": entry.count}
        for entry in marks.entries
    ]


def _load_marks(value: object, *, name: str, allow_empty: bool) -> MarkDistribution | None:
    if type(value) is not list:
        raise _invalid(f"invalid NHPP {name}", corrective_action="provide a list of strict empirical marks")
    entries: list[MarkCount] = []
    for raw in cast(list[object], value):
        if type(raw) is not dict:
            raise _invalid(f"invalid NHPP {name}", corrective_action="provide strict direction, frame_length, count marks")
        mark = cast(dict[str, object], raw)
        if set(mark) != {"direction", "frame_length", "count"}:
            raise _invalid(f"invalid NHPP {name}", corrective_action="provide strict direction, frame_length, count marks")
        direction, frame_length, count = mark["direction"], mark["frame_length"], mark["count"]
        if type(direction) is not str or type(frame_length) is not int or type(count) is not int:
            raise _invalid(f"invalid NHPP {name}", corrective_action="provide exact empirical mark primitives")
        try:
            entries.append(MarkCount(Direction(direction), frame_length, count))
        except (TypeError, ValueError) as error:
            raise _invalid(f"invalid NHPP {name}: {error}", corrective_action="provide valid empirical marks") from error
    if not entries and allow_empty:
        return None
    try:
        return MarkDistribution(tuple(entries))
    except (TypeError, ValueError) as error:
        raise _invalid(f"invalid NHPP {name}: {error}", corrective_action="provide nonempty unique empirical marks") from error


class NhppFamily:
    """Fit, serialize, and generate the equal-width piecewise NHPP family."""

    name: FamilyName = "nhpp"
    gene_names: tuple[str, ...] = ("bin_count",)
    gene_coordinate_kinds: tuple[GeneCoordinateKind, ...] = ("integer",)
    bounds_type = NhppConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "first_event": "zero",
        "rate": "equal_width_bin_interval_count_over_width",
        "marks": "bin_joint_empirical_first_appearance_global_fallback",
    }

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: TrafficTrace) -> Genes:
        del reference
        return _repair_genes(genes, bounds)

    def fit(self, reference: TrafficTrace, genes: Sequence[Gene], *, W: float, bounds: FamilyBounds) -> NhppModel:
        trace = validate_fit_inputs(reference, W=W)
        bin_count = _repair_genes(genes, bounds)[0]
        width = W / bin_count
        rate_counts = [0] * bin_count
        mark_events: list[list[tuple[Direction, int]]] = [[] for _ in range(bin_count)]
        for index, event in enumerate(trace.to_events()):
            bin_index = min(int(event.timestamp / width), bin_count - 1)
            mark_events[bin_index].append((event.direction, event.frame_length))
            if index != 0:
                rate_counts[bin_index] += 1
        bin_marks = tuple(
            MarkDistribution.from_reference(
                tuple(TraceEvent(0.0, direction, frame_length) for direction, frame_length in entries)
            )
            if entries
            else None
            for entries in mark_events
        )
        return NhppModel(
            rates=tuple(float(count / width) for count in rate_counts),
            bin_marks=bin_marks,
            global_marks=MarkDistribution.from_trace(trace),
        )

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
                "invalid NHPP seed: it must be a nonnegative exact integer",
                corrective_action="provide a nonnegative integer generation seed",
            )
        return _generate_with_rng(cast(NhppModel, model), make_rng(seed), W=W, limits=limits, clock=clock)

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        checked_model = _validate_model(model)
        return {
            "rates": list(checked_model.rates),
            "bin_marks": [_mark_payload(marks) if marks is not None else [] for marks in checked_model.bin_marks],
            "global_marks": _mark_payload(checked_model.global_marks),
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> NhppModel:
        bin_count = _repair_genes(genes, bounds)[0]
        if type(data) is not dict:
            raise _invalid("invalid fitted NHPP payload", corrective_action="provide rates, bin_marks, and global_marks")
        payload = cast(dict[str, object], data)
        if set(payload) != {"rates", "bin_marks", "global_marks"}:
            raise _invalid("invalid fitted NHPP payload", corrective_action="provide rates, bin_marks, and global_marks")
        raw_rates, raw_tables = payload["rates"], payload["bin_marks"]
        if type(raw_rates) is not list or type(raw_tables) is not list:
            raise _invalid("invalid fitted NHPP payload", corrective_action="provide list rates and bin mark tables")
        rates = cast(list[object], raw_rates)
        tables = cast(list[object], raw_tables)
        if len(rates) != bin_count or len(tables) != bin_count:
            raise _invalid("invalid fitted NHPP payload", corrective_action="match rates and bin marks to repaired bin_count")
        if any(type(rate) is not float or not math.isfinite(rate) or rate < 0.0 for rate in rates):
            raise _invalid("invalid fitted NHPP rates", corrective_action="provide finite nonnegative float rates")
        global_marks = _load_marks(payload["global_marks"], name="global_marks", allow_empty=False)
        assert global_marks is not None
        bin_marks = tuple(_load_marks(table, name="bin_marks", allow_empty=True) for table in tables)
        try:
            return NhppModel(tuple(cast(float, rate) for rate in rates), bin_marks, global_marks)
        except (TypeError, ValueError) as error:
            raise _invalid(f"invalid fitted NHPP payload: {error}", corrective_action="provide valid NHPP parameters") from error
