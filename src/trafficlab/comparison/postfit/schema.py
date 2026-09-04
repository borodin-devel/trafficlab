"""Typed final-only post-fit Fano/Allan and transition artifacts."""

import math
from itertools import groupby
from typing import Annotated, Literal, Self, cast

from pydantic import BeforeValidator, Field, StrictInt, model_validator

from trafficlab.comparison.diagnostics import (
    WEIGHT_TOLERANCE,
    FloatTuple,
    NonnegativeFloat,
    NonnegativeInt,
    PositiveFloat,
    PositiveInt,
    StrictArtifactModel,
    UnitFloat,
    require_close,
)
from trafficlab.comparison.similarity.multiscale import snap_near_integer

_MAXIMUM_C2ST_WINDOWS = 65_536
_MAXIMUM_FOLD_INDEX_CELLS = 65_536
_MAXIMUM_FANO_DIRECTION_WINDOW_CELLS = 65_536
_MAXIMUM_TRANSITION_STATES = 256
_MAXIMUM_TRANSITION_CELLS = 65_536


def _tuple_input(value: object) -> object:
    if type(value) is list:
        return tuple(cast(list[object], value))
    return value


type NonnegativeIntTuple = Annotated[tuple[NonnegativeInt, ...], BeforeValidator(_tuple_input)]
type AtLeastTwoInt = Annotated[StrictInt, Field(ge=2)]
type BoundedC2stWindowCount = Annotated[StrictInt, Field(gt=0, le=_MAXIMUM_C2ST_WINDOWS)]
type BoundedFanoDirectionCells = Annotated[
    StrictInt,
    Field(gt=0, le=_MAXIMUM_FANO_DIRECTION_WINDOW_CELLS),
]
type BoundedTransitionStateCount = Annotated[StrictInt, Field(gt=0, le=_MAXIMUM_TRANSITION_STATES)]
type BoundedTransitionCellCount = Annotated[StrictInt, Field(gt=0, le=_MAXIMUM_TRANSITION_CELLS)]
type BoundedTransitionSizeBinCount = Annotated[StrictInt, Field(gt=0, le=30)]
type BoundedTransitionIatBinCount = Annotated[StrictInt, Field(gt=0, le=39)]


def _snapped_window_count(W: float, width: float, *, name: str) -> int:
    quotient = W / width
    if not math.isfinite(quotient):
        raise ValueError(f"{name}: W divided by width must be finite")
    return math.ceil(snap_near_integer(quotient))


maximum_fold_index_cells = _MAXIMUM_FOLD_INDEX_CELLS
snapped_window_count = _snapped_window_count
tuple_input = _tuple_input


def _require_normalized_postfit(values: tuple[float, ...], *, name: str) -> None:
    if any(not 0.0 <= value <= 1.0 for value in values) or not math.isclose(
        math.fsum(values), 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE
    ):
        raise ValueError(f"{name} must be nonnegative and sum to one")


class DispersionChannelCounts(StrictArtifactModel):
    total: NonnegativeIntTuple
    outbound: NonnegativeIntTuple
    inbound: NonnegativeIntTuple


class DispersionChannelValues(StrictArtifactModel):
    total: NonnegativeFloat
    outbound: NonnegativeFloat
    inbound: NonnegativeFloat


class DispersionComponentValues(StrictArtifactModel):
    fano: UnitFloat
    allan: UnitFloat


def _dispersion_curve(counts: tuple[int, ...]) -> tuple[float, float]:
    mean = math.fsum(counts) / len(counts)
    if mean == 0.0:
        return (0.0, 0.0)
    fano = math.fsum((count - mean) ** 2 for count in counts) / len(counts) / mean
    allan = math.fsum((right - left) ** 2 for left, right in zip(counts, counts[1:], strict=False))
    return (fano, allan / ((len(counts) - 1) * 2.0 * mean))


def _dispersion_difference(reference: float, generated: float) -> float:
    left = math.log1p(reference)
    right = math.log1p(generated)
    return 0.0 if left + right == 0.0 else abs(left - right) / (left + right)


