from collections.abc import Callable
from dataclasses import FrozenInstanceError
from itertools import permutations

import pytest

from trafficlab.libs.lineage import (
    ConfigurationIdentity,
    FileIdentity,
    InvalidDigestError,
    InvalidLineagePathError,
    InvalidProvenanceError,
    NamedIdentity,
    PathKind,
    SeedIdentity,
    Sha256Digest,
    UnsupportedLineageVersionError,
    build_provenance,
    provenance_items,
)

ZERO = "0" * 64
ONE = "1" * 64


@pytest.mark.unit
def test_sha256_digest_accepts_only_lowercase_hex() -> None:
    assert str(Sha256Digest(ZERO)) == ZERO
    for invalid in ("", "0" * 63, "0" * 65, "A" * 64, "g" * 64):
        with pytest.raises(InvalidDigestError):
            Sha256Digest(invalid)


@pytest.mark.unit
def test_file_identity_requires_canonical_path_for_kind() -> None:
    digest = Sha256Digest(ZERO)
    assert FileIdentity(PathKind.LOCAL, "nested/member.bin", digest).path == (
        "nested/member.bin"
    )
    assert (
        FileIdentity(PathKind.EXTERNAL, "/data/source.pcapng", digest).path
        == "/data/source.pcapng"
    )
    for kind, path in (
        (PathKind.LOCAL, "../escape"),
        (PathKind.LOCAL, "/absolute"),
        (PathKind.LOCAL, "member/"),
        (PathKind.EXTERNAL, "relative"),
        # This is a validation fixture; no temporary filesystem path is opened.
        (PathKind.EXTERNAL, "/tmp/../escape"),  # noqa: S108
        (PathKind.EXTERNAL, "//ambiguous"),
    ):
        with pytest.raises(InvalidLineagePathError):
            FileIdentity(kind, path, digest)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "path"),
    (
        pytest.param(PathKind.LOCAL, "", id="empty-local"),
        pytest.param(PathKind.LOCAL, ".", id="dot-local"),
        pytest.param(PathKind.LOCAL, "./member", id="dot-component-local"),
        pytest.param(PathKind.LOCAL, "member//part", id="repeated-separator-local"),
        pytest.param(PathKind.LOCAL, "member\npart", id="control-local"),
        pytest.param(PathKind.LOCAL, "member\x7fpart", id="del-local"),
        pytest.param(PathKind.EXTERNAL, "", id="empty-external"),
        pytest.param(PathKind.EXTERNAL, "/member/", id="trailing-slash-external"),
        pytest.param(PathKind.EXTERNAL, "/member//part", id="separator-external"),
        pytest.param(PathKind.EXTERNAL, "/member\tpart", id="control-external"),
        pytest.param(PathKind.EXTERNAL, "/member\x7fpart", id="del-external"),
    ),
)
def test_file_identity_rejects_noncanonical_and_control_paths(
    kind: PathKind, path: str
) -> None:
    with pytest.raises(InvalidLineagePathError):
        FileIdentity(kind, path, Sha256Digest(ZERO))


@pytest.mark.unit
def test_file_identity_rejects_unknown_runtime_kind() -> None:
    with pytest.raises(InvalidLineagePathError):
        FileIdentity("local", "member", Sha256Digest(ZERO))  # type: ignore[arg-type]


def _named_with_name(name: str) -> object:
    return NamedIdentity(name, "1")


def _configuration_with_name(name: str) -> object:
    return ConfigurationIdentity(name, "cfg")


def _seed_with_name(name: str) -> object:
    return SeedIdentity(name, 1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "constructor",
    (
        pytest.param(_named_with_name, id="named"),
        pytest.param(_configuration_with_name, id="configuration"),
        pytest.param(_seed_with_name, id="seed"),
    ),
)
@pytest.mark.parametrize(
    "invalid_name",
    (
        pytest.param("", id="empty"),
        pytest.param("bad\x00name", id="nul"),
        pytest.param("bad\nname", id="newline"),
        pytest.param("bad\x7fname", id="del"),
    ),
)
def test_identities_reject_empty_or_control_bearing_names(
    constructor: Callable[[str], object], invalid_name: str
) -> None:
    with pytest.raises(InvalidProvenanceError):
        constructor(invalid_name)


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_version",
    (
        pytest.param("", id="empty"),
        pytest.param("1\x00", id="nul"),
        pytest.param("1\n2", id="newline"),
        pytest.param("1\x7f", id="del"),
    ),
)
def test_named_identity_rejects_empty_or_control_bearing_version(
    invalid_version: str,
) -> None:
    with pytest.raises(InvalidProvenanceError):
        NamedIdentity("implementation", invalid_version)


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_identity",
    (
        pytest.param("", id="empty"),
        pytest.param("cfg\x00", id="nul"),
        pytest.param("cfg\rnext", id="carriage-return"),
        pytest.param("cfg\x7f", id="del"),
    ),
)
def test_configuration_identity_rejects_empty_or_control_bearing_identity(
    invalid_identity: str,
) -> None:
    with pytest.raises(InvalidProvenanceError):
        ConfigurationIdentity("component", invalid_identity)


