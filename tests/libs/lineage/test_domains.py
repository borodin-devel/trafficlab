from itertools import permutations

import pytest

from trafficlab.libs.lineage import (
    HashDomain,
    HashRegion,
    InvalidHashDomainError,
    InvalidProvenanceError,
    validate_hash_domain,
)


@pytest.mark.unit
def test_whole_file_self_hash_is_rejected() -> None:
    domain = HashDomain(
        carrier=HashRegion("manifest.json", "sha256"),
        covered=(HashRegion("manifest.json"),),
    )
    with pytest.raises(InvalidHashDomainError):
        validate_hash_domain(domain)


@pytest.mark.unit
def test_detached_status_domains_are_valid() -> None:
    manifest = validate_hash_domain(
        HashDomain(
            carrier=HashRegion("artifact-status.toml", "sha256"),
            covered=(HashRegion("manifest.json"),),
        )
    )
    artifact = validate_hash_domain(
        HashDomain(
            carrier=HashRegion("artifact-status.toml", "sha256"),
            covered=(HashRegion("artifact.pcapng"),),
        )
    )
    assert manifest.covered == (HashRegion("manifest.json"),)
    assert artifact.covered == (HashRegion("artifact.pcapng"),)


@pytest.mark.unit
def test_distinct_delimited_payload_may_share_resource() -> None:
    assert validate_hash_domain(
        HashDomain(
            carrier=HashRegion("record.json", "payload_sha256"),
            covered=(HashRegion("record.json", "payload"),),
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "resource",
    (
        pytest.param("", id="empty"),
        pytest.param("bad\x00resource", id="nul"),
        pytest.param("bad\nresource", id="newline"),
        pytest.param("bad\x7fresource", id="del"),
    ),
)
def test_hash_region_rejects_empty_or_control_bearing_resource(
    resource: str,
) -> None:
    with pytest.raises(InvalidProvenanceError):
        HashRegion(resource)


@pytest.mark.unit
@pytest.mark.parametrize(
    "region",
    (
        pytest.param("", id="empty"),
        pytest.param("bad\x00region", id="nul"),
        pytest.param("bad\rregion", id="carriage-return"),
        pytest.param("bad\x7fregion", id="del"),
    ),
)
def test_hash_region_rejects_empty_or_control_bearing_named_region(
    region: str,
) -> None:
    with pytest.raises(InvalidProvenanceError):
        HashRegion("record.json", region)


@pytest.mark.unit
def test_hash_domain_rejects_empty_covered_regions() -> None:
    with pytest.raises(InvalidHashDomainError):
        validate_hash_domain(
            HashDomain(
                carrier=HashRegion("artifact-status.toml", "sha256"),
                covered=(),
            )
        )


@pytest.mark.unit
def test_hash_domain_rejects_duplicate_covered_regions() -> None:
    member = HashRegion("artifact.pcapng")
    with pytest.raises(InvalidHashDomainError):
        validate_hash_domain(
            HashDomain(
                carrier=HashRegion("artifact-status.toml", "sha256"),
                covered=(member, member),
            )
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("carrier", "covered"),
    (
        pytest.param(
            HashRegion("record.json"),
            HashRegion("record.json"),
            id="both-whole-resource",
        ),
        pytest.param(
            HashRegion("record.json"),
            HashRegion("record.json", "payload"),
            id="whole-carrier",
        ),
        pytest.param(
            HashRegion("record.json", "payload_sha256"),
            HashRegion("record.json", "payload_sha256"),
            id="same-named-region",
        ),
    ),
)
def test_hash_domain_rejects_every_same_resource_carrier_overlap(
    carrier: HashRegion, covered: HashRegion
) -> None:
    with pytest.raises(InvalidHashDomainError):
        validate_hash_domain(HashDomain(carrier=carrier, covered=(covered,)))


@pytest.mark.unit
def test_hash_domain_canonicalizes_covered_region_permutations() -> None:
    regions = (
        HashRegion("b.bin"),
        HashRegion("a.bin", "z"),
        HashRegion("a.bin"),
        HashRegion("a.bin", "a"),
    )
    expected = (
        HashRegion("a.bin"),
        HashRegion("a.bin", "a"),
        HashRegion("a.bin", "z"),
        HashRegion("b.bin"),
    )
    carrier = HashRegion("manifest.json", "members_sha256")

    for covered in permutations(regions):
        assert (
            validate_hash_domain(HashDomain(carrier=carrier, covered=covered)).covered
            == expected
        )


@pytest.mark.unit
def test_distinct_named_covered_regions_are_not_duplicates() -> None:
    domain = validate_hash_domain(
        HashDomain(
            carrier=HashRegion("artifact-status.toml", "sha256"),
            covered=(
                HashRegion("record.json", "payload"),
                HashRegion("record.json", "metadata"),
            ),
        )
    )

    assert domain.covered == (
        HashRegion("record.json", "metadata"),
        HashRegion("record.json", "payload"),
    )


@pytest.mark.unit
def test_manifest_member_hash_domains_are_valid() -> None:
    members = ("launch.toml", "packets.pcapng")

    for member in members:
        assert validate_hash_domain(
            HashDomain(
                carrier=HashRegion("manifest.json", f"member:{member}:sha256"),
                covered=(HashRegion(member),),
            )
        ) == HashDomain(
            carrier=HashRegion("manifest.json", f"member:{member}:sha256"),
            covered=(HashRegion(member),),
        )


@pytest.mark.unit
def test_hash_domain_requires_hash_region_carrier() -> None:
    with pytest.raises(
        InvalidHashDomainError,
        match=r"^hash domain carrier must be a HashRegion$",
    ):
        HashDomain(
            carrier="artifact-status.toml",  # type: ignore[arg-type]
            covered=(HashRegion("manifest.json"),),
        )


@pytest.mark.unit
def test_hash_domain_requires_immutable_covered_tuple() -> None:
    with pytest.raises(
        InvalidHashDomainError,
        match=(
            r"^covered hash regions must be an immutable tuple of HashRegion values$"
        ),
    ):
        HashDomain(
            carrier=HashRegion("artifact-status.toml", "sha256"),
            covered=[HashRegion("manifest.json")],  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_hash_domain_rejects_wrong_covered_member_type() -> None:
    with pytest.raises(
        InvalidHashDomainError,
        match=(
            r"^covered hash regions must be an immutable tuple of HashRegion values$"
        ),
    ):
        HashDomain(
            carrier=HashRegion("artifact-status.toml", "sha256"),
            covered=("manifest.json",),  # type: ignore[arg-type]
        )