class FanoAllanScaleDiagnostic(StrictArtifactModel):
    width_seconds: PositiveFloat
    window_count: AtLeastTwoInt
    reference_counts: DispersionChannelCounts
    generated_counts: DispersionChannelCounts
    reference_fano: DispersionChannelValues
    generated_fano: DispersionChannelValues
    reference_allan: DispersionChannelValues
    generated_allan: DispersionChannelValues
    component_differences: DispersionComponentValues
    discrepancy: UnitFloat

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        for name, counts in (("reference", self.reference_counts), ("generated", self.generated_counts)):
            if not (len(counts.total) == len(counts.outbound) == len(counts.inbound) == self.window_count):
                raise ValueError(f"Fano/Allan {name} count vectors must match window_count")
            if any(
                total != outbound + inbound
                for total, outbound, inbound in zip(counts.total, counts.outbound, counts.inbound, strict=True)
            ):
                raise ValueError(f"Fano/Allan {name} total counts must equal directional counts")
        fano_differences: list[float] = []
        allan_differences: list[float] = []
        for channel in ("total", "outbound", "inbound"):
            reference_fano, reference_allan = _dispersion_curve(getattr(self.reference_counts, channel))
            generated_fano, generated_allan = _dispersion_curve(getattr(self.generated_counts, channel))
            require_close(getattr(self.reference_fano, channel), reference_fano, name=f"reference {channel} Fano")
            require_close(getattr(self.generated_fano, channel), generated_fano, name=f"generated {channel} Fano")
            require_close(getattr(self.reference_allan, channel), reference_allan, name=f"reference {channel} Allan")
            require_close(getattr(self.generated_allan, channel), generated_allan, name=f"generated {channel} Allan")
            fano_differences.append(_dispersion_difference(reference_fano, generated_fano))
            allan_differences.append(_dispersion_difference(reference_allan, generated_allan))
        require_close(
            self.component_differences.fano,
            math.fsum(fano_differences) / 3.0,
            name="Fano/Allan scale Fano difference",
        )
        require_close(
            self.component_differences.allan,
            math.fsum(allan_differences) / 3.0,
            name="Fano/Allan scale Allan difference",
        )
        return self


class FanoAllanDiagnostic(StrictArtifactModel):
    observation_window_seconds: PositiveFloat
    widths: FloatTuple
    scale_weights: FloatTuple
    component_weights: DispersionComponentValues
    total_direction_window_cells: BoundedFanoDirectionCells
    scales: Annotated[tuple[FanoAllanScaleDiagnostic, ...], BeforeValidator(_tuple_input)]
    component_differences: DispersionComponentValues
    scale_differences: FloatTuple
    discrepancy: UnitFloat

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        if (
            not self.widths
            or any(width <= 0.0 or width > self.observation_window_seconds for width in self.widths)
            or any(left >= right for left, right in zip(self.widths, self.widths[1:], strict=False))
            or not (len(self.widths) == len(self.scale_weights) == len(self.scales) == len(self.scale_differences))
        ):
            raise ValueError("Fano/Allan widths must be ordered within W and match weights, scales, and differences")
        _require_normalized_postfit(self.scale_weights, name="Fano/Allan scale weights")
        _require_normalized_postfit(
            (self.component_weights.fano, self.component_weights.allan),
            name="Fano/Allan component weights",
        )
        expected_window_counts = tuple(
            _snapped_window_count(self.observation_window_seconds, width, name="Fano/Allan") for width in self.widths
        )
        if any(count < 2 for count in expected_window_counts):
            raise ValueError("Fano/Allan each width must yield at least two windows")
        if tuple(scale.window_count for scale in self.scales) != expected_window_counts:
            raise ValueError("Fano/Allan window counts must equal ceil(snap(W / width))")
        expected_direction_cells = 2 * sum(expected_window_counts)
        if expected_direction_cells > _MAXIMUM_FANO_DIRECTION_WINDOW_CELLS:
            raise ValueError("Fano/Allan direction-window cells exceed the fixed cap")
        if self.total_direction_window_cells != expected_direction_cells:
            raise ValueError("Fano/Allan direction-window cells are inconsistent")
        for index, scale in enumerate(self.scales):
            if scale.width_seconds != self.widths[index] or scale.discrepancy != self.scale_differences[index]:
                raise ValueError("Fano/Allan retained scales are inconsistent with their vectors")
            expected = math.fsum(
                (
                    self.component_weights.fano * scale.component_differences.fano,
                    self.component_weights.allan * scale.component_differences.allan,
                )
            )
            require_close(scale.discrepancy, expected, name=f"Fano/Allan scale {index} discrepancy")
        fano = math.fsum(
            weight * scale.component_differences.fano
            for weight, scale in zip(self.scale_weights, self.scales, strict=True)
        )
        allan = math.fsum(
            weight * scale.component_differences.allan
            for weight, scale in zip(self.scale_weights, self.scales, strict=True)
        )
        require_close(self.component_differences.fano, fano, name="Fano/Allan Fano difference")
        require_close(self.component_differences.allan, allan, name="Fano/Allan Allan difference")
        expected = math.fsum(
            (
                self.component_weights.fano * fano,
                self.component_weights.allan * allan,
            )
        )
        require_close(self.discrepancy, expected, name="Fano/Allan discrepancy")
        return self


