"""Pure validation for detached and explicitly delimited hash domains."""

from dataclasses import dataclass
from typing import cast

from .errors import InvalidHashDomainError, InvalidProvenanceError
from .values import (
    _validate_text,  # pyright: ignore[reportPrivateUsage]
)

_HASH_IDENTIFIER_LINE_BREAKS = frozenset({"\x85", "\u2028", "\u2029"})


def _validate_hash_identifier(value: str, field: str) -> None:
    _validate_text(value, field)
    if not _HASH_IDENTIFIER_LINE_BREAKS.isdisjoint(value):
        raise InvalidProvenanceError(f"{field} must be a single-line string")


@dataclass(frozen=True, slots=True)
class HashRegion:
    """A whole resource or one explicitly delimited region within it."""

    resource: str
    region: str | None = None

    def __post_init__(self) -> None:
        _validate_hash_identifier(self.resource, "hash resource")
        if self.region is not None:
            _validate_hash_identifier(self.region, "hash region")


@dataclass(frozen=True, slots=True)
class HashDomain:
    """The carrier of a digest and the regions covered by that digest."""

    carrier: HashRegion
    covered: tuple[HashRegion, ...]

    def __post_init__(self) -> None:
        carrier = cast(object, self.carrier)
        covered = cast(object, self.covered)
        if not isinstance(carrier, HashRegion):
            raise InvalidHashDomainError("hash domain carrier must be a HashRegion")
        if not isinstance(covered, tuple):
            raise InvalidHashDomainError(
                "covered hash regions must be an immutable tuple of HashRegion values"
            )
        typed_covered = cast(tuple[object, ...], covered)
        if not all(isinstance(region, HashRegion) for region in typed_covered):
            raise InvalidHashDomainError(
                "covered hash regions must be an immutable tuple of HashRegion values"
            )


def _region_key(region: HashRegion) -> tuple[str, bool, str]:
    return region.resource, region.region is not None, region.region or ""


def _regions_overlap(carrier: HashRegion, covered: HashRegion) -> bool:
    return carrier.resource == covered.resource and (
        carrier.region is None
        or covered.region is None
        or carrier.region == covered.region
    )


def validate_hash_domain(domain: HashDomain) -> HashDomain:
    """Reject self-containing hash claims and return canonical covered order."""

    if not domain.covered:
        raise InvalidHashDomainError("hash domain must cover at least one region")

    seen: set[HashRegion] = set()
    for covered in domain.covered:
        if covered in seen:
            raise InvalidHashDomainError("duplicate covered hash region")
        seen.add(covered)

    ordered = tuple(sorted(domain.covered, key=_region_key))
    if any(_regions_overlap(domain.carrier, covered) for covered in ordered):
        raise InvalidHashDomainError("hash carrier overlaps its covered domain")

    return HashDomain(carrier=domain.carrier, covered=ordered)
