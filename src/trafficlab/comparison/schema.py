"""Traffic comparison schema ownership."""

import math
from collections.abc import Mapping
from itertools import groupby
from types import MappingProxyType
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.comparison.diagnostics import (
    FITNESS_METHOD_NAMES,
    WEIGHT_TOLERANCE,
    AndersonDarlingDiagnostic,
    ApproximateMmdDiagnostic,
    AutocorrelationDiagnostic,
    CramerVonMisesDiagnostic,
    ExactFloat,
    FloatTuple,
    FrameSizeDiagnostic,
    IatDiagnostic,
    JensenShannonDiagnostic,
    MethodDiagnostic,
    MultiscaleDiagnostic,
    NonnegativeFloat,
    NonnegativeInt,
    PositiveFloat,
    PositiveInt,
    StrictArtifactModel,
    UnitFloat,
    diagnostic_discriminator,
    require_close,
)
from trafficlab.comparison.similarity.common import JsonValue, SimilarityResult
from trafficlab.comparison.similarity.multiscale import snap_near_integer

INPUT_NAMES = ("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings")
POSTFIT_NAMES = ("fano_allan", "transition_matrix", "classical_c2st")
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


def _snapped_window_count(W: float, width: float, *, name: str) -> int:
    quotient = W / width
    if not math.isfinite(quotient):
        raise ValueError(f"{name}: W divided by width must be finite")
    return math.ceil(snap_near_integer(quotient))


