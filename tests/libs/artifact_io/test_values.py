from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import BinaryIO

import pytest

from trafficlab.libs.artifact_io import (
    ARTIFACT_STATUS_NAME,
    CURRENT_ARTIFACT_STATUS_VERSION,
    LAUNCH_NAME,
    MANIFEST_NAME,
    MAX_ARTIFACT_STATUS_BYTES,
    ArtifactIoError,
    ArtifactKind,
    ArtifactStatusSecurityError,
    ArtifactValidationError,
    ArtifactWriteError,
    AtomicPublicationError,
    InvalidArtifactStatusError,
    InvalidPublicationPlanError,
    MissingArtifactStatusError,
    OrphanArtifactError,
    PackageMember,
    PublicationConflictError,
    PublicationPlan,
    UnsupportedAtomicPublicationError,
    build_file_plan,
    build_package_plan,
)


def _write(_handle: BinaryIO) -> None:
    pass


def _validate(_path: Path) -> None:
    pass


class _OneShotMembers:
    def __init__(self, values: tuple[str, ...]) -> None:
        self.values = values
        self.iterations = 0

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("member iterable consumed more than once")
        yield from self.values


class _OneShotPaths:
    def __init__(self, values: tuple[Path, ...]) -> None:
        self.values = values
        self.iterations = 0

    def __iter__(self) -> Iterator[Path]:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("path iterable consumed more than once")
        yield from self.values


@pytest.mark.unit
def test_public_constants_and_kind_are_fixed() -> None:
    assert CURRENT_ARTIFACT_STATUS_VERSION == 1
    assert MAX_ARTIFACT_STATUS_BYTES == 16_384
    assert ARTIFACT_STATUS_NAME == "artifact-status.toml"
    assert LAUNCH_NAME == "launch.toml"
    assert MANIFEST_NAME == "manifest.json"
    assert tuple(ArtifactKind) == (ArtifactKind.PACKAGE, ArtifactKind.FILE)
    assert ArtifactKind.PACKAGE.value == "package"
    assert ArtifactKind.FILE.value == "file"


@pytest.mark.unit
def test_file_plan_derives_fixed_attempt_members() -> None:
    attempt = Path("/absolute/attempt")
    artifact = Path("/absolute/output/capture.pcapng")

    plan = build_file_plan(attempt, artifact)

    assert plan == PublicationPlan(attempt, artifact, ArtifactKind.FILE, ())
    assert plan.member_paths == ()
    assert plan.launch_path == attempt / LAUNCH_NAME
    assert plan.status_path == attempt / ARTIFACT_STATUS_NAME


@pytest.mark.unit
def test_package_builder_materializes_once_and_sorts_members() -> None:
    members = _OneShotMembers(("z.bin", "nested/b.bin", "a.bin"))

    plan = build_package_plan(
        Path("/absolute/attempt"),
        Path("/absolute/output/package"),
        members=members,
    )

    assert members.iterations == 1
    assert plan.artifact_kind is ArtifactKind.PACKAGE
    assert plan.member_paths == ("a.bin", "nested/b.bin", "z.bin")


@pytest.mark.unit
def test_package_builder_rejects_non_iterable_members() -> None:
    with pytest.raises(InvalidPublicationPlanError):
        build_package_plan(
            Path("/absolute/attempt"),
            Path("/absolute/package"),
            members=object(),  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_empty_package_component_membership_is_canonical() -> None:
    plan = build_package_plan(
        Path("/absolute/attempt"), Path("/absolute/output/package"), members=()
    )

    assert plan.member_paths == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("attempt", "artifact"),
    (
        pytest.param(
            Path("relative"), Path("/absolute/artifact"), id="relative-attempt"
        ),
        pytest.param(Path("."), Path("/absolute/artifact"), id="empty-attempt"),
        pytest.param(
            Path("//ambiguous"), Path("/absolute/artifact"), id="double-root-attempt"
        ),
        pytest.param(
            Path("/absolute/../escape"), Path("/absolute/artifact"), id="dotdot-attempt"
        ),
        pytest.param(
            Path("/bad\npath"), Path("/absolute/artifact"), id="control-attempt"
        ),
        pytest.param(
            Path("/absolute/attempt"), Path("relative"), id="relative-artifact"
        ),
        pytest.param(
            Path("/absolute/attempt"), Path("//ambiguous"), id="double-root-artifact"
        ),
        pytest.param(
            Path("/absolute/attempt"), Path("/output/../artifact"), id="dotdot-artifact"
        ),
        pytest.param(
            Path("/absolute/attempt"), Path("/bad\x7fpath"), id="control-artifact"
        ),
    ),
)
def test_plan_rejects_noncanonical_filesystem_paths(
    attempt: Path, artifact: Path
) -> None:
    with pytest.raises(InvalidPublicationPlanError):
        build_file_plan(attempt, artifact)