@pytest.mark.unit
def test_build_provenance_is_permutation_invariant() -> None:
    paths = (
        FileIdentity(PathKind.LOCAL, "z.bin", Sha256Digest(ONE)),
        FileIdentity(PathKind.EXTERNAL, "/a.bin", Sha256Digest(ZERO)),
    )
    identities = (NamedIdentity("z", "2"), NamedIdentity("a", "1"))
    seeds = (SeedIdentity("z", 2), SeedIdentity("a", 1))
    configs = (
        ConfigurationIdentity("z", "cfg-z"),
        ConfigurationIdentity("a", "cfg-a"),
    )
    expected = build_provenance(
        paths=paths,
        implementations=identities,
        dependencies=identities,
        seeds=seeds,
        configurations=configs,
        parent_hashes=(Sha256Digest(ONE), Sha256Digest(ZERO)),
    )
    assert expected.paths == (paths[1], paths[0])
    assert expected.implementations == (identities[1], identities[0])
    assert expected.dependencies == (identities[1], identities[0])
    assert expected.seeds == (seeds[1], seeds[0])
    assert expected.configurations == (configs[1], configs[0])
    assert expected.parent_hashes == (Sha256Digest(ZERO), Sha256Digest(ONE))
    for path_order in permutations(paths):
        assert (
            build_provenance(
                paths=path_order,
                implementations=reversed(identities),
                dependencies=reversed(identities),
                seeds=reversed(seeds),
                configurations=reversed(configs),
                parent_hashes=(Sha256Digest(ZERO), Sha256Digest(ONE)),
            )
            == expected
        )


@pytest.mark.unit
def test_provenance_items_have_fixed_serialization_neutral_order() -> None:
    record = build_provenance(
        paths=(FileIdentity(PathKind.LOCAL, "member", Sha256Digest(ZERO)),),
        implementations=(NamedIdentity("trafficlab", "0.1.0"),),
        seeds=(SeedIdentity("generator", 7),),
    )
    assert provenance_items(record) == (
        ("schema_version", 1),
        (
            "paths",
            (
                (
                    ("kind", "local"),
                    ("path", "member"),
                    ("sha256", ZERO),
                ),
            ),
        ),
        (
            "implementations",
            ((("name", "trafficlab"), ("version", "0.1.0")),),
        ),
        ("dependencies", ()),
        ("seeds", ((("name", "generator"), ("value", 7)),)),
        ("configurations", ()),
        ("parent_hashes", ()),
    )
    assert tuple(name for name, _ in provenance_items(record)) == (
        "schema_version",
        "paths",
        "implementations",
        "dependencies",
        "seeds",
        "configurations",
        "parent_hashes",
    )


@pytest.mark.unit
def test_provenance_rejects_invalid_version_and_duplicates() -> None:
    with pytest.raises(UnsupportedLineageVersionError):
        build_provenance(schema_version=2)
    duplicate = NamedIdentity("same", "1")
    with pytest.raises(InvalidProvenanceError):
        build_provenance(implementations=(duplicate, duplicate))
    with pytest.raises(InvalidProvenanceError):
        SeedIdentity("boolean", True)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "schema_version",
    (
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(True, id="boolean"),
    ),
)
def test_provenance_rejects_other_unsupported_schema_versions(
    schema_version: int,
) -> None:
    with pytest.raises(UnsupportedLineageVersionError):
        build_provenance(schema_version=schema_version)


@pytest.mark.unit
def test_provenance_rejects_duplicate_names_with_different_values() -> None:
    with pytest.raises(InvalidProvenanceError):
        build_provenance(
            implementations=(NamedIdentity("same", "1"), NamedIdentity("same", "2"))
        )
    with pytest.raises(InvalidProvenanceError):
        build_provenance(
            dependencies=(NamedIdentity("same", "1"), NamedIdentity("same", "2"))
        )
    with pytest.raises(InvalidProvenanceError):
        build_provenance(seeds=(SeedIdentity("same", 1), SeedIdentity("same", 2)))
    with pytest.raises(InvalidProvenanceError):
        build_provenance(
            configurations=(
                ConfigurationIdentity("same", "one"),
                ConfigurationIdentity("same", "two"),
            )
        )


@pytest.mark.unit
def test_provenance_rejects_duplicate_paths_and_parent_hashes() -> None:
    with pytest.raises(InvalidProvenanceError):
        build_provenance(
            paths=(
                FileIdentity(PathKind.LOCAL, "member", Sha256Digest(ZERO)),
                FileIdentity(PathKind.LOCAL, "member", Sha256Digest(ONE)),
            )
        )
    with pytest.raises(InvalidProvenanceError):
        build_provenance(parent_hashes=(Sha256Digest(ZERO), Sha256Digest(ZERO)))


@pytest.mark.unit
def test_provenance_materializes_immutable_tuples() -> None:
    source = [FileIdentity(PathKind.LOCAL, "member", Sha256Digest(ZERO))]
    record = build_provenance(paths=iter(source))
    source.clear()

    assert record.paths == (FileIdentity(PathKind.LOCAL, "member", Sha256Digest(ZERO)),)
    assert all(
        isinstance(items, tuple)
        for items in (
            record.paths,
            record.implementations,
            record.dependencies,
            record.seeds,
            record.configurations,
            record.parent_hashes,
            provenance_items(record),
        )
    )
    with pytest.raises(FrozenInstanceError):
        record.__setattr__("paths", ())
