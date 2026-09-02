"""Closed built-in traffic-model registry and family-bound construction."""

from __future__ import annotations

from types import MappingProxyType
from typing import cast

from pydantic import ValidationError

from trafficlab.common.config import FamilyName, FloatBounds, IntegerBounds
from trafficlab.common.errors import TrafficlabError
from trafficlab.generation.models.acd import AcdFamily
from trafficlab.generation.models.common import FamilyBounds, ModelFamily
from trafficlab.generation.models.markov_packet_train import MarkovPacketTrainFamily
from trafficlab.generation.models.markov_renewal import MarkovRenewalFamily
from trafficlab.generation.models.mmpp import MmppFamily
from trafficlab.generation.models.nhpp import NhppFamily
from trafficlab.generation.models.packet_hmm import PacketHmmFamily
from trafficlab.generation.models.poisson import PoissonFamily

POISSON_FAMILY = PoissonFamily()
MARKOV_RENEWAL_FAMILY = MarkovRenewalFamily()
MMPP_FAMILY = MmppFamily()
NHPP_FAMILY = NhppFamily()
ACD_FAMILY = AcdFamily()
MARKOV_PACKET_TRAIN_FAMILY = MarkovPacketTrainFamily()
PACKET_HMM_FAMILY = PacketHmmFamily()

REGISTRY: MappingProxyType[str, ModelFamily] = MappingProxyType(
    {
        "poisson_empirical": POISSON_FAMILY,
        "markov_renewal": MARKOV_RENEWAL_FAMILY,
        "mmpp": MMPP_FAMILY,
        "nhpp": NHPP_FAMILY,
        "acd": ACD_FAMILY,
        "markov_packet_train": MARKOV_PACKET_TRAIN_FAMILY,
        "packet_hmm": PACKET_HMM_FAMILY,
    }
)


def get_family(name: str) -> ModelFamily:
    """Return one of the closed built-in families and reject extension names."""
    try:
        return REGISTRY[name]
    except KeyError as error:
        raise TrafficlabError(
            f"unknown model family {name!r}",
            corrective_action=(
                "select poisson_empirical, markov_renewal, mmpp, nhpp, acd, markov_packet_train, or packet_hmm"
            ),
        ) from error


def family_name(name: str) -> FamilyName:
    """Narrow a name after closed-registry validation."""
    get_family(name)
    return cast(FamilyName, name)


def invalid_best_model(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _bound_type(family: ModelFamily, name: str) -> type[FloatBounds] | type[IntegerBounds]:
    """Return the exact bound value type declared by one registered family coordinate."""
    if len(family.gene_names) != len(family.gene_coordinate_kinds):
        raise invalid_best_model(
            f"invalid {family.name} coordinate metadata",
            corrective_action="provide one coordinate kind for every canonical gene name",
        )
    try:
        kind = dict(zip(family.gene_names, family.gene_coordinate_kinds, strict=True))[name]
    except KeyError as error:
        raise invalid_best_model(
            f"invalid {family.name} coordinate metadata",
            corrective_action="provide one coordinate kind for every canonical gene name",
        ) from error
    if kind not in {"linear", "log", "integer"}:
        raise invalid_best_model(
            f"invalid {family.name} coordinate metadata",
            corrective_action="use linear, log, or integer coordinate kinds",
        )
    return IntegerBounds if kind == "integer" else FloatBounds


def bounds_from_config(family: ModelFamily, bounds: FamilyBounds) -> dict[str, FloatBounds | IntegerBounds]:
    if type(bounds) is not family.bounds_type:
        raise invalid_best_model(
            f"invalid {family.name} gene bounds",
            corrective_action="provide the registered family's exact configured bounds type",
        )
    result: dict[str, FloatBounds | IntegerBounds] = {}
    for name in family.gene_names:
        value = getattr(bounds, name, None)
        expected_type = _bound_type(family, name)
        if not isinstance(value, (FloatBounds, IntegerBounds)) or type(value) is not expected_type:
            raise invalid_best_model(
                f"invalid {family.name} gene bounds",
                corrective_action="provide every canonical named bound with its exact numeric type",
            )
        result[name] = value
    return result


def build_bounds(family: ModelFamily, value: object) -> tuple[FamilyBounds, dict[str, FloatBounds | IntegerBounds]]:
    if type(value) is not dict:
        raise invalid_best_model(
            f"invalid {family.name} gene bounds",
            corrective_action="provide one exact lower/upper object for every canonical gene name",
        )
    raw_bounds = cast(dict[object, object], value)
    if set(raw_bounds) != set(family.gene_names):
        raise invalid_best_model(
            f"invalid {family.name} gene bounds",
            corrective_action="provide one exact lower/upper object for every canonical gene name",
        )

    parsed: dict[str, FloatBounds | IntegerBounds] = {}
    for name in family.gene_names:
        item = raw_bounds[name]
        if type(item) is not dict or set(cast(dict[object, object], item)) != {"lower", "upper"}:
            raise invalid_best_model(
                f"invalid {family.name} gene bounds",
                corrective_action="provide exactly lower and upper for every canonical gene bound",
            )
        fields = cast(dict[str, object], item)
        bound_type: type[FloatBounds] | type[IntegerBounds]
        bound_type = _bound_type(family, name)
        scalar_type = int if bound_type is IntegerBounds else float
        if type(fields["lower"]) is not scalar_type or type(fields["upper"]) is not scalar_type:
            raise invalid_best_model(
                f"invalid {family.name} gene bounds",
                corrective_action="use exact integer or float lower and upper values for each coordinate",
            )
        try:
            parsed[name] = bound_type(lower=fields["lower"], upper=fields["upper"])  # type: ignore[arg-type]
        except ValidationError as error:
            raise invalid_best_model(
                f"invalid {family.name} gene bounds: {error}",
                corrective_action="provide finite ordered bounds satisfying the registered family constraints",
            ) from error

    try:
        config = family.bounds_type.model_validate(parsed)
    except ValidationError as error:
        raise invalid_best_model(
            f"invalid {family.name} gene bounds: {error}",
            corrective_action="provide finite ordered bounds satisfying the registered family constraints",
        ) from error
    return (config, parsed)


def config_from_bound_mapping(family: ModelFamily, bounds: dict[str, FloatBounds | IntegerBounds]) -> FamilyBounds:
    if type(bounds) is not dict or set(bounds) != set(family.gene_names):
        raise invalid_best_model(
            f"invalid {family.name} gene bounds",
            corrective_action="provide one exact bound for every canonical gene",
        )
    if any(type(bounds[name]) is not _bound_type(family, name) for name in family.gene_names):
        raise invalid_best_model(
            f"invalid {family.name} gene bounds",
            corrective_action="provide exact FloatBounds or IntegerBounds values for every declared coordinate",
        )
    try:
        return family.bounds_type.model_validate(bounds)
    except ValidationError as error:
        raise invalid_best_model(
            f"invalid {family.name} gene bounds: {error}",
            corrective_action="provide finite ordered bounds satisfying the registered family constraints",
        ) from error