class MethodComparison(StrictArtifactModel):
    """One configured method's immutable score, weight, and retained diagnostics."""

    score: UnitFloat
    weight: UnitFloat
    diagnostics: MethodDiagnostic

    @field_validator("diagnostics", mode="before")
    @classmethod
    def thaw_diagnostics(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return SimilarityResult(0.0, cast(Mapping[str, object], value)).as_dict()["diagnostics"]

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        diagnostic_tag = diagnostic_discriminator(self.diagnostics)
        if diagnostic_tag is None:
            raise ValueError("diagnostics must identify one supported method")
        discrepancy = (
            self.diagnostics.distance
            if isinstance(self.diagnostics, (FrameSizeDiagnostic, IatDiagnostic))
            else self.diagnostics.discrepancy
        )
        require_close(self.score, 1.0 - discrepancy, name=f"{diagnostic_tag} score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a fresh ordinary JSON representation."""
        return {
            "diagnostics": cast(dict[str, JsonValue], self.diagnostics.model_dump(mode="json")),
            "score": self.score,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, method_name: str, value: object) -> Self:
        """Strictly validate one method object from parsed JSON."""
        if method_name not in FITNESS_METHOD_NAMES:
            raise ValueError(f"unsupported comparison method {method_name!r}")
        prepared: object = value
        if type(value) is dict:
            fields = dict(cast(dict[str, object], value))
            if "method" in fields:
                fields["_persisted_method"] = fields.pop("method")
            prepared = fields
        try:
            result = cls.model_validate(prepared)
            if diagnostic_discriminator(result.diagnostics) != method_name:
                raise ValueError(f"{method_name} diagnostics use the wrong method discriminator")
            return result
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            if field.endswith("observation_window_seconds"):
                raise ValueError(
                    "every method diagnostic observation window must be a finite positive float"
                ) from error
            if field.endswith("reference_count") and first["type"] == "int_type":
                raise ValueError("reference_count must be an integer") from error
            raise ValueError(f"invalid method result {field}: {first['msg']}") from error


class ComparisonMethods(StrictArtifactModel):
    autocorrelation: MethodComparison
    frame_size_ks: MethodComparison
    iat_ks: MethodComparison
    multiscale_rate: MethodComparison
    cramer_von_mises: MethodComparison
    anderson_darling: MethodComparison
    jensen_shannon: MethodComparison
    approximate_mmd: MethodComparison

    @field_validator(
        "autocorrelation",
        "frame_size_ks",
        "iat_ks",
        "multiscale_rate",
        "cramer_von_mises",
        "anderson_darling",
        "jensen_shannon",
        "approximate_mmd",
        mode="before",
    )
    @classmethod
    def methods_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return value

    @model_validator(mode="after")
    def mapping_keys_match_diagnostics(self) -> Self:
        for name, method in self.items():
            if diagnostic_discriminator(method.diagnostics) != name:
                raise ValueError(f"{name} diagnostics use the wrong method discriminator")
        return self

    def __getitem__(self, name: str) -> MethodComparison:
        if name not in FITNESS_METHOD_NAMES:
            raise KeyError(name)
        return cast(MethodComparison, getattr(self, name))

    def keys(self) -> tuple[str, ...]:
        return FITNESS_METHOD_NAMES

    def items(self) -> tuple[tuple[str, MethodComparison], ...]:
        return tuple((name, self[name]) for name in FITNESS_METHOD_NAMES)

    def values(self) -> tuple[MethodComparison, ...]:
        return tuple(self[name] for name in FITNESS_METHOD_NAMES)


class ContentIdentityPayload(StrictArtifactModel):
    size: NonnegativeInt
    sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class ComparisonInputIdentities(StrictArtifactModel):
    capture_json: ContentIdentityPayload
    generated_pcapng: ContentIdentityPayload
    reference_pcapng: ContentIdentityPayload
    similarity_settings: ContentIdentityPayload

    @field_validator("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings", mode="before")
    @classmethod
    def identities_are_reconstructed_from_primitives(cls, value: object) -> object:
        if type(value) is ContentIdentity:
            return value.as_dict()
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return value

    def __getitem__(self, name: str) -> ContentIdentityPayload:
        if name not in INPUT_NAMES:
            raise KeyError(name)
        return cast(ContentIdentityPayload, getattr(self, name))

    def keys(self) -> tuple[str, ...]:
        return INPUT_NAMES

    def items(self) -> tuple[tuple[str, ContentIdentityPayload], ...]:
        return tuple((name, self[name]) for name in INPUT_NAMES)

    def as_content_identities(self) -> dict[str, ContentIdentity]:
        return {name: ContentIdentity(size=identity.size, sha256=identity.sha256) for name, identity in self.items()}


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
            if not (
                len(counts.total) == len(counts.outbound) == len(counts.inbound) == self.window_count
            ):
                raise ValueError(f"Fano/Allan {name} count vectors must match window_count")
            if any(total != outbound + inbound for total, outbound, inbound in zip(
                counts.total, counts.outbound, counts.inbound, strict=True
            )):
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
            _snapped_window_count(self.observation_window_seconds, width, name="Fano/Allan")
            for width in self.widths
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
    denominator = sum(counts) + pseudocount * len(counts)
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError("transition pseudocount cannot be evaluated safely")
    probabilities = tuple((count + pseudocount) / denominator for count in counts)
    if any(not math.isfinite(probability) or probability <= 0.0 for probability in probabilities):
        raise ValueError("transition pseudocount cannot be evaluated safely")
    return probabilities


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
    size_bin_count: PositiveInt
    iat_bin_count: PositiveInt
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
            or any(left > right for left, right in zip(self.log_size_thresholds, self.log_size_thresholds[1:], strict=False))
            or any(left > right for left, right in zip(self.log_iat_thresholds, self.log_iat_thresholds[1:], strict=False))
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
        if self.occupancy.reference_counts != tuple(expected_reference_occupancy) or self.occupancy.generated_counts != tuple(
            expected_generated_occupancy
        ):
            raise ValueError("transition occupancy counts must equal retained state sequences")
        if self.transitions.reference_counts != tuple(map(tuple, expected_reference_transitions)) or self.transitions.generated_counts != tuple(
            map(tuple, expected_generated_transitions)
        ):
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
            (
                sum(1 for _ in group)
                for _state, group in groupby(self.reference_states)
            ),
            default=1,
        )
        if self.runs.vocabulary != (*range(1, maximum_run + 1), "overflow"):
            raise ValueError("transition run vocabulary must be frozen from the reference maximum")
        if self.runs.reference_counts != _state_run_counts(self.reference_states, maximum_run) or self.runs.generated_counts != _state_run_counts(
            self.generated_states, maximum_run
        ):
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


_C2ST_FEATURE_NAMES = (
    "outbound_packet_count",
    "inbound_packet_count",
    "outbound_byte_count",
    "inbound_byte_count",
    "frame_size_mean",
    "frame_size_q25",
    "frame_size_q50",
    "frame_size_q75",
    "positive_iat_mean",
    "positive_iat_q25",
    "positive_iat_q50",
    "positive_iat_q75",
    "zero_iat_count",
    "activity_count",
)


class C2stFoldDiagnostic(StrictArtifactModel):
    fold_index: NonnegativeInt
    training_window_indexes: NonnegativeIntTuple
    guard_window_indexes: NonnegativeIntTuple
    evaluation_window_indexes: NonnegativeIntTuple
    training_reference_count: PositiveInt
    training_generated_count: PositiveInt
    evaluation_reference_count: PositiveInt
    evaluation_generated_count: PositiveInt
    reference_training_mean: FloatTuple
    reference_training_scale: FloatTuple
    intercept: ExactFloat
    coefficients: FloatTuple
    iterations: NonnegativeInt
    final_loss: NonnegativeFloat
    converged: StrictBool
    auc: UnitFloat
    balanced_accuracy: UnitFloat

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        training = set(self.training_window_indexes)
        guard = set(self.guard_window_indexes)
        evaluation = set(self.evaluation_window_indexes)
        if (
            len(training) != len(self.training_window_indexes)
            or len(guard) != len(self.guard_window_indexes)
            or len(evaluation) != len(self.evaluation_window_indexes)
            or training & guard
            or training & evaluation
            or guard & evaluation
        ):
            raise ValueError("C2ST fold indexes must be unique and pairwise disjoint")
        if self.training_reference_count != len(training) or self.training_generated_count != len(training):
            raise ValueError("C2ST fold training labels must be balanced over training indexes")
        if self.evaluation_reference_count != len(evaluation) or self.evaluation_generated_count != len(evaluation):
            raise ValueError("C2ST fold evaluation labels must be balanced over evaluation indexes")
        if not self.converged:
            raise ValueError("C2ST every retained fold must have converged")
        return self


class C2stDiagnostic(StrictArtifactModel):
    observation_window_seconds: PositiveFloat
    feature_version: Literal["window-v1"]
    feature_names: Annotated[tuple[StrictStr, ...], BeforeValidator(_tuple_input)]
    window_width_seconds: PositiveFloat
    window_count_per_trace: BoundedC2stWindowCount
    fold_count: AtLeastTwoInt
    guard_window_count: NonnegativeInt
    maximum_window_count: BoundedC2stWindowCount
    l2_regularization: PositiveFloat
    maximum_iterations: PositiveInt
    tolerance: PositiveFloat
    solver: Literal["scipy.optimize.minimize/L-BFGS-B"]
    intercept: ExactFloat
    coefficients: FloatTuple
    folds: Annotated[tuple[C2stFoldDiagnostic, ...], BeforeValidator(_tuple_input)]
    out_of_fold_reference_count: PositiveInt
    out_of_fold_generated_count: PositiveInt
    balanced_accuracy: UnitFloat
    auc: UnitFloat

    @model_validator(mode="after")
    def validate_solver_evidence(self) -> Self:
        if self.feature_names != _C2ST_FEATURE_NAMES:
            raise ValueError("C2ST feature_names must equal the frozen window-v1 feature order")
        expected_window_count = _snapped_window_count(
            self.observation_window_seconds,
            self.window_width_seconds,
            name="C2ST",
        )
        if self.window_count_per_trace != expected_window_count:
            raise ValueError("C2ST window_count_per_trace must equal ceil(snap(W / width))")
        if self.window_count_per_trace > self.maximum_window_count:
            raise ValueError("C2ST window count exceeds the retained cap")
        if self.fold_count > self.window_count_per_trace:
            raise ValueError("C2ST fold_count must not exceed the window count")
        if self.fold_count * self.window_count_per_trace > _MAXIMUM_FOLD_INDEX_CELLS:
            raise ValueError("C2ST total fold evidence exceeds the fixed cap")
        if len(self.folds) != self.fold_count:
            raise ValueError("C2ST folds must match fold_count")
        if self.out_of_fold_reference_count != self.window_count_per_trace or self.out_of_fold_generated_count != self.window_count_per_trace:
            raise ValueError("C2ST out-of-fold counts must equal the per-trace window count")
        if len(self.coefficients) != len(self.feature_names):
            raise ValueError("C2ST coefficients must match feature_names")
        base, remainder = divmod(self.window_count_per_trace, self.fold_count)
        start = 0
        for expected_fold_index, fold in enumerate(self.folds):
            if fold.fold_index != expected_fold_index:
                raise ValueError("C2ST fold_index values must be canonical and ordered")
            stop = start + base + int(expected_fold_index < remainder)
            guard_start = max(0, start - self.guard_window_count)
            guard_stop = min(self.window_count_per_trace, stop + self.guard_window_count)
            expected_evaluation = tuple(range(start, stop))
            expected_guard = (*range(guard_start, start), *range(stop, guard_stop))
            expected_training = (*range(guard_start), *range(guard_stop, self.window_count_per_trace))
            if (
                fold.evaluation_window_indexes != expected_evaluation
                or fold.guard_window_indexes != expected_guard
                or fold.training_window_indexes != expected_training
            ):
                raise ValueError("C2ST fold indexes must equal the exact ordered divmod partition")
            start = stop
            if fold.iterations > self.maximum_iterations:
                raise ValueError("C2ST fold iterations exceed maximum_iterations")
            if not (
                len(fold.reference_training_mean)
                == len(fold.reference_training_scale)
                == len(fold.coefficients)
                == len(self.feature_names)
            ) or any(scale <= 0.0 for scale in fold.reference_training_scale):
                raise ValueError("C2ST fold transform and coefficient vectors must match positive feature scales")
        expected_coefficients = tuple(
            math.fsum(fold.coefficients[index] for fold in self.folds) / len(self.folds)
            for index in range(len(self.feature_names))
        )
        for index, (value, expected) in enumerate(zip(self.coefficients, expected_coefficients, strict=True)):
            require_close(value, expected, name=f"C2ST coefficient {index}")
        require_close(
            self.intercept,
            math.fsum(fold.intercept for fold in self.folds) / len(self.folds),
            name="C2ST intercept",
        )
        return self


def _require_normalized_postfit(values: tuple[float, ...], *, name: str) -> None:
    if any(not 0.0 <= value <= 1.0 for value in values) or not math.isclose(
        math.fsum(values), 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE
    ):
        raise ValueError(f"{name} must be nonnegative and sum to one")


class FanoAllanComparison(StrictArtifactModel):
    diagnostics: FanoAllanDiagnostic
    score: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        require_close(self.score, 1.0 - self.diagnostics.discrepancy, name="Fano/Allan score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json"))


