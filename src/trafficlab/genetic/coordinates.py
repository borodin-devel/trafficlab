"""Exact pure mappings between family genes and normalized GA coordinates."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from random import Random
from typing import Literal, cast

from trafficlab.config import FamilyName, FloatBounds, IntegerBounds
from trafficlab.errors import EvidenceState, FailureAuthority, TrafficlabError
from trafficlab.genetic.types import CandidateFailure, CandidateFailureKind
from trafficlab.models.common import FamilyBounds, Gene, Genes, ModelFamily
from trafficlab.models.registry import MARKOV_RENEWAL_FAMILY, REGISTRY, get_family
from trafficlab.trace import TraceEvent

type CoordinateKind = Literal["linear", "log", "integer"]


def _invalid(detail: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action="provide finite genes and registered family bounds")


@dataclass(frozen=True, slots=True)
class GeneCoordinate:
    """One canonical gene name, transformed coordinate kind, and named bounds."""

    name: str
    kind: CoordinateKind
    bounds: FloatBounds | IntegerBounds


@dataclass(frozen=True, slots=True)
class CandidateEvaluationError(Exception):
    """An expected candidate-science failure that population code may classify."""

    kind: CandidateFailureKind
    seed: int | None
    detail: str
    stage: str = field(kw_only=True)
    affected_evidence: str = field(kw_only=True)
    evidence_state: EvidenceState = field(kw_only=True)
    corrective_action: str = field(kw_only=True)
    authority: FailureAuthority = field(kw_only=True)

    def __post_init__(self) -> None:
        CandidateFailure(
            kind=self.kind,
            seed=self.seed,
            detail=self.detail,
            stage=self.stage,
            affected_evidence=self.affected_evidence,
            evidence_state=self.evidence_state,
            corrective_action=self.corrective_action,
            authority=self.authority,
        )
        Exception.__init__(self, self.detail)


def _validate_coordinate(coordinate: object) -> GeneCoordinate:
    if type(coordinate) is not GeneCoordinate:
        raise TypeError("coordinate must be a GeneCoordinate")
    if coordinate.kind not in {"linear", "log", "integer"}:
        raise _invalid("invalid genetic coordinate kind")
    if coordinate.kind == "integer":
        if type(coordinate.bounds) is not IntegerBounds:
            raise _invalid("integer coordinate requires integer bounds")
    elif type(coordinate.bounds) is not FloatBounds:
        raise _invalid("continuous coordinate requires float bounds")
    if coordinate.kind == "log" and coordinate.bounds.lower <= 0.0:
        raise _invalid("logarithmic coordinate requires a positive lower bound")
    return coordinate


def _coordinate_value(value: object, *, name: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise _invalid(f"{name} must be a finite coordinate")
    return value


def reflect(value: float) -> float:
    """Reflect one finite normalized coordinate into the inclusive unit interval."""
    finite_value = _coordinate_value(value, name="genetic coordinate")
    remainder = finite_value % 2.0
    return remainder if remainder <= 1.0 else 2.0 - remainder


def encode_gene(coordinate: GeneCoordinate, value: Gene) -> float:
    """Encode one exact bounded gene as its normalized linear or log coordinate."""
    checked = _validate_coordinate(coordinate)
    if checked.kind == "integer":
        if type(value) is not int:
            raise _invalid("integer gene must be an exact integer")
    elif type(value) is not float:
        raise _invalid("continuous gene must be an exact float")
    numeric_value = float(value)
    if not math.isfinite(numeric_value) or not checked.bounds.lower <= numeric_value <= checked.bounds.upper:
        raise _invalid("gene must be finite and within its named coordinate bounds")
    if checked.kind == "log":
        bounds = cast(FloatBounds, checked.bounds)
        return (math.log(numeric_value) - math.log(bounds.lower)) / (math.log(bounds.upper) - math.log(bounds.lower))
    return (numeric_value - checked.bounds.lower) / (checked.bounds.upper - checked.bounds.lower)


def decode_gene(coordinate: GeneCoordinate, value: float) -> Gene:
    """Decode one finite normalized coordinate using the locked endpoint rules."""
    checked = _validate_coordinate(coordinate)
    normalized = _coordinate_value(value, name="normalized coordinate")
    if not 0.0 <= normalized <= 1.0:
        raise _invalid("normalized coordinate must be in [0, 1]")
    if normalized == 0.0:
        return checked.bounds.lower
    if normalized == 1.0:
        return checked.bounds.upper
    if checked.kind == "integer":
        bounds = cast(IntegerBounds, checked.bounds)
        return bounds.lower + math.floor(normalized * (bounds.upper - bounds.lower) + 0.5)
    bounds = cast(FloatBounds, checked.bounds)
    if checked.kind == "log":
        return math.exp(math.log(bounds.lower) + normalized * (math.log(bounds.upper) - math.log(bounds.lower)))
    return bounds.lower + normalized * (bounds.upper - bounds.lower)


def family_coordinates(name: FamilyName, bounds: FamilyBounds) -> tuple[GeneCoordinate, ...]:
    """Return family chromosome metadata in the published family-gene order."""
    family = get_family(name)
    if type(bounds) is not family.bounds_type:
        raise _invalid(f"invalid {family.name} gene bounds")
    coordinates: list[GeneCoordinate] = []
    for gene_name in family.gene_names:
        bound = getattr(bounds, gene_name)
        kind: CoordinateKind
        if family is MARKOV_RENEWAL_FAMILY and gene_name == "r":
            kind = "integer"
        elif family is MARKOV_RENEWAL_FAMILY and gene_name in {"q1", "q2", "alpha"}:
            kind = "linear"
        else:
            kind = "log"
        coordinates.append(GeneCoordinate(gene_name, kind, bound))
    return tuple(coordinates)


def _registered_family(family: ModelFamily) -> ModelFamily:
    if REGISTRY.get(family.name) is not family:
        raise _invalid("unknown registered model family")
    return family


def initialize_candidate(
    family: ModelFamily, bounds: FamilyBounds, reference: Sequence[TraceEvent], rng: Random
) -> Genes:
    """Draw one chromosome in family order, then repair it once without further RNG."""
    checked_family = _registered_family(family)
    raw_genes: list[Gene] = []
    for coordinate in family_coordinates(checked_family.name, bounds):
        if coordinate.kind == "integer":
            integer_bounds = cast(IntegerBounds, coordinate.bounds)
            raw_genes.append(rng.randrange(integer_bounds.lower, integer_bounds.upper + 1))
        else:
            raw_genes.append(decode_gene(coordinate, rng.random()))
    try:
        return checked_family.repair(tuple(raw_genes), bounds, reference)
    except TrafficlabError as error:
        raise CandidateEvaluationError(
            "repair",
            None,
            str(error),
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action=error.corrective_action,
            authority="primary",
        ) from error


def bernoulli(rng: Random, probability: float) -> bool:
    """Draw the one required endpoint-preserving Bernoulli variate."""
    if type(probability) is not float or not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise _invalid("Bernoulli probability must be a finite float in [0, 1]")
    return rng.random() < probability


def mutate_coordinate(coordinate: GeneCoordinate, value: Gene, epsilon: float) -> Gene:
    """Apply one Gaussian offset in normalized space and decode after reflection."""
    normalized = encode_gene(coordinate, value)
    return decode_gene(coordinate, reflect(normalized + _coordinate_value(epsilon, name="mutation offset")))