@pytest.mark.unit
@pytest.mark.parametrize("field", ("attempt", "artifact"))
@pytest.mark.parametrize(
    "value",
    (
        pytest.param("/absolute/string", id="string"),
        pytest.param("/absolute/trailing/", id="trailing-string"),
        pytest.param("/absolute/repeated//part", id="repeated-string"),
        pytest.param("/absolute/dot/./part", id="dot-string"),
        pytest.param(None, id="none"),
    ),
)
def test_direct_plan_requires_path_runtime_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "attempt_dir": Path("/absolute/attempt"),
        "artifact_path": Path("/absolute/artifact"),
        "artifact_kind": ArtifactKind.FILE,
        "member_paths": (),
    }
    values[field + ("_dir" if field == "attempt" else "_path")] = value

    with pytest.raises(InvalidPublicationPlanError):
        PublicationPlan(**values)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("artifact_suffix", ("", LAUNCH_NAME, ARTIFACT_STATUS_NAME))
def test_artifact_must_be_distinct_from_attempt_launch_and_status(
    artifact_suffix: str,
) -> None:
    attempt = Path("/absolute/attempt")
    artifact = attempt if not artifact_suffix else attempt / artifact_suffix

    with pytest.raises(InvalidPublicationPlanError):
        build_file_plan(attempt, artifact)


@pytest.mark.unit
def test_direct_plan_requires_artifact_kind_runtime_value() -> None:
    with pytest.raises(InvalidPublicationPlanError):
        PublicationPlan(
            Path("/absolute/attempt"),
            Path("/absolute/artifact"),
            "file",  # type: ignore[arg-type]
            (),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "member",
    (
        pytest.param("", id="empty"),
        pytest.param("/absolute", id="absolute"),
        pytest.param(".", id="dot"),
        pytest.param("./member", id="leading-dot"),
        pytest.param("member/../escape", id="traversal"),
        pytest.param("member//part", id="repeated-separator"),
        pytest.param("member/", id="trailing-separator"),
        pytest.param("member\x00part", id="nul"),
        pytest.param("member\npart", id="newline"),
        pytest.param("member\x7fpart", id="del"),
        pytest.param(MANIFEST_NAME, id="manifest-reserved"),
        pytest.param(LAUNCH_NAME, id="launch-reserved"),
    ),
)
def test_package_builder_rejects_invalid_or_reserved_members(member: str) -> None:
    with pytest.raises(InvalidPublicationPlanError):
        build_package_plan(
            Path("/absolute/attempt"),
            Path("/absolute/package"),
            members=(member,),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "members",
    (
        pytest.param(("same", "same"), id="duplicate"),
        pytest.param(("a", "a/b"), id="forward-prefix"),
        pytest.param(("a/b", "a"), id="reverse-prefix"),
        pytest.param(("nested/file", "nested/file/child"), id="deep-prefix"),
    ),
)
def test_package_builder_rejects_duplicate_or_file_prefix_members(
    members: tuple[str, ...],
) -> None:
    with pytest.raises(InvalidPublicationPlanError):
        build_package_plan(
            Path("/absolute/attempt"),
            Path("/absolute/package"),
            members=members,
        )


@pytest.mark.unit
def test_member_prefix_comparison_uses_complete_posix_components() -> None:
    plan = build_package_plan(
        Path("/absolute/attempt"),
        Path("/absolute/package"),
        members=("a/b", "ab", "a/c", "abc/file"),
    )

    assert plan.member_paths == ("a/b", "a/c", "ab", "abc/file")