class TransitionMatrixComparison(StrictArtifactModel):
    diagnostics: TransitionMatrixDiagnostic
    score: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        require_close(self.score, 1.0 - self.diagnostics.discrepancy, name="transition score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json"))


class C2stComparison(StrictArtifactModel):
    diagnostics: C2stDiagnostic
    score: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        expected = 1.0 - 2.0 * abs(self.diagnostics.auc - 0.5)
        require_close(self.score, expected, name="C2ST score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json"))


type PostfitComparison = FanoAllanComparison | TransitionMatrixComparison | C2stComparison


class PostfitDiagnostics(StrictArtifactModel):
    classical_c2st: C2stComparison
    fano_allan: FanoAllanComparison
    transition_matrix: TransitionMatrixComparison

    def __getitem__(self, name: str) -> PostfitComparison:
        if name not in POSTFIT_NAMES:
            raise KeyError(name)
        return cast(PostfitComparison, getattr(self, name))

    def items(self) -> tuple[tuple[str, PostfitComparison], ...]:
        return tuple((name, self[name]) for name in POSTFIT_NAMES)


def _require_method_score(score: float, discrepancy: float, *, name: str) -> None:
    require_close(score, 1.0 - discrepancy, name=f"{name} score")


class PublishedAutocorrelationMethod(StrictArtifactModel):
    diagnostics: AutocorrelationDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="autocorrelation")
        return self