type TransitionCategory = NonnegativeInt | Literal["initial", "below", "above"]
type TransitionState = Annotated[
    tuple[Literal["outbound", "inbound"], TransitionCategory, TransitionCategory],
    BeforeValidator(_tuple_input),
]
type TransitionStates = Annotated[tuple[TransitionState, ...], BeforeValidator(_tuple_input)]
type CountRows = Annotated[tuple[NonnegativeIntTuple, ...], BeforeValidator(_tuple_input)]


def _canonical_transition_vocabulary(size_bin_count: int, iat_bin_count: int) -> tuple[TransitionState, ...]:
    size_categories: tuple[TransitionCategory, ...] = ("below", *range(size_bin_count), "above")
    iat_categories: tuple[TransitionCategory, ...] = ("initial", "below", *range(iat_bin_count), "above")
    return cast(
        tuple[TransitionState, ...],
        tuple(
            (direction, size_category, iat_category)
            for direction in ("outbound", "inbound")
            for size_category in size_categories
            for iat_category in iat_categories
        ),
    )


class TransitionComponentValues(StrictArtifactModel):
    occupancy: UnitFloat
    transition_rows: UnitFloat
    runs: UnitFloat


def _smoothed_probabilities(counts: tuple[int, ...], pseudocount: float) -> tuple[float, ...]:
    try:
        total = float(sum(counts))
        numeric_counts = tuple(float(count) for count in counts)
    except (OverflowError, ValueError) as error:
        raise ValueError("transition pseudocount cannot be evaluated safely") from error
    denominator = total + pseudocount * len(counts)
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError("transition pseudocount cannot be evaluated safely")
    numerators = tuple(count + pseudocount for count in numeric_counts)
    if any(not math.isfinite(numerator) or numerator <= 0.0 for numerator in numerators):
        raise ValueError("transition pseudocount cannot be evaluated safely")
    probabilities = tuple(numerator / denominator for numerator in numerators)
    if any(not math.isfinite(probability) or probability <= 0.0 for probability in probabilities):
        raise ValueError("transition pseudocount cannot be evaluated safely")
    return probabilities


smoothed_probabilities = _smoothed_probabilities


def _smoothed_jsd(reference: tuple[int, ...], generated: tuple[int, ...], pseudocount: float) -> float:
    left = _smoothed_probabilities(reference, pseudocount)
    right = _smoothed_probabilities(generated, pseudocount)
    terms: list[float] = []
    for p, q in zip(left, right, strict=True):
        midpoint = (p + q) / 2.0
        terms.extend((0.5 * p * math.log2(p / midpoint), 0.5 * q * math.log2(q / midpoint)))
    return math.fsum(terms)


def _require_probability_vector(actual: tuple[float, ...], expected: tuple[float, ...], *, name: str) -> None:
    if len(actual) != len(expected):
        raise ValueError(f"{name} length is inconsistent")
    for index, (value, wanted) in enumerate(zip(actual, expected, strict=True)):
        require_close(value, wanted, name=f"{name} {index}")


class TransitionOccupancyDiagnostic(StrictArtifactModel):
    reference_counts: NonnegativeIntTuple
    generated_counts: NonnegativeIntTuple
    reference_probabilities: FloatTuple
    generated_probabilities: FloatTuple
    jsd: UnitFloat


class TransitionRowDiagnostic(StrictArtifactModel):
    source: TransitionState
    reference_probabilities: FloatTuple
    generated_probabilities: FloatTuple
    jsd: UnitFloat


class TransitionRowsDiagnostic(StrictArtifactModel):
    reference_counts: CountRows
    generated_counts: CountRows
    rows: Annotated[tuple[TransitionRowDiagnostic, ...], BeforeValidator(_tuple_input)]
    jsd: UnitFloat


type RunCategory = PositiveInt | Literal["overflow"]
type RunVocabulary = Annotated[tuple[RunCategory, ...], BeforeValidator(_tuple_input)]