@pytest.mark.unit
@pytest.mark.parametrize(
    "member_paths",
    (
        pytest.param(["a"], id="mutable-list"),
        pytest.param(("z", "a"), id="unsorted"),
        pytest.param(("a", "a"), id="duplicate"),
        pytest.param(("a", "a/b"), id="prefix-collision"),
        pytest.param((MANIFEST_NAME,), id="reserved"),
        pytest.param((1,), id="wrong-member-type"),
    ),
)
def test_direct_package_plan_requires_canonical_member_tuple(
    member_paths: object,
) -> None:
    with pytest.raises(InvalidPublicationPlanError):
        PublicationPlan(
            Path("/absolute/attempt"),
            Path("/absolute/package"),
            ArtifactKind.PACKAGE,
            member_paths,  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_direct_file_plan_rejects_members() -> None:
    with pytest.raises(InvalidPublicationPlanError):
        PublicationPlan(
            Path("/absolute/attempt"),
            Path("/absolute/file"),
            ArtifactKind.FILE,
            ("member",),
        )


@pytest.mark.unit
def test_package_member_validates_path_callbacks_and_is_immutable() -> None:
    member = PackageMember("nested/member.bin", _write, _validate)

    assert member.path == "nested/member.bin"
    with pytest.raises(FrozenInstanceError):
        member.path = "other"  # type: ignore[misc]
    with pytest.raises(InvalidPublicationPlanError):
        PackageMember("../escape", _write, _validate)
    with pytest.raises(InvalidPublicationPlanError):
        PackageMember(MANIFEST_NAME, _write, _validate)
    with pytest.raises(InvalidPublicationPlanError):
        PackageMember("member", object(), _validate)  # type: ignore[arg-type]
    with pytest.raises(InvalidPublicationPlanError):
        PackageMember("member", _write, object())  # type: ignore[arg-type]


@pytest.mark.unit
def test_plan_is_frozen_and_slotted() -> None:
    plan = build_file_plan(Path("/absolute/attempt"), Path("/absolute/file"))

    assert not hasattr(plan, "__dict__")
    with pytest.raises(FrozenInstanceError):
        plan.artifact_path = Path("/other")  # type: ignore[misc]


@pytest.mark.unit
def test_plan_and_member_reject_unencodable_paths_without_repeating_them() -> None:
    unencodable = "\udcff"

    for operation in (
        lambda: build_file_plan(
            Path(f"/absolute/{unencodable}"), Path("/absolute/artifact")
        ),
        lambda: build_package_plan(
            Path("/absolute/attempt"),
            Path("/absolute/artifact"),
            members=(f"member-{unencodable}",),
        ),
    ):
        with pytest.raises(InvalidPublicationPlanError) as caught:
            operation()
        message = str(caught.value)
        assert message.splitlines() == [message]
        assert unencodable not in message


@pytest.mark.unit
def test_public_error_hierarchy_and_immutable_publication_evidence() -> None:
    assert issubclass(InvalidPublicationPlanError, ArtifactIoError)
    assert issubclass(InvalidArtifactStatusError, ArtifactIoError)
    assert issubclass(ArtifactStatusSecurityError, InvalidArtifactStatusError)
    assert issubclass(MissingArtifactStatusError, InvalidArtifactStatusError)
    assert issubclass(UnsupportedAtomicPublicationError, AtomicPublicationError)
    for error_type in (
        OrphanArtifactError,
        PublicationConflictError,
        ArtifactWriteError,
        ArtifactValidationError,
        AtomicPublicationError,
    ):
        assert issubclass(error_type, ArtifactIoError)

    retained = [Path("/absolute/staging")]
    error = ArtifactWriteError(
        "write failed",
        retained_paths=retained,
        orphan_path=Path("/absolute/orphan"),
    )
    retained.append(Path("/absolute/later"))
    assert error.args == ("write failed",)
    assert error.retained_paths == (Path("/absolute/staging"),)
    assert error.orphan_path == Path("/absolute/orphan")


@pytest.mark.unit
def test_publication_error_evidence_is_copied_once_and_read_only() -> None:
    retained = _OneShotPaths((Path("/absolute/staging"),))
    error = ArtifactWriteError(
        "write failed",
        retained_paths=retained,
        orphan_path=Path("/absolute/orphan"),
    )

    assert retained.iterations == 1
    assert error.args == ("write failed",)
    assert error.retained_paths == (Path("/absolute/staging"),)
    assert error.orphan_path == Path("/absolute/orphan")
    with pytest.raises(AttributeError):
        error.retained_paths = (Path("/changed"),)  # type: ignore[misc]
    with pytest.raises(AttributeError):
        error.orphan_path = None  # type: ignore[misc]
    assert error.retained_paths == (Path("/absolute/staging"),)
    assert error.orphan_path == Path("/absolute/orphan")


@pytest.mark.unit
def test_publication_error_rejects_wrong_runtime_evidence_paths() -> None:
    with pytest.raises(
        TypeError,
        match=r"^retained_paths must contain only Path values$",
    ):
        ArtifactWriteError(
            "write failed",
            retained_paths=("not-a-path",),  # type: ignore[arg-type]
        )
    with pytest.raises(
        TypeError,
        match=r"^orphan_path must be a Path or None$",
    ):
        ArtifactWriteError(
            "write failed",
            orphan_path="not-a-path",  # type: ignore[arg-type]
        )