class PublishedFrameSizeMethod(StrictArtifactModel):
    diagnostics: FrameSizeDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.distance, name="frame_size_ks")
        return self


class PublishedIatMethod(StrictArtifactModel):
    diagnostics: IatDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.distance, name="iat_ks")
        return self


class PublishedMultiscaleMethod(StrictArtifactModel):
    diagnostics: MultiscaleDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="multiscale_rate")
        return self


class PublishedCramerVonMisesMethod(StrictArtifactModel):
    diagnostics: CramerVonMisesDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="cramer_von_mises")
        return self


class PublishedAndersonDarlingMethod(StrictArtifactModel):
    diagnostics: AndersonDarlingDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="anderson_darling")
        return self


class PublishedJensenShannonMethod(StrictArtifactModel):
    diagnostics: JensenShannonDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="jensen_shannon")
        return self


class PublishedApproximateMmdMethod(StrictArtifactModel):
    diagnostics: ApproximateMmdDiagnostic
    score: UnitFloat
    weight: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        _require_method_score(self.score, self.diagnostics.discrepancy, name="approximate_mmd")
        return self


type PublishedMethod = (
    PublishedAutocorrelationMethod
    | PublishedFrameSizeMethod
    | PublishedIatMethod
    | PublishedMultiscaleMethod
    | PublishedCramerVonMisesMethod
    | PublishedAndersonDarlingMethod
    | PublishedJensenShannonMethod
    | PublishedApproximateMmdMethod
)