class TransitionRunsDiagnostic(StrictArtifactModel):
    vocabulary: RunVocabulary
    reference_counts: NonnegativeIntTuple
    generated_counts: NonnegativeIntTuple
    reference_probabilities: FloatTuple
    generated_probabilities: FloatTuple
    jsd: UnitFloat


def _state_run_counts(states: tuple[TransitionState, ...], maximum: int) -> tuple[int, ...]:
    counts = [0] * (maximum + 1)
    run_length = 1
    for source, destination in zip(states, states[1:], strict=False):
        if source == destination:
            run_length += 1
        else:
            counts[run_length - 1 if run_length <= maximum else maximum] += 1
            run_length = 1
    counts[run_length - 1 if run_length <= maximum else maximum] += 1
    return tuple(counts)


class TransitionMatrixDiagnostic(StrictArtifactModel):
    observation_window_seconds: PositiveFloat
    size_bin_count: BoundedTransitionSizeBinCount
    iat_bin_count: BoundedTransitionIatBinCount
    log_size_thresholds: FloatTuple
    log_iat_thresholds: FloatTuple
    pseudocount: PositiveFloat
    component_weights: TransitionComponentValues
    active_state_count: BoundedTransitionStateCount
    transition_cell_count: BoundedTransitionCellCount
    vocabulary: TransitionStates
    reference_states: TransitionStates
    generated_states: TransitionStates
    occupancy: TransitionOccupancyDiagnostic
    transitions: TransitionRowsDiagnostic
    runs: TransitionRunsDiagnostic
    component_jsd: TransitionComponentValues
    discrepancy: UnitFloat

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        if len(self.log_size_thresholds) != self.size_bin_count + 1:
            raise ValueError("transition size thresholds must match size_bin_count")
        if len(self.log_iat_thresholds) != self.iat_bin_count + 1:
            raise ValueError("transition IAT thresholds must match iat_bin_count")
        if (
            any(value < 0.0 for value in (*self.log_size_thresholds, *self.log_iat_thresholds))
            or any(
                left > right
                for left, right in zip(self.log_size_thresholds, self.log_size_thresholds[1:], strict=False)
            )
            or any(
                left > right for left, right in zip(self.log_iat_thresholds, self.log_iat_thresholds[1:], strict=False)
            )
        ):
            raise ValueError("transition thresholds must be finite nonnegative and nondecreasing")
        expected_state_count = 2 * (self.size_bin_count + 2) * (self.iat_bin_count + 3)
        expected_cell_count = expected_state_count * expected_state_count
        if expected_state_count > _MAXIMUM_TRANSITION_STATES or expected_cell_count > _MAXIMUM_TRANSITION_CELLS:
            raise ValueError("transition configured vocabulary exceeds the state or cell cap")
        expected_vocabulary = _canonical_transition_vocabulary(self.size_bin_count, self.iat_bin_count)
        if self.vocabulary != expected_vocabulary:
            raise ValueError("transition vocabulary must equal the exact ordered Cartesian definition")
        if self.active_state_count != expected_state_count:
            raise ValueError("transition active_state_count must match the canonical vocabulary")
        if self.transition_cell_count != expected_cell_count:
            raise ValueError("transition cell count must equal the canonical vocabulary square")
        if len(self.reference_states) < 2 or len(self.generated_states) < 2:
            raise ValueError("transition diagnostics require at least two states per trace")
        vocabulary = set(self.vocabulary)
        if any(state not in vocabulary for state in (*self.reference_states, *self.generated_states)):
            raise ValueError("transition state sequence contains a state outside the vocabulary")
        if not (
            len(self.occupancy.reference_counts)
            == len(self.occupancy.generated_counts)
            == len(self.occupancy.reference_probabilities)
            == len(self.occupancy.generated_probabilities)
            == expected_state_count
        ):
            raise ValueError("transition occupancy vectors must match the canonical vocabulary")
        if not (
            len(self.transitions.reference_counts)
            == len(self.transitions.generated_counts)
            == len(self.transitions.rows)
            == expected_state_count
        ) or any(
            len(reference_row) != expected_state_count
            or len(generated_row) != expected_state_count
            or len(row.reference_probabilities) != expected_state_count
            or len(row.generated_probabilities) != expected_state_count
            for reference_row, generated_row, row in zip(
                self.transitions.reference_counts,
                self.transitions.generated_counts,
                self.transitions.rows,
                strict=True,
            )
        ):
            raise ValueError("transition count and probability rows must match the canonical vocabulary square")
        indexes = {state: index for index, state in enumerate(self.vocabulary)}
        expected_reference_occupancy = [0] * self.active_state_count
        expected_generated_occupancy = [0] * self.active_state_count
        expected_reference_transitions = [[0] * self.active_state_count for _ in self.vocabulary]
        expected_generated_transitions = [[0] * self.active_state_count for _ in self.vocabulary]
        for states, occupancy, transitions in (
            (self.reference_states, expected_reference_occupancy, expected_reference_transitions),
            (self.generated_states, expected_generated_occupancy, expected_generated_transitions),
        ):
            for state in states:
                occupancy[indexes[state]] += 1
            for source, destination in zip(states, states[1:], strict=False):
                transitions[indexes[source]][indexes[destination]] += 1
        if self.occupancy.reference_counts != tuple(
            expected_reference_occupancy
        ) or self.occupancy.generated_counts != tuple(expected_generated_occupancy):
            raise ValueError("transition occupancy counts must equal retained state sequences")
        if self.transitions.reference_counts != tuple(
            map(tuple, expected_reference_transitions)
        ) or self.transitions.generated_counts != tuple(map(tuple, expected_generated_transitions)):
            raise ValueError("transition count rows must equal retained state sequences")
        _require_smoothed_component(
            self.occupancy.reference_counts,
            self.occupancy.generated_counts,
            self.occupancy.reference_probabilities,
            self.occupancy.generated_probabilities,
            self.occupancy.jsd,
            self.pseudocount,
            name="transition occupancy",
        )
        row_jsds: list[float] = []
        for index, row in enumerate(self.transitions.rows):
            reference_counts = self.transitions.reference_counts[index]
            generated_counts = self.transitions.generated_counts[index]
            if row.source != self.vocabulary[index]:
                raise ValueError("transition row sources must follow vocabulary order")
            _require_smoothed_component(
                reference_counts,
                generated_counts,
                row.reference_probabilities,
                row.generated_probabilities,
                row.jsd,
                self.pseudocount,
                name=f"transition row {index}",
            )
            row_jsds.append(row.jsd)
        require_close(
            self.transitions.jsd,
            math.fsum(row_jsds) / len(row_jsds),
            name="transition rows JSD",
        )
        maximum_run = max(
            (sum(1 for _ in group) for _state, group in groupby(self.reference_states)),
            default=1,
        )
        if self.runs.vocabulary != (*range(1, maximum_run + 1), "overflow"):
            raise ValueError("transition run vocabulary must be frozen from the reference maximum")
        if self.runs.reference_counts != _state_run_counts(
            self.reference_states, maximum_run
        ) or self.runs.generated_counts != _state_run_counts(self.generated_states, maximum_run):
            raise ValueError("transition run counts must equal retained state sequences")
        _require_smoothed_component(
            self.runs.reference_counts,
            self.runs.generated_counts,
            self.runs.reference_probabilities,
            self.runs.generated_probabilities,
            self.runs.jsd,
            self.pseudocount,
            name="transition runs",
        )
        if not len(self.runs.vocabulary) == len(self.runs.reference_counts) == len(self.runs.generated_counts):
            raise ValueError("transition run vectors must match the run vocabulary")
        values = (self.component_weights.occupancy, self.component_weights.transition_rows, self.component_weights.runs)
        _require_normalized_postfit(values, name="transition component weights")
        if self.component_jsd != TransitionComponentValues(
            occupancy=self.occupancy.jsd,
            transition_rows=self.transitions.jsd,
            runs=self.runs.jsd,
        ):
            raise ValueError("transition component JSD values must match retained components")
        expected = math.fsum(
            (
                values[0] * self.occupancy.jsd,
                values[1] * self.transitions.jsd,
                values[2] * self.runs.jsd,
            )
        )
        require_close(self.discrepancy, expected, name="transition discrepancy")
        return self


def _require_smoothed_component(
    reference_counts: tuple[int, ...],
    generated_counts: tuple[int, ...],
    reference_probabilities: tuple[float, ...],
    generated_probabilities: tuple[float, ...],
    jsd: float,
    pseudocount: float,
    *,
    name: str,
) -> None:
    expected_reference = _smoothed_probabilities(reference_counts, pseudocount)
    expected_generated = _smoothed_probabilities(generated_counts, pseudocount)
    _require_probability_vector(reference_probabilities, expected_reference, name=f"{name} reference probabilities")
    _require_probability_vector(generated_probabilities, expected_generated, name=f"{name} generated probabilities")
    require_close(jsd, _smoothed_jsd(reference_counts, generated_counts, pseudocount), name=f"{name} JSD")