class PublishedComparisonMethods(StrictArtifactModel):
    autocorrelation: PublishedAutocorrelationMethod
    frame_size_ks: PublishedFrameSizeMethod
    iat_ks: PublishedIatMethod
    multiscale_rate: PublishedMultiscaleMethod
    cramer_von_mises: PublishedCramerVonMisesMethod
    anderson_darling: PublishedAndersonDarlingMethod
    jensen_shannon: PublishedJensenShannonMethod
    approximate_mmd: PublishedApproximateMmdMethod

    def items(self) -> tuple[tuple[str, PublishedMethod], ...]:
        return tuple(
            (name, cast(PublishedMethod, getattr(self, name))) for name in FITNESS_METHOD_NAMES
        )


class PublishedComparisonResult(StrictArtifactModel):
    aggregate_score: UnitFloat
    input_identities: ComparisonInputIdentities
    methods: PublishedComparisonMethods
    observation_window_seconds: PositiveFloat
    postfit_diagnostics: PostfitDiagnostics

    @model_validator(mode="after")
    def validate_publication_arithmetic(self) -> Self:
        for _name, method in self.methods.items():
            if method.diagnostics.observation_window_seconds != self.observation_window_seconds:
                raise ValueError("every method diagnostic must contain the shared observation window")
        for _name, postfit in self.postfit_diagnostics.items():
            if postfit.diagnostics.observation_window_seconds != self.observation_window_seconds:
                raise ValueError("every post-fit diagnostic must contain the shared observation window")
        weights = tuple(method.weight for _name, method in self.methods.items())
        if not math.isclose(math.fsum(weights), 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("method weights must sum to one")
        expected = math.fsum(method.weight * method.score for _name, method in self.methods.items())
        if not math.isclose(expected, self.aggregate_score, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("aggregate_score must equal the exact configured weighted sum")
        return self


class ComparisonResult(StrictArtifactModel):
    """One deeply immutable comparison result, optionally carrying artifact identities."""

    # Construction copies nested method and identity mappings before exposing
    # them.  Callers can safely retain this object as publication evidence even
    # if the dictionaries used to build it are later mutated.

    aggregate_score: UnitFloat
    observation_window_seconds: PositiveFloat
    methods: ComparisonMethods
    input_identities: ComparisonInputIdentities | None
    postfit_diagnostics: PostfitDiagnostics | None = None

    @field_validator("methods", mode="before")
    @classmethod
    def methods_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, ComparisonMethods):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            methods = cast(Mapping[str, object], value)
            return {
                name: method.model_dump(mode="python") if isinstance(method, BaseModel) else method
                for name, method in methods.items()
            }
        return value

    @field_validator("input_identities", mode="before")
    @classmethod
    def input_identities_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, ComparisonInputIdentities):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            identities = cast(Mapping[str, object], value)
            return {
                name: identity.as_dict()
                if type(identity) is ContentIdentity
                else identity.model_dump(mode="python")
                if isinstance(identity, BaseModel)
                else identity
                for name, identity in identities.items()
            }
        return value

    @field_validator("postfit_diagnostics", mode="before")
    @classmethod
    def postfit_diagnostics_are_reconstructed_from_primitives(cls, value: object) -> object:
        if isinstance(value, PostfitDiagnostics):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            diagnostics = cast(Mapping[str, object], value)
            return {
                name: item.model_dump(mode="python") if isinstance(item, BaseModel) else item
                for name, item in diagnostics.items()
            }
        return value

    @model_validator(mode="after")
    def validate_local_arithmetic(self) -> Self:
        for _name, method in self.methods.items():
            diagnostic_window = method.diagnostics.get("observation_window_seconds")
            if diagnostic_window != self.observation_window_seconds:
                raise ValueError("every method diagnostic must contain the shared observation window")
        if self.postfit_diagnostics is not None:
            for _name, postfit in self.postfit_diagnostics.items():
                if postfit.diagnostics.observation_window_seconds != self.observation_window_seconds:
                    raise ValueError("every post-fit diagnostic must contain the shared observation window")
        weight_sum = math.fsum(method.weight for method in self.methods.values())
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("method weights must sum to one")
        weighted_score = math.fsum(method.weight * method.score for method in self.methods.values())
        if not math.isclose(weighted_score, self.aggregate_score, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("aggregate_score must equal the exact configured weighted sum")
        return self

    @property
    def input_sha256(self) -> Mapping[str, str] | None:
        """Expose digests for diagnostics that do not need byte counts."""
        if self.input_identities is None:
            return None
        return MappingProxyType({name: identity.sha256 for name, identity in self.input_identities.items()})

    def with_input_identities(
        self,
        identities: Mapping[str, ContentIdentity | ContentIdentityPayload] | ComparisonInputIdentities,
    ) -> Self:
        """Return the same scientific result with exact file and settings identities."""
        return type(self).model_validate(
            {
                "aggregate_score": self.aggregate_score,
                "observation_window_seconds": self.observation_window_seconds,
                "methods": self.methods,
                "input_identities": identities,
                "postfit_diagnostics": self.postfit_diagnostics,
            }
        )

    def with_postfit_diagnostics(self, diagnostics: PostfitDiagnostics) -> Self:
        """Return this fitness result joined to the exact final-only diagnostics."""
        return type(self).model_validate(
            {
                "aggregate_score": self.aggregate_score,
                "observation_window_seconds": self.observation_window_seconds,
                "methods": self.methods,
                "input_identities": self.input_identities,
                "postfit_diagnostics": diagnostics,
            }
        )

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the exact publishable JSON shape as fresh mutable values."""
        return cast(dict[str, JsonValue], published_comparison_result(self).model_dump(mode="json"))

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Strictly validate the documented similarity artifact object."""
        try:
            return cast(Self, operational_comparison_result(PublishedComparisonResult.model_validate(value)))
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            if field.endswith("observation_window_seconds"):
                raise ValueError(
                    "every method diagnostic observation window must be a finite positive float"
                ) from error
            if field.endswith("reference_count") and first["type"] == "int_type":
                raise ValueError("reference_count must be an integer") from error
            raise ValueError(f"invalid comparison result {field}: {first['msg']}") from error


def published_comparison_result(result: ComparisonResult) -> PublishedComparisonResult:
    """Revalidate one operational result as the exact required publication wire root."""
    if result.input_identities is None:
        raise ValueError("input content identities are required for a similarity artifact")
    if result.postfit_diagnostics is None:
        raise ValueError("post-fit diagnostics are required for a similarity artifact")
    raw_methods = cast(object, result.methods)
    if isinstance(raw_methods, ComparisonMethods):
        methods: Mapping[str, object] = dict(raw_methods.items())
    elif isinstance(raw_methods, Mapping):
        methods = cast(Mapping[str, object], raw_methods)
    else:
        raise ValueError("comparison methods must be a canonical methods object")
    raw_identities = cast(object, result.input_identities)
    identities: object = (
        raw_identities.model_dump(mode="python")
        if isinstance(raw_identities, ComparisonInputIdentities)
        else raw_identities
    )
    return PublishedComparisonResult.model_validate(
        {
            "aggregate_score": result.aggregate_score,
            "input_identities": identities,
            "methods": {
                name: method.model_dump(mode="python") if isinstance(method, BaseModel) else method
                for name, method in methods.items()
            },
            "observation_window_seconds": result.observation_window_seconds,
            "postfit_diagnostics": result.postfit_diagnostics.model_dump(mode="python"),
        }
    )


def operational_comparison_result(published: PublishedComparisonResult) -> ComparisonResult:
    """Build the in-process comparison value from one validated publication wire root."""
    return ComparisonResult.model_validate(published.model_dump(mode="python"))
