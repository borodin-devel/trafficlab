# pyright: reportPossiblyUnboundVariable=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false

import errno
import json
import os
import stat
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from trafficlab.libs.artifact_io import (
    ArtifactKind,
    ArtifactStatus,
    ArtifactStatusSecurityError,
    ArtifactValidationError,
    ArtifactWriteError,
    AtomicPublicationError,
    InvalidArtifactStatusError,
    InvalidPublicationPlanError,
    OrphanArtifactError,
    PackageMember,
    PublicationConflictError,
    PublicationPlan,
    build_file_plan,
    build_package_plan,
    publish_package,
    render_artifact_status,
    validate_publication,
)
from trafficlab.libs.lineage import (
    MAX_CHUNK_SIZE,
    FileIdentity,
    FileSnapshotError,
    ManifestValidationError,
    PathKind,
    Sha256Digest,
    snapshot_external_file,
    snapshot_local_file,
)

filesystem = import_module("trafficlab.libs.artifact_io.filesystem")
publication = import_module("trafficlab.libs.artifact_io.publication")


@dataclass(frozen=True, slots=True)
class _PackageFixture:
    plan: PublicationPlan
    launch: FileIdentity


_EvidenceFailure = (
    ArtifactWriteError
    | ArtifactValidationError
    | AtomicPublicationError
    | PublicationConflictError
    | OrphanArtifactError
)


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _package_fixture(tmp_path: Path) -> _PackageFixture:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    launch_path = attempt / "launch.toml"
    _write_private(launch_path, b'schema_version = 1\ncommand = ["fixture"]\n')
    plan = build_package_plan(
        attempt,
        attempt / "artifact",
        members=("nested/beta.bin", "alpha.bin"),
    )
    return _PackageFixture(plan, snapshot_external_file(launch_path))


def _render_manifest(identities: tuple[FileIdentity, ...]) -> bytes:
    payload = {
        "schema_version": 1,
        "members": [
            {
                "kind": identity.kind.value,
                "path": identity.path,
                "sha256": str(identity.sha256),
            }
            for identity in identities
        ],
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _parse_manifest(data: bytes) -> Iterable[FileIdentity]:
    payload = json.loads(data)
    assert payload["schema_version"] == 1
    return (
        FileIdentity(
            PathKind(member["kind"]),
            member["path"],
            Sha256Digest(member["sha256"]),
        )
        for member in payload["members"]
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _default_payloads() -> dict[str, bytes]:
    return {
        "alpha.bin": b"canonical alpha\n",
        "nested/beta.bin": b"canonical beta\n",
    }


def _member_specs(
    fixture: _PackageFixture,
    *,
    payloads: dict[str, bytes] | None = None,
    after_write: Callable[[str, Path], None] | None = None,
) -> tuple[PackageMember, ...]:
    actual_payloads = _default_payloads() if payloads is None else payloads

    def spec(member_path: str) -> PackageMember:
        def writer(handle: BinaryIO) -> None:
            handle.write(actual_payloads[member_path])

        def validator(path: Path) -> None:
            if path.read_bytes() != actual_payloads[member_path]:
                raise ValueError(f"invalid component bytes for {member_path}")
            if after_write is not None:
                after_write(member_path, path)

        return PackageMember(member_path, writer, validator)

    del fixture
    return tuple(spec(path) for path in ("nested/beta.bin", "alpha.bin"))


def _accept_package(root: Path) -> None:
    del root


def _publish_fixture(
    fixture: _PackageFixture,
    *,
    members: Iterable[PackageMember] | None = None,
    build_manifest: Callable[[tuple[FileIdentity, ...]], bytes] = _render_manifest,
    parse_manifest: Callable[[bytes], Iterable[FileIdentity]] = _parse_manifest,
    validate_package: Callable[[Path], None] | None = None,
    max_manifest_bytes: int = 4096,
    chunk_size: int = 7,
) -> ArtifactStatus:
    specs = _member_specs(fixture) if members is None else members
    validator = _accept_package if validate_package is None else validate_package
    return publish_package(
        fixture.plan,
        fixture.launch,
        specs,
        build_manifest,
        parse_manifest,
        validator,
        max_manifest_bytes=max_manifest_bytes,
        chunk_size=chunk_size,
    )


def _retained_package(error: _EvidenceFailure) -> Path:
    assert len(error.retained_paths) == 1
    retained = error.retained_paths[0]
    assert retained.is_dir()
    assert retained.name.startswith(".trafficlab-staging-")
    assert retained != error.orphan_path
    return retained


def _assert_pre_package_failure(
    error: _EvidenceFailure,
    plan: PublicationPlan,
) -> Path:
    retained = _retained_package(error)
    assert error.orphan_path is None
    assert not plan.artifact_path.exists()
    assert not plan.status_path.exists()
    assert str(error).splitlines() == [str(error)]
    return retained


def _assert_orphan_package(
    error: _EvidenceFailure,
    plan: PublicationPlan,
    *,
    expect_status_staging: bool,
) -> None:
    assert error.orphan_path == plan.artifact_path
    assert plan.artifact_path.is_dir()
    assert not plan.status_path.exists()
    if expect_status_staging:
        assert len(error.retained_paths) == 1
        retained = error.retained_paths[0]
        assert retained.is_file()
        assert retained.parent == plan.attempt_dir
    else:
        assert error.retained_paths == ()
    assert str(error).splitlines() == [str(error)]


@pytest.mark.integration
def test_art_ac_002_package_publication_is_canonical_closed_and_status_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    plan = fixture.plan
    payloads = {
        "alpha.bin": b"canonical alpha\n",
        "nested/beta.bin": b"canonical beta\n",
    }
    expected_member_paths = ("alpha.bin", "launch.toml", "nested/beta.bin")
    original_rename = filesystem._atomic_rename_noreplace
    event_sink: list[str] = []

    def recording_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_path: Path,
        orphan_path: Path | None = None,
    ) -> None:
        if destination_name == plan.artifact_path.name:
            assert not plan.artifact_path.exists()
            assert not plan.status_path.exists()
            event_sink.append("artifact-rename")
        else:
            assert destination_name == plan.status_path.name
            assert plan.artifact_path.is_dir()
            assert not plan.status_path.exists()
            event_sink.append("status-rename")
        original_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            source_path=source_path,
            orphan_path=orphan_path,
        )

    monkeypatch.setattr(filesystem, "_atomic_rename_noreplace", recording_rename)

    def run_order(
        order: tuple[str, str],
    ) -> tuple[dict[str, bytes], bytes, tuple[str, ...], ArtifactStatus]:
        nonlocal event_sink
        active_events: list[str] = []
        event_sink = active_events
        handles: dict[str, BinaryIO] = {}
        manifest_inputs: list[tuple[FileIdentity, ...]] = []
        expected_manifest: list[bytes] = []

        def member_spec(member_path: str) -> PackageMember:
            def writer(handle: BinaryIO) -> None:
                active_events.append(f"write:{member_path}")
                handles[member_path] = handle
                assert not handle.closed
                assert not plan.artifact_path.exists()
                assert not plan.status_path.exists()
                assert handle.write(payloads[member_path]) == len(payloads[member_path])

            def validator(path: Path) -> None:
                active_events.append(f"validate:{member_path}")
                assert handles[member_path].closed
                assert path.read_bytes() == payloads[member_path]
                metadata = path.stat()
                assert stat.S_ISREG(metadata.st_mode)
                assert stat.S_IMODE(metadata.st_mode) == 0o600
                assert metadata.st_nlink == 1
                assert not plan.artifact_path.exists()
                assert not plan.status_path.exists()

            return PackageMember(member_path, writer, validator)

        def build_manifest(identities: tuple[FileIdentity, ...]) -> bytes:
            active_events.append("build-manifest")
            manifest_inputs.append(identities)
            assert (
                tuple(identity.path for identity in identities) == expected_member_paths
            )
            assert all(identity.kind is PathKind.LOCAL for identity in identities)
            assert identities[1].sha256 == fixture.launch.sha256
            data = _render_manifest(identities)
            expected_manifest.append(data)
            return data

        def parse_manifest(data: bytes) -> Iterable[FileIdentity]:
            active_events.append("parse-manifest")
            assert data == expected_manifest[0]
            return _parse_manifest(data)

        def validate_package(root: Path) -> None:
            active_events.append("validate-package")
            assert active_events[-2] == "parse-manifest"
            assert root.name.startswith(".trafficlab-staging-")
            assert _tree_bytes(root)["launch.toml"] == plan.launch_path.read_bytes()
            assert not plan.artifact_path.exists()
            assert not plan.status_path.exists()

        status = publish_package(
            plan,
            fixture.launch,
            (member_spec(path) for path in order),
            build_manifest,
            parse_manifest,
            validate_package,
            max_manifest_bytes=4096,
            chunk_size=7,
        )

        expected_events = (
            "write:alpha.bin",
            "validate:alpha.bin",
            "write:nested/beta.bin",
            "validate:nested/beta.bin",
            "build-manifest",
            "parse-manifest",
            "validate-package",
            "parse-manifest",
            "artifact-rename",
            "parse-manifest",
            "status-rename",
        )
        assert tuple(active_events) == expected_events
        assert all(handle.closed for handle in handles.values())
        assert len(manifest_inputs) == 1
        manifest_identity = snapshot_local_file(plan.artifact_path, "manifest.json")
        copied_launch = snapshot_local_file(plan.artifact_path, "launch.toml")
        assert copied_launch.sha256 == fixture.launch.sha256
        assert status == ArtifactStatus(
            schema_version=1,
            state="published",
            artifact_kind=ArtifactKind.PACKAGE,
            artifact_path=str(plan.artifact_path),
            digest_path=str(plan.artifact_path / "manifest.json"),
            sha256=manifest_identity.sha256,
            launch_path=str(plan.launch_path),
            launch_sha256=fixture.launch.sha256,
        )
        assert validate_publication(plan, chunk_size=7) == status
        status_bytes = plan.status_path.read_bytes()
        assert status_bytes == render_artifact_status(status)
        return (
            _tree_bytes(plan.artifact_path),
            status_bytes,
            expected_events,
            status,
        )

    first = run_order(("nested/beta.bin", "alpha.bin"))
    os.rename(plan.artifact_path, plan.attempt_dir / "first-artifact")
    os.rename(plan.status_path, plan.attempt_dir / "first-status.toml")
    second = run_order(("alpha.bin", "nested/beta.bin"))
    assert first == second


class _SinglePassMembers:
    def __init__(self, members: tuple[PackageMember, ...]) -> None:
        self.members = members
        self.iterations = 0

    def __iter__(self) -> Iterator[PackageMember]:
        self.iterations += 1
        if self.iterations != 1:
            raise AssertionError("package member specs were materialized twice")
        return iter(self.members)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        pytest.param("wrong-plan", InvalidPublicationPlanError, id="plan-type"),
        pytest.param("file-plan", InvalidPublicationPlanError, id="file-plan"),
        pytest.param("wrong-launch", InvalidPublicationPlanError, id="launch-type"),
        pytest.param("local-launch", InvalidPublicationPlanError, id="launch-kind"),
        pytest.param(
            "wrong-launch-path", InvalidPublicationPlanError, id="launch-path"
        ),
        pytest.param(
            "members-not-iterable", InvalidPublicationPlanError, id="members-iterable"
        ),
        pytest.param(
            "member-wrong-type", InvalidPublicationPlanError, id="member-type"
        ),
        pytest.param(
            "missing-member", InvalidPublicationPlanError, id="member-missing"
        ),
        pytest.param("extra-member", InvalidPublicationPlanError, id="member-extra"),
        pytest.param(
            "duplicate-member", InvalidPublicationPlanError, id="member-duplicate"
        ),
        pytest.param(
            "writer-mismatch", InvalidPublicationPlanError, id="member-inconsistent"
        ),
        pytest.param(
            "build-not-callable", InvalidPublicationPlanError, id="builder-callable"
        ),
        pytest.param(
            "parse-not-callable", InvalidPublicationPlanError, id="parser-callable"
        ),
        pytest.param(
            "validate-not-callable",
            InvalidPublicationPlanError,
            id="validator-callable",
        ),
        pytest.param("boolean-manifest-limit", ValueError, id="manifest-limit-boolean"),
        pytest.param("zero-manifest-limit", ValueError, id="manifest-limit-zero"),
        pytest.param("boolean-chunk", ValueError, id="chunk-boolean"),
        pytest.param("zero-chunk", ValueError, id="chunk-zero"),
        pytest.param("oversize-chunk", ValueError, id="chunk-oversize"),
    ],
)
def test_package_publication_rejects_invalid_inputs_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_error: type[Exception],
) -> None:
    fixture = _package_fixture(tmp_path)
    plan: object = fixture.plan
    launch: object = fixture.launch
    members: object = _member_specs(fixture)
    build_manifest: object = _render_manifest
    parse_manifest: object = _parse_manifest

    def valid_package_validator(root: Path) -> None:
        del root

    validate_package: object = valid_package_validator
    max_manifest_bytes: object = 4096
    chunk_size: object = 7

    if case == "wrong-plan":
        plan = object()
    elif case == "file-plan":
        plan = build_file_plan(fixture.plan.attempt_dir, fixture.plan.artifact_path)
    elif case == "wrong-launch":
        launch = object()
    elif case == "local-launch":
        launch = FileIdentity(PathKind.LOCAL, "launch.toml", fixture.launch.sha256)
    elif case == "wrong-launch-path":
        launch = FileIdentity(
            PathKind.EXTERNAL,
            str(fixture.plan.attempt_dir / "other.toml"),
            fixture.launch.sha256,
        )
    elif case == "members-not-iterable":
        members = object()
    elif case == "member-wrong-type":
        members = (object(), object())
    elif case == "missing-member":
        members = _member_specs(fixture)[:1]
    elif case == "extra-member":
        members = (
            *_member_specs(fixture),
            PackageMember("extra.bin", lambda h: None, lambda p: None),
        )
    elif case == "duplicate-member":
        specs = _member_specs(fixture)
        members = (specs[0], specs[0], specs[1])
    elif case == "writer-mismatch":
        specs = _member_specs(fixture)
        members = (
            PackageMember("alpha.bin", specs[0].write, specs[0].validate),
            specs[1],
        )
    elif case == "build-not-callable":
        build_manifest = object()
    elif case == "parse-not-callable":
        parse_manifest = object()
    elif case == "validate-not-callable":
        validate_package = object()
    elif case == "boolean-manifest-limit":
        max_manifest_bytes = True
    elif case == "zero-manifest-limit":
        max_manifest_bytes = 0
    elif case == "boolean-chunk":
        chunk_size = True
    elif case == "zero-chunk":
        chunk_size = 0
    elif case == "oversize-chunk":
        chunk_size = MAX_CHUNK_SIZE + 1

    def forbidden_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise AssertionError("invalid inputs reached a filesystem effect")

    monkeypatch.setattr(filesystem, "_open_fd", forbidden_open)

    with pytest.raises(expected_error):
        publish_package(
            cast(PublicationPlan, plan),
            cast(FileIdentity, launch),
            cast(Iterable[PackageMember], members),
            cast(Callable[[tuple[FileIdentity, ...]], bytes], build_manifest),
            cast(Callable[[bytes], Iterable[FileIdentity]], parse_manifest),
            cast(Callable[[Path], None], validate_package),
            max_manifest_bytes=cast(int, max_manifest_bytes),
            chunk_size=cast(int, chunk_size),
        )

    assert not fixture.plan.artifact_path.exists()
    assert not fixture.plan.status_path.exists()
    assert list(fixture.plan.artifact_path.parent.glob(".trafficlab-staging-*")) == []


@pytest.mark.integration
def test_package_member_specs_and_manifest_parser_are_materialized_once_per_use(
    tmp_path: Path,
) -> None:
    fixture = _package_fixture(tmp_path)
    members = _SinglePassMembers(_member_specs(fixture))
    parser_iterations: list[int] = []

    def parse_once(data: bytes) -> Iterable[FileIdentity]:
        parsed = tuple(_parse_manifest(data))

        def delayed() -> Iterable[FileIdentity]:
            parser_iterations.append(1)
            yield from parsed

        return delayed()

    _publish_fixture(fixture, members=members, parse_manifest=parse_once)

    assert members.iterations == 1
    assert len(parser_iterations) == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    ("boundary", "expected_error"),
    [
        pytest.param("writer", ArtifactWriteError, id="writer-callback"),
        pytest.param("writer-output", ArtifactValidationError, id="writer-output"),
        pytest.param(
            "member-validator", ArtifactValidationError, id="member-validator"
        ),
        pytest.param(
            "manifest-builder", ArtifactValidationError, id="manifest-builder"
        ),
        pytest.param("manifest-non-bytes", ArtifactValidationError, id="manifest-type"),
        pytest.param("manifest-oversize", ArtifactValidationError, id="manifest-size"),
        pytest.param("manifest-parser", ArtifactValidationError, id="manifest-parser"),
        pytest.param("parser-iterator", ArtifactValidationError, id="parser-iterator"),
        pytest.param(
            "package-validator", ArtifactValidationError, id="package-validator"
        ),
    ],
)
def test_package_publication_retains_tree_for_callback_and_manifest_failures(
    tmp_path: Path,
    boundary: str,
    expected_error: type[Exception],
) -> None:
    fixture = _package_fixture(tmp_path)
    cause = RuntimeError(f"injected {boundary} failure")
    default_specs = {spec.path: spec for spec in _member_specs(fixture)}
    members = tuple(default_specs.values())
    build_manifest: Callable[[tuple[FileIdentity, ...]], bytes] = _render_manifest
    parse_manifest: Callable[[bytes], Iterable[FileIdentity]] = _parse_manifest

    def successful_package_validator(root: Path) -> None:
        del root

    validate_package: Callable[[Path], None] = successful_package_validator
    max_manifest_bytes = 4096

    if boundary in {"writer", "writer-output"}:
        original = default_specs["alpha.bin"]

        def failing_writer(handle: BinaryIO) -> None:
            if boundary == "writer":
                handle.write(b"partial")
                raise cause
            handle.write(b"semantically invalid")

        members = (
            PackageMember("alpha.bin", failing_writer, original.validate),
            default_specs["nested/beta.bin"],
        )
    elif boundary == "member-validator":
        original = default_specs["alpha.bin"]

        def failing_validator(path: Path) -> None:
            assert path.read_bytes() == _default_payloads()["alpha.bin"]
            raise cause

        members = (
            PackageMember("alpha.bin", original.write, failing_validator),
            default_specs["nested/beta.bin"],
        )
    elif boundary == "manifest-builder":

        def failing_builder(identities: tuple[FileIdentity, ...]) -> bytes:
            del identities
            raise cause

        build_manifest = failing_builder
    elif boundary == "manifest-non-bytes":

        def invalid_builder(identities: tuple[FileIdentity, ...]) -> bytes:
            del identities
            return cast(bytes, bytearray(b"not exact bytes"))

        build_manifest = invalid_builder
    elif boundary == "manifest-oversize":
        max_manifest_bytes = 8
    elif boundary == "manifest-parser":

        def failing_parser(data: bytes) -> Iterable[FileIdentity]:
            del data
            raise cause

        parse_manifest = failing_parser
    elif boundary == "parser-iterator":

        def delayed_failure(data: bytes) -> Iterable[FileIdentity]:
            parsed = tuple(_parse_manifest(data))
            yield parsed[0]
            raise cause

        parse_manifest = delayed_failure
    elif boundary == "package-validator":

        def failing_package_validator(root: Path) -> None:
            assert (root / "manifest.json").is_file()
            raise cause

        validate_package = failing_package_validator

    with pytest.raises(expected_error) as caught:
        _publish_fixture(
            fixture,
            members=members,
            build_manifest=build_manifest,
            parse_manifest=parse_manifest,
            validate_package=validate_package,
            max_manifest_bytes=max_manifest_bytes,
        )

    retained = _assert_pre_package_failure(
        cast(_EvidenceFailure, caught.value), fixture.plan
    )
    if boundary not in {"writer", "writer-output", "member-validator"}:
        assert (
            retained / "launch.toml"
        ).read_bytes() == fixture.plan.launch_path.read_bytes()
    if boundary == "writer":
        assert (retained / "alpha.bin").read_bytes() == b"partial"


@pytest.mark.unit
@pytest.mark.parametrize(
    "violation",
    [
        pytest.param("self-entry", id="self-entry"),
        pytest.param("omission", id="omission"),
        pytest.param("extra", id="extra"),
        pytest.param("wrong-digest", id="wrong-digest"),
        pytest.param("wrong-kind", id="wrong-kind"),
        pytest.param("duplicate", id="duplicate"),
        pytest.param("traversal", id="traversal"),
        pytest.param("malformed-schema", id="schema"),
    ],
)
def test_package_publication_rejects_every_manifest_membership_violation(
    tmp_path: Path,
    violation: str,
) -> None:
    fixture = _package_fixture(tmp_path)

    def parser(data: bytes) -> Iterable[FileIdentity]:
        if violation == "malformed-schema":
            raise ValueError("injected manifest schema failure")
        identities = list(_parse_manifest(data))
        if violation == "self-entry":
            identities.append(
                FileIdentity(PathKind.LOCAL, "manifest.json", fixture.launch.sha256)
            )
        elif violation == "omission":
            identities.pop(0)
        elif violation == "extra":
            identities.append(
                FileIdentity(PathKind.LOCAL, "extra.bin", fixture.launch.sha256)
            )
        elif violation == "wrong-digest":
            identities[0] = FileIdentity(
                PathKind.LOCAL,
                identities[0].path,
                Sha256Digest("0" * 64),
            )
        elif violation == "wrong-kind":
            identities[0] = FileIdentity(
                PathKind.EXTERNAL,
                str(fixture.plan.artifact_path / identities[0].path),
                identities[0].sha256,
            )
        elif violation == "duplicate":
            identities.append(identities[0])
        elif violation == "traversal":
            identities.append(
                FileIdentity(PathKind.LOCAL, "../escape", fixture.launch.sha256)
            )
        return identities

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(fixture, parse_manifest=parser)

    retained = _assert_pre_package_failure(caught.value, fixture.plan)
    assert (retained / "manifest.json").is_file()
    assert caught.value.__cause__ is not None


@pytest.mark.integration
@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param("extra-file", id="extra-file"),
        pytest.param("missing-member", id="missing-member"),
        pytest.param("mutated-member", id="mutated-member"),
        pytest.param("symlink", id="symlink"),
        pytest.param("hard-link", id="hard-link"),
        pytest.param("fifo", id="fifo"),
        pytest.param("extra-directory", id="extra-directory"),
        pytest.param("member-as-directory", id="member-as-directory"),
        pytest.param("nested-prefix-type", id="nested-prefix-type"),
        pytest.param("manifest-mutation", id="manifest-mutation"),
    ],
)
def test_package_publication_rejects_callback_side_tree_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _package_fixture(tmp_path)

    def mutate(root: Path) -> None:
        alpha = root / "alpha.bin"
        if mutation == "extra-file":
            _write_private(root / "extra.bin", b"injected")
        elif mutation == "missing-member":
            alpha.unlink()
        elif mutation == "mutated-member":
            alpha.write_bytes(b"changed after hash")
        elif mutation == "symlink":
            os.symlink("alpha.bin", root / "extra-link")
        elif mutation == "hard-link":
            os.link(alpha, root / "extra-hard-link")
        elif mutation == "fifo":
            os.mkfifo(root / "extra-fifo", mode=0o600)
        elif mutation == "extra-directory":
            (root / "extra-directory").mkdir(mode=0o700)
        elif mutation == "member-as-directory":
            alpha.unlink()
            alpha.mkdir(mode=0o700)
        elif mutation == "nested-prefix-type":
            os.rename(root / "nested", root / "moved-nested")
            _write_private(root / "nested", b"prefix became a file")
        else:
            (root / "manifest.json").write_bytes(b"changed manifest")

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(fixture, validate_package=mutate)

    retained = _assert_pre_package_failure(caught.value, fixture.plan)
    assert retained.exists()


@pytest.mark.integration
def test_package_publication_rejects_member_mutation_from_manifest_builder(
    tmp_path: Path,
) -> None:
    fixture = _package_fixture(tmp_path)

    def mutating_builder(identities: tuple[FileIdentity, ...]) -> bytes:
        staging_root = next(
            path
            for path in fixture.plan.artifact_path.parent.glob(".trafficlab-staging-*")
            if path.is_dir()
        )
        (staging_root / "alpha.bin").write_bytes(b"mutated by builder")
        return _render_manifest(identities)

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(fixture, build_manifest=mutating_builder)

    retained = _assert_pre_package_failure(caught.value, fixture.plan)
    assert (retained / "alpha.bin").read_bytes() == b"mutated by builder"


@pytest.mark.integration
def test_package_publication_rejects_runtime_nested_prefix_conflict(
    tmp_path: Path,
) -> None:
    fixture = _package_fixture(tmp_path)
    specs = {spec.path: spec for spec in _member_specs(fixture)}
    alpha = specs["alpha.bin"]

    def conflicting_alpha_validator(path: Path) -> None:
        alpha.validate(path)
        os.rename(path.parent / "nested", path.parent / "moved-nested")
        _write_private(path.parent / "nested", b"blocks planned directory")

    members = (
        PackageMember("alpha.bin", alpha.write, conflicting_alpha_validator),
        specs["nested/beta.bin"],
    )

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(fixture, members=members)

    retained = _assert_pre_package_failure(caught.value, fixture.plan)
    assert (retained / "nested").is_file()
    assert (retained / "moved-nested").is_dir()


@pytest.mark.integration
def test_package_publication_rejects_wrong_launch_digest_before_staging(
    tmp_path: Path,
) -> None:
    fixture = _package_fixture(tmp_path)
    wrong_launch = FileIdentity(
        PathKind.EXTERNAL,
        str(fixture.plan.launch_path),
        Sha256Digest("0" * 64),
    )

    with pytest.raises(ArtifactValidationError) as caught:
        publish_package(
            fixture.plan,
            wrong_launch,
            _member_specs(fixture),
            _render_manifest,
            _parse_manifest,
            lambda root: None,
            max_manifest_bytes=4096,
        )

    assert caught.value.retained_paths == ()
    assert caught.value.orphan_path is None
    assert not fixture.plan.artifact_path.exists()
    assert not fixture.plan.status_path.exists()
    assert list(fixture.plan.artifact_path.parent.glob(".trafficlab-staging-*")) == []


@pytest.mark.integration
@pytest.mark.parametrize("change", ["mutate", "replace"])
def test_package_launch_copy_rejects_source_change_during_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    fixture = _package_fixture(tmp_path)
    original_read = filesystem._read_fd
    changed = False

    def changing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        data = original_read(descriptor, count)
        if not changed:
            changed = True
            if change == "mutate":
                fixture.plan.launch_path.write_bytes(b"changed during copy\n")
            else:
                replacement = fixture.plan.attempt_dir / "replacement.toml"
                _write_private(replacement, b"replacement during copy\n")
                os.replace(replacement, fixture.plan.launch_path)
        return data

    monkeypatch.setattr(filesystem, "_read_fd", changing_read)

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(fixture, chunk_size=3)

    retained = _assert_pre_package_failure(caught.value, fixture.plan)
    assert changed
    assert (retained / "launch.toml").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("boundary", "injected"),
    [
        pytest.param("short-read", b"", id="short-read"),
        pytest.param("invalid-read", bytearray(b"invalid"), id="invalid-read"),
        pytest.param("oversize-read", b"too many bytes", id="oversize-read"),
        pytest.param("invalid-write", 0, id="invalid-write-count"),
    ],
)
def test_package_launch_copy_rejects_invalid_io_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    injected: object,
) -> None:
    fixture = _package_fixture(tmp_path)
    if boundary in {"short-read", "invalid-read", "oversize-read"}:
        original_read = filesystem._read_fd
        injected_once = False

        def invalid_read(descriptor: int, count: int) -> bytes:
            nonlocal injected_once
            if not injected_once:
                injected_once = True
                if boundary == "oversize-read":
                    return b"x" * (count + 1)
                return cast(bytes, injected)
            return original_read(descriptor, count)

        monkeypatch.setattr(filesystem, "_read_fd", invalid_read)
    else:
        original_write = filesystem._write_fd
        write_calls = 0

        def invalid_launch_write(descriptor: int, data: object) -> int:
            nonlocal write_calls
            write_calls += 1
            if write_calls == 3:
                return cast(int, injected)
            return original_write(descriptor, data)

        monkeypatch.setattr(filesystem, "_write_fd", invalid_launch_write)

    with pytest.raises((ArtifactWriteError, ArtifactValidationError)) as caught:
        _publish_fixture(fixture, chunk_size=3)

    retained = _assert_pre_package_failure(
        cast(_EvidenceFailure, caught.value), fixture.plan
    )
    assert (retained / "launch.toml").exists()


@pytest.mark.unit
def test_package_launch_copy_surfaces_source_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    launch_inode = fixture.plan.launch_path.stat().st_ino
    original_close = filesystem._close_fd
    cause = OSError(errno.EIO, "injected launch source close failure")
    failed = False

    def failing_close(descriptor: int) -> None:
        nonlocal failed
        is_launch = os.fstat(descriptor).st_ino == launch_inode
        original_close(descriptor)
        if is_launch and not failed:
            failed = True
            raise cause

    monkeypatch.setattr(filesystem, "_close_fd", failing_close)

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(fixture)

    _assert_pre_package_failure(caught.value, fixture.plan)
    assert caught.value.__cause__ is cause


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        pytest.param("live-mutate-builder", id="live-builder-mutation"),
        pytest.param("live-replace-validator", id="live-validator-replacement"),
        pytest.param("copy-mutate", id="copied-launch-mutation"),
        pytest.param("copy-hardlink", id="copied-launch-alias"),
    ],
)
def test_package_publication_revalidates_live_and_copied_launch_after_callbacks(
    tmp_path: Path,
    change: str,
) -> None:
    fixture = _package_fixture(tmp_path)

    def builder(identities: tuple[FileIdentity, ...]) -> bytes:
        if change == "live-mutate-builder":
            fixture.plan.launch_path.write_bytes(b"builder changed launch\n")
        return _render_manifest(identities)

    def validator(root: Path) -> None:
        if change == "live-replace-validator":
            replacement = fixture.plan.attempt_dir / "new-launch.toml"
            _write_private(replacement, b"validator replacement\n")
            os.replace(replacement, fixture.plan.launch_path)
        elif change == "copy-mutate":
            (root / "launch.toml").write_bytes(b"copied launch changed\n")
        elif change == "copy-hardlink":
            (root / "launch.toml").unlink()
            os.link(fixture.plan.launch_path, root / "launch.toml")

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(
            fixture,
            build_manifest=builder,
            validate_package=validator,
        )

    retained = _assert_pre_package_failure(caught.value, fixture.plan)
    assert (retained / "launch.toml").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("boundary", "expected_error"),
    [
        pytest.param("staging-open", ArtifactWriteError, id="staging-root-open"),
        pytest.param("nested-mkdir", ArtifactWriteError, id="nested-mkdir"),
        pytest.param("nested-open", ArtifactWriteError, id="nested-open"),
        pytest.param("member-open", ArtifactWriteError, id="member-open"),
        pytest.param("member-flush", ArtifactWriteError, id="member-flush"),
        pytest.param("member-close", ArtifactWriteError, id="member-close"),
        pytest.param("launch-open", ArtifactValidationError, id="launch-open"),
        pytest.param("launch-read", ArtifactValidationError, id="launch-read"),
        pytest.param("manifest-write", ArtifactWriteError, id="manifest-write"),
        pytest.param("walk", ArtifactValidationError, id="membership-walk"),
        pytest.param("snapshot", ArtifactValidationError, id="lineage-snapshot"),
        pytest.param(
            "manifest-contract", ArtifactValidationError, id="manifest-contract"
        ),
        pytest.param("tree-revalidation", ArtifactWriteError, id="tree-revalidation"),
        pytest.param("artifact-rename", AtomicPublicationError, id="artifact-rename"),
    ],
)
def test_package_publication_injects_pre_rename_filesystem_and_lineage_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    expected_error: type[Exception],
) -> None:
    fixture = _package_fixture(tmp_path)
    cause: BaseException = OSError(errno.EIO, f"injected {boundary} failure")

    if boundary == "staging-open":
        original_open = filesystem._open_fd

        def failing_staging_open(
            path: str,
            flags: int,
            *,
            dir_fd: int | None = None,
            mode: int = 0o777,
        ) -> int:
            if path.startswith(".trafficlab-staging-") and flags & os.O_DIRECTORY:
                raise cause
            return original_open(path, flags, dir_fd=dir_fd, mode=mode)

        monkeypatch.setattr(filesystem, "_open_fd", failing_staging_open)
    elif boundary == "nested-mkdir":
        original_mkdir = filesystem._mkdir_entry

        def failing_mkdir(name: str, *, dir_fd: int, mode: int) -> None:
            if name == "nested":
                raise cause
            original_mkdir(name, dir_fd=dir_fd, mode=mode)

        monkeypatch.setattr(filesystem, "_mkdir_entry", failing_mkdir)
    elif boundary == "nested-open":
        original_open = filesystem._open_fd

        def failing_nested_open(
            path: str,
            flags: int,
            *,
            dir_fd: int | None = None,
            mode: int = 0o777,
        ) -> int:
            if path == "nested" and flags & os.O_DIRECTORY:
                raise cause
            return original_open(path, flags, dir_fd=dir_fd, mode=mode)

        monkeypatch.setattr(filesystem, "_open_fd", failing_nested_open)
    elif boundary == "member-open":
        original_open = filesystem._open_fd

        def failing_member_open(
            path: str,
            flags: int,
            *,
            dir_fd: int | None = None,
            mode: int = 0o777,
        ) -> int:
            if path == "alpha.bin" and flags & os.O_CREAT:
                raise cause
            return original_open(path, flags, dir_fd=dir_fd, mode=mode)

        monkeypatch.setattr(filesystem, "_open_fd", failing_member_open)
    elif boundary in {"member-flush", "member-close"}:
        if boundary == "member-flush":

            def failing_flush(handle: BinaryIO) -> None:
                del handle
                raise cause

            monkeypatch.setattr(filesystem, "_flush_writer", failing_flush)
        else:
            original_close = filesystem._close_writer

            def failing_close(handle: BinaryIO) -> None:
                original_close(handle)
                raise cause

            monkeypatch.setattr(filesystem, "_close_writer", failing_close)
    elif boundary == "launch-open":
        original_open = filesystem._open_fd

        def failing_launch_open(
            path: str,
            flags: int,
            *,
            dir_fd: int | None = None,
            mode: int = 0o777,
        ) -> int:
            if path == "launch.toml" and not flags & os.O_CREAT:
                raise cause
            return original_open(path, flags, dir_fd=dir_fd, mode=mode)

        monkeypatch.setattr(filesystem, "_open_fd", failing_launch_open)
    elif boundary == "launch-read":

        def failing_read(descriptor: int, count: int) -> bytes:
            del descriptor, count
            raise cause

        monkeypatch.setattr(filesystem, "_read_fd", failing_read)
    elif boundary == "manifest-write":
        original_write = filesystem._write_fd

        def failing_manifest_write(descriptor: int, data: object) -> int:
            if bytes(cast(bytes, data)).startswith(b"{"):
                raise cause
            return original_write(descriptor, data)

        monkeypatch.setattr(filesystem, "_write_fd", failing_manifest_write)
    elif boundary == "walk":

        def failing_list(descriptor: int) -> tuple[str, ...]:
            del descriptor
            raise cause

        monkeypatch.setattr(filesystem, "_list_directory", failing_list)
    elif boundary == "snapshot":
        cause = FileSnapshotError("injected local snapshot failure")

        def failing_snapshot(
            root: Path,
            relative_path: str,
            *,
            chunk_size: int,
        ) -> FileIdentity:
            del root, relative_path, chunk_size
            raise cause

        monkeypatch.setattr(publication, "_snapshot_local_file", failing_snapshot)
    elif boundary == "manifest-contract":
        cause = ManifestValidationError("injected manifest contract failure")

        def failing_manifest_validation(
            *args: object, **kwargs: object
        ) -> tuple[FileIdentity, ...]:
            del args, kwargs
            raise cause

        monkeypatch.setattr(
            publication,
            "_validate_package_members",
            failing_manifest_validation,
        )
    elif boundary == "tree-revalidation":

        def failing_tree_revalidation(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise cause

        monkeypatch.setattr(
            filesystem,
            "_revalidate_staged_directory",
            failing_tree_revalidation,
        )
    elif boundary == "artifact-rename":

        def failing_rename(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise cause

        monkeypatch.setattr(filesystem, "_renameat2_call", failing_rename)

    with pytest.raises(expected_error) as caught:
        _publish_fixture(fixture)

    retained = _assert_pre_package_failure(
        cast(_EvidenceFailure, caught.value), fixture.plan
    )
    assert retained.exists()
    assert caught.value.__cause__ is cause


@pytest.mark.unit
def test_package_publication_root_mkdir_failure_has_no_false_retained_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    cause = OSError(errno.ENOSPC, "injected package staging mkdir failure")

    def failing_mkdir(name: str, *, dir_fd: int, mode: int) -> None:
        del name, dir_fd, mode
        raise cause

    monkeypatch.setattr(filesystem, "_mkdir_entry", failing_mkdir)

    with pytest.raises(ArtifactWriteError) as caught:
        _publish_fixture(fixture)

    assert caught.value.__cause__ is cause
    assert caught.value.retained_paths == ()
    assert caught.value.orphan_path is None
    assert not fixture.plan.artifact_path.exists()
    assert not fixture.plan.status_path.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("boundary", "expected_error", "expect_status_staging"),
    [
        pytest.param(
            "published-contract",
            ArtifactValidationError,
            False,
            id="published-contract",
        ),
        pytest.param("live-launch", ArtifactValidationError, False, id="live-launch"),
        pytest.param("tree-close", ArtifactValidationError, False, id="tree-close"),
        pytest.param("status-token", ArtifactWriteError, False, id="status-token"),
        pytest.param(
            "status-render", ArtifactValidationError, False, id="status-render"
        ),
        pytest.param("status-create", ArtifactWriteError, False, id="status-create"),
        pytest.param("status-write", ArtifactWriteError, True, id="status-write"),
        pytest.param("status-flush", ArtifactWriteError, True, id="status-flush"),
        pytest.param("status-close", ArtifactWriteError, True, id="status-close"),
        pytest.param(
            "status-envelope", ArtifactValidationError, True, id="status-envelope"
        ),
        pytest.param("status-parse", ArtifactValidationError, True, id="status-parse"),
        pytest.param("status-rename", AtomicPublicationError, True, id="status-rename"),
        pytest.param(
            "final-validation", ArtifactValidationError, False, id="final-validation"
        ),
        pytest.param(
            "destination-close", ArtifactValidationError, False, id="destination-close"
        ),
        pytest.param(
            "attempt-close", ArtifactValidationError, False, id="attempt-close"
        ),
        pytest.param(
            "attempt-repin", ArtifactValidationError, False, id="attempt-repin"
        ),
        pytest.param(
            "final-attempt-close",
            ArtifactValidationError,
            False,
            id="final-attempt-close",
        ),
    ],
)
def test_package_publication_reports_every_artifact_to_status_crash_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    expected_error: type[Exception],
    expect_status_staging: bool,
) -> None:
    fixture = _package_fixture(tmp_path)
    plan = fixture.plan
    cause: BaseException = RuntimeError(f"injected {boundary} failure")

    if boundary == "published-contract":
        cause = ManifestValidationError("injected published package failure")
        original_validate = publication._validate_package_members
        calls = 0

        def failing_third_contract(
            *args: object, **kwargs: object
        ) -> tuple[FileIdentity, ...]:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise cause
            return original_validate(*args, **kwargs)

        monkeypatch.setattr(
            publication, "_validate_package_members", failing_third_contract
        )
    elif boundary == "live-launch":
        cause = FileSnapshotError("injected post-artifact launch failure")
        original_validate = publication._validate_external_file
        calls = 0

        def failing_third_launch(
            expected: FileIdentity,
            *,
            chunk_size: int,
        ) -> FileIdentity:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise cause
            return original_validate(expected, chunk_size=chunk_size)

        monkeypatch.setattr(
            publication, "_validate_external_file", failing_third_launch
        )
    elif boundary == "tree-close":
        cause = OSError(errno.EIO, "injected package descriptor close failure")
        original_close = filesystem._close_staged_directory

        def failing_tree_close(staged: object) -> OSError | None:
            result = original_close(staged)
            assert result is None
            return cause

        monkeypatch.setattr(filesystem, "_close_staged_directory", failing_tree_close)
    elif boundary == "status-token":
        token_calls = 0

        def failing_status_token() -> str:
            nonlocal token_calls
            token_calls += 1
            if token_calls == 2:
                raise cause
            return f"package-token-{token_calls}"

        monkeypatch.setattr(filesystem, "_publication_token", failing_status_token)
    elif boundary == "status-render":

        def failing_render(status: ArtifactStatus) -> bytes:
            del status
            raise cause

        monkeypatch.setattr(publication, "_render_artifact_status", failing_render)
    elif boundary == "status-create":
        original_create = filesystem._create_staged_file

        def failing_status_create(parent: object, destination_name: str) -> object:
            if destination_name == plan.status_path.name:
                raise cause
            return original_create(parent, destination_name)

        monkeypatch.setattr(filesystem, "_create_staged_file", failing_status_create)
    elif boundary == "status-write":
        cause = OSError(errno.EIO, "injected status write failure")
        original_write = filesystem._write_fd

        def failing_status_write(descriptor: int, data: object) -> int:
            if bytes(cast(bytes, data)).startswith(
                b'schema_version = 1\nstate = "published"'
            ):
                raise cause
            return original_write(descriptor, data)

        monkeypatch.setattr(filesystem, "_write_fd", failing_status_write)
    elif boundary in {"status-flush", "status-close"}:
        if boundary == "status-flush":
            original_flush = filesystem._flush_writer
            calls = 0

            def failing_fifth_flush(handle: BinaryIO) -> None:
                nonlocal calls
                calls += 1
                if calls == 5:
                    raise cause
                original_flush(handle)

            monkeypatch.setattr(filesystem, "_flush_writer", failing_fifth_flush)
        else:
            original_close = filesystem._close_writer
            calls = 0

            def failing_fifth_close(handle: BinaryIO) -> None:
                nonlocal calls
                calls += 1
                original_close(handle)
                if calls == 5:
                    raise cause

            monkeypatch.setattr(filesystem, "_close_writer", failing_fifth_close)
    elif boundary == "status-envelope":
        cause = OSError(errno.ESTALE, "injected status envelope failure")

        def failing_status_snapshot(*args: object, **kwargs: object) -> bytes:
            del args, kwargs
            raise cause

        monkeypatch.setattr(
            filesystem, "_snapshot_staged_status", failing_status_snapshot
        )
    elif boundary == "status-parse":

        def failing_status_parse(data: bytes) -> ArtifactStatus:
            del data
            raise cause

        monkeypatch.setattr(publication, "_parse_artifact_status", failing_status_parse)
    elif boundary == "status-rename":
        cause = OSError(errno.EIO, "injected status rename failure")
        original_rename = filesystem._renameat2_call
        calls = 0

        def failing_second_rename(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise cause
            original_rename(*args, **kwargs)

        monkeypatch.setattr(filesystem, "_renameat2_call", failing_second_rename)
    elif boundary == "final-validation":

        def failing_final_validation(
            publication_plan: PublicationPlan,
            *,
            chunk_size: int,
        ) -> ArtifactStatus:
            del publication_plan, chunk_size
            raise cause

        monkeypatch.setattr(
            publication, "_validate_publication", failing_final_validation
        )
    elif boundary in {"destination-close", "attempt-close", "final-attempt-close"}:
        cause = OSError(errno.EIO, f"injected {boundary} failure")
        original_close = filesystem._close_pinned_directory
        expected_call = {
            "destination-close": 1,
            "attempt-close": 2,
            "final-attempt-close": 3,
        }[boundary]
        calls = 0

        def failing_selected_close(directory: object) -> OSError | None:
            nonlocal calls
            calls += 1
            result = original_close(directory)
            return cause if calls == expected_call else result

        monkeypatch.setattr(
            filesystem, "_close_pinned_directory", failing_selected_close
        )
    elif boundary == "attempt-repin":
        cause = OSError(errno.EIO, "injected package attempt repin failure")
        original_pin = filesystem._pin_attempt_directory
        calls = 0

        def failing_second_pin(path: Path) -> object:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise cause
            return original_pin(path)

        monkeypatch.setattr(filesystem, "_pin_attempt_directory", failing_second_pin)

    with pytest.raises(expected_error) as caught:
        _publish_fixture(fixture)

    error = cast(_EvidenceFailure, caught.value)
    assert error.__cause__ is cause
    if boundary == "final-validation":
        assert error.orphan_path == plan.artifact_path
        assert error.retained_paths == ()
        assert plan.artifact_path.is_dir()
        assert plan.status_path.is_file()
    elif boundary == "final-attempt-close":
        assert error.orphan_path is None
        assert error.retained_paths == ()
        assert plan.artifact_path.is_dir()
        assert plan.status_path.is_file()
    else:
        _assert_orphan_package(
            error,
            plan,
            expect_status_staging=expect_status_staging,
        )


@pytest.mark.integration
@pytest.mark.parametrize("conflict", ("artifact", "status"))
def test_package_publication_rejects_existing_commit_state(
    tmp_path: Path,
    conflict: str,
) -> None:
    fixture = _package_fixture(tmp_path)
    if conflict == "artifact":
        fixture.plan.artifact_path.mkdir(mode=0o700)
    else:
        _write_private(fixture.plan.status_path, b"unexpected status\n")

    with pytest.raises(PublicationConflictError) as caught:
        _publish_fixture(fixture)

    assert caught.value.orphan_path == (
        fixture.plan.artifact_path if conflict == "artifact" else None
    )
    assert caught.value.retained_paths == ()


@pytest.mark.unit
def test_filesystem_private_guards_cover_invalid_tokens_and_missing_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = filesystem._pin_owned_directory(tmp_path)
    try:
        monkeypatch.setattr(filesystem, "_publication_token", lambda: "invalid token")
        with pytest.raises(OSError, match="publication token is invalid"):
            filesystem._create_staged_directory(parent, "artifact")
        with pytest.raises(OSError, match="publication token is invalid"):
            filesystem._create_staged_file(parent, "artifact")

        def denied_stat(*args: object, **kwargs: object) -> os.stat_result:
            del args, kwargs
            raise OSError(errno.EACCES, "injected entry access failure")

        monkeypatch.setattr(filesystem, "_stat_entry", denied_stat)
        with pytest.raises(OSError) as caught:
            filesystem._entry_exists(parent, "artifact")
        assert caught.value.errno == errno.EACCES
    finally:
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.integration
def test_filesystem_rejects_replaced_published_file_and_package_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = filesystem._pin_owned_directory(tmp_path)
    file_staging, handle = filesystem._create_staged_file(parent, "artifact.bin")
    handle.close()
    package_staging = filesystem._create_staged_directory(parent, "artifact")
    try:
        os.rename(file_staging.path, tmp_path / "artifact.bin")
        replacement_file = tmp_path / "replacement.bin"
        _write_private(replacement_file, b"replacement")
        os.replace(replacement_file, tmp_path / "artifact.bin")
        monkeypatch.setattr(
            filesystem,
            "_revalidate_pinned_directory",
            lambda directory: None,
        )
        with pytest.raises(OSError, match="published artifact binding changed"):
            filesystem._revalidate_published_file(
                parent,
                "artifact.bin",
                file_staging,
            )

        os.rename(package_staging.path, tmp_path / "artifact")
        (tmp_path / "artifact").rmdir()
        (tmp_path / "artifact").mkdir(mode=0o700)
        with pytest.raises(OSError, match="published package root changed"):
            filesystem._revalidate_published_directory(
                parent,
                "artifact",
                package_staging,
            )
    finally:
        close_error = filesystem._close_staged_directory(package_staging)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_filesystem_rejects_unsafe_private_staging_envelopes(tmp_path: Path) -> None:
    unsafe_file = tmp_path / "unsafe-file"
    unsafe_file.write_bytes(b"unsafe")
    unsafe_file.chmod(0o644)
    with pytest.raises(OSError, match="staged publication file"):
        filesystem._require_private_staged_file(unsafe_file.stat())
    with pytest.raises(OSError, match="staged package directory"):
        filesystem._require_private_staged_directory(unsafe_file.stat())


@pytest.mark.unit
def test_unbuffered_writer_rejects_closed_descriptor_and_surfaces_close_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "writer.bin"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    writer = filesystem._UnbufferedWriter(descriptor)
    writer.close()
    with pytest.raises(ValueError, match="closed publication file"):
        writer.fileno()

    failing_descriptor = os.open(
        tmp_path / "failing-writer.bin",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    failing_writer = filesystem._UnbufferedWriter(failing_descriptor)
    original_close = filesystem._close_fd

    def close_then_fail(value: int) -> None:
        original_close(value)
        raise OSError(errno.EIO, "injected descriptor close failure")

    monkeypatch.setattr(filesystem, "_close_fd", close_then_fail)
    with pytest.raises(OSError, match="injected descriptor close failure"):
        failing_writer.close()


@pytest.mark.unit
def test_publication_internal_identity_mismatch_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    plan = fixture.plan
    parent = filesystem._pin_owned_directory(plan.artifact_path.parent)
    staged_file, handle = filesystem._create_staged_file(parent, "file.bin")
    handle.close()
    staged_directory = filesystem._create_staged_directory(parent, "artifact")
    try:
        changed_launch = FileIdentity(
            PathKind.EXTERNAL,
            str(plan.launch_path),
            Sha256Digest("0" * 64),
        )
        monkeypatch.setattr(
            publication,
            "_validate_external_file",
            lambda identity, *, chunk_size: changed_launch,
        )
        with pytest.raises(ArtifactValidationError, match="startup record failed"):
            publication._validate_launch_identity(
                fixture.launch,
                chunk_size=7,
                orphan_path=None,
            )

        invalid_staged_identity = FileIdentity(
            PathKind.LOCAL,
            "file.bin",
            Sha256Digest("0" * 64),
        )
        monkeypatch.setattr(
            publication,
            "_snapshot_external_file",
            lambda path, *, chunk_size: invalid_staged_identity,
        )
        with pytest.raises(ArtifactValidationError, match="staged artifact failed"):
            publication._snapshot_staging(staged_file, chunk_size=7)

        invalid_member_identity = FileIdentity(
            PathKind.EXTERNAL,
            str(staged_directory.path / "alpha.bin"),
            Sha256Digest("0" * 64),
        )
        monkeypatch.setattr(
            publication,
            "_snapshot_local_file",
            lambda root, path, *, chunk_size: invalid_member_identity,
        )
        with pytest.raises(ArtifactValidationError, match="package members failed"):
            publication._snapshot_package_members(
                staged_directory,
                ("alpha.bin",),
                chunk_size=7,
            )
    finally:
        close_error = filesystem._close_staged_directory(staged_directory)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_status_snapshot_rejects_invalid_reads_sizes_and_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = filesystem._pin_owned_directory(tmp_path)
    staged, handle = filesystem._create_staged_file(parent, "artifact-status.toml")
    assert handle.write(b"status") == len(b"status")
    handle.close()
    try:
        original_read = filesystem._read_fd
        monkeypatch.setattr(
            filesystem,
            "_read_fd",
            lambda descriptor, count: b"x" * (count + 1),
        )
        with pytest.raises(OSError, match="invalid bytes"):
            filesystem._snapshot_staged_status(
                staged,
                max_bytes=16,
                chunk_size=3,
            )
        monkeypatch.setattr(filesystem, "_read_fd", original_read)
        with pytest.raises(OSError, match="exceeds maximum"):
            filesystem._snapshot_staged_status(
                staged,
                max_bytes=2,
                chunk_size=3,
            )

        original_close = filesystem._close_fd

        def close_after_success(descriptor: int) -> None:
            original_close(descriptor)
            raise OSError(errno.EIO, "injected status close failure")

        monkeypatch.setattr(filesystem, "_close_fd", close_after_success)
        with pytest.raises(OSError, match="injected status close failure"):
            filesystem._snapshot_staged_status(
                staged,
                max_bytes=16,
                chunk_size=3,
            )
    finally:
        monkeypatch.setattr(filesystem, "_close_fd", original_close)
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_publication_preflight_and_writer_close_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    monkeypatch.setattr(
        publication,
        "render_artifact_status",
        lambda status: (_ for _ in ()).throw(InvalidArtifactStatusError("bad status")),
    )
    with pytest.raises(InvalidPublicationPlanError, match="canonical detached status"):
        _publish_fixture(fixture)

    parent = filesystem._pin_owned_directory(tmp_path)
    staged, handle = filesystem._create_staged_file(parent, "writer.bin")
    try:
        monkeypatch.setattr(filesystem, "_flush_writer", lambda writer: None)
        monkeypatch.setattr(filesystem, "_close_writer", lambda writer: None)
        with pytest.raises(ArtifactWriteError) as caught:
            publication._run_owned_writer(
                staged,
                handle,
                lambda writer: None,
                orphan_path=None,
            )
        assert (
            str(caught.value.__cause__) == "[Errno 5] publication writer did not close"
        )
    finally:
        handle.close()
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_publication_preserves_orphan_and_mismatch_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    plan = fixture.plan

    def orphan_validation(
        publication_plan: PublicationPlan,
        *,
        chunk_size: int,
    ) -> ArtifactStatus:
        del publication_plan, chunk_size
        raise OrphanArtifactError("orphan")

    monkeypatch.setattr(publication, "_validate_publication", orphan_validation)
    with pytest.raises(OrphanArtifactError, match="orphan"):
        publication._final_validation(plan, chunk_size=7)

    parent = filesystem._pin_owned_directory(tmp_path)
    staged = filesystem._create_staged_directory(parent, "artifact")
    try:
        monkeypatch.setattr(
            filesystem,
            "_revalidate_pinned_directory_binding",
            lambda directory: (_ for _ in ()).throw(OSError(errno.ESTALE, "changed")),
        )
        with pytest.raises(ArtifactValidationError, match="immediately before"):
            publication._publish_package_artifact(plan, parent, staged)
    finally:
        close_error = filesystem._close_staged_directory(staged)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_filesystem_rejects_invalid_paths_directories_and_missing_renameat2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ArtifactStatusSecurityError):
        filesystem._validate_attempt_path(object())
    regular = tmp_path / "regular"
    regular.write_bytes(b"regular")
    with pytest.raises(ArtifactStatusSecurityError):
        filesystem._require_directory(regular.stat())
    monkeypatch.setattr(filesystem, "_renameat2_symbol", None)
    with pytest.raises(OSError) as caught:
        filesystem._renameat2_call(1, b"source", 1, b"destination", 1)
    assert caught.value.errno == errno.ENOSYS


@pytest.mark.integration
def test_filesystem_staging_collisions_and_replacements_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_name = "collision"
    (tmp_path / f".trafficlab-staging-{staging_name}").mkdir(mode=0o700)
    parent = filesystem._pin_owned_directory(tmp_path)
    try:
        monkeypatch.setattr(filesystem, "_publication_token", lambda: staging_name)
        monkeypatch.setattr(filesystem, "_MAX_STAGING_COLLISIONS", 1)
        with pytest.raises(OSError, match="names collided"):
            filesystem._create_staged_file(parent, "artifact.bin")
        with pytest.raises(OSError, match="names collided"):
            filesystem._create_staged_directory(parent, "artifact")
    finally:
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.integration
def test_filesystem_revalidates_replaced_package_directories_and_members(
    tmp_path: Path,
) -> None:
    parent = filesystem._pin_owned_directory(tmp_path)
    staged = filesystem._create_staged_directory(parent, "artifact")
    try:
        filesystem._create_package_directories(staged, ("nested/member.bin",))
        package_file, handle = filesystem._create_package_file(
            staged, "nested/member.bin"
        )
        handle.close()

        replacement = staged.path / "nested" / "replacement.bin"
        _write_private(replacement, b"replacement")
        os.replace(replacement, package_file.path)
        with pytest.raises(OSError, match="package member binding changed"):
            filesystem._revalidate_package_file(package_file)

        os.rename(staged.path, tmp_path / "moved-staging")
        staged.path.mkdir(mode=0o700)
        with pytest.raises(OSError, match="staged package root changed"):
            filesystem._revalidate_staged_directory(staged)
    finally:
        close_error = filesystem._close_staged_directory(staged)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.integration
def test_filesystem_package_tree_and_pinned_source_guards(
    tmp_path: Path,
) -> None:
    parent = filesystem._pin_owned_directory(tmp_path)
    staged = filesystem._create_staged_directory(parent, "artifact")
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    attempt_pin = filesystem._pin_attempt_directory(attempt)
    (attempt / "directory-source").mkdir(mode=0o700)
    try:
        _write_private(staged.path / "untracked.bin", b"untracked")
        with pytest.raises(OSError, match="planned-file membership"):
            filesystem._validate_package_tree(staged, ("untracked.bin",))
        with pytest.raises(OSError, match="not a regular file"):
            filesystem._open_pinned_file(
                attempt_pin,
                "directory-source",
                attempt / "directory-source",
            )
    finally:
        close_error = filesystem._close_staged_directory(staged)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(attempt_pin)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_filesystem_status_snapshot_and_cleanup_failure_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = filesystem._pin_owned_directory(tmp_path)
    staged, handle = filesystem._create_staged_file(parent, "status.bin")
    assert handle.write(b"status") == len(b"status")
    handle.close()
    try:
        original_revalidate = filesystem._revalidate_staged_file
        monkeypatch.setattr(filesystem, "_revalidate_staged_file", lambda value: None)
        replacement = tmp_path / "replacement-status.bin"
        _write_private(replacement, b"replacement")
        os.replace(replacement, staged.path)
        with pytest.raises(OSError, match="staged status binding changed"):
            filesystem._snapshot_staged_status(staged, max_bytes=16, chunk_size=3)
        monkeypatch.setattr(filesystem, "_revalidate_staged_file", original_revalidate)

        descriptor = os.open(staged.path, os.O_RDONLY)
        entry = filesystem._PinnedEntry(
            None, "status.bin", descriptor, os.fstat(descriptor)
        )
        try:
            monkeypatch.setattr(
                filesystem,
                "_read_fd",
                lambda value, count: b"x" * (count + 1),
            )
            with pytest.raises(ArtifactStatusSecurityError) as caught:
                filesystem._read_bounded_status(entry, 3)
            assert caught.value.__cause__ is not None
            assert "invalid result" in str(caught.value.__cause__)
        finally:
            os.close(descriptor)

        first = OSError(errno.EIO, "first close failure")
        second = OSError(errno.EIO, "second close failure")
        failures = iter((first, second))
        monkeypatch.setattr(
            filesystem,
            "_close_fd",
            lambda descriptor: (_ for _ in ()).throw(next(failures)),
        )
        assert filesystem._close_all([1, 2]) is first
    finally:
        monkeypatch.setattr(filesystem, "_close_fd", os.close)
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_copy_launch_and_manifest_revalidation_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    attempt = filesystem._pin_attempt_directory(fixture.plan.attempt_dir)
    parent = filesystem._pin_owned_directory(tmp_path)
    staged = filesystem._create_staged_directory(parent, "artifact")
    try:
        monkeypatch.setattr(
            publication,
            "_create_package_member",
            lambda root, path: (_ for _ in ()).throw(RuntimeError("member failure")),
        )
        with pytest.raises(RuntimeError, match="member failure"):
            publication._copy_package_launch(
                staged,
                attempt,
                fixture.launch,
                chunk_size=7,
            )
        monkeypatch.undo()

        monkeypatch.setattr(
            filesystem,
            "_revalidate_package_file",
            lambda package_file: (_ for _ in ()).throw(
                OSError(errno.ESTALE, "changed")
            ),
        )
        with pytest.raises(ArtifactValidationError, match="manifest failed lineage"):
            publication._stage_package_manifest(
                staged,
                (),
                lambda members: b"{}",
                max_manifest_bytes=16,
                chunk_size=7,
            )
    finally:
        close_error = filesystem._close_staged_directory(staged)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(attempt)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_staged_status_must_parse_to_its_exact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    attempt = filesystem._pin_attempt_directory(fixture.plan.attempt_dir)
    status = ArtifactStatus(
        1,
        "published",
        ArtifactKind.PACKAGE,
        str(fixture.plan.artifact_path),
        str(fixture.plan.artifact_path / "manifest.json"),
        Sha256Digest("0" * 64),
        str(fixture.plan.launch_path),
        fixture.launch.sha256,
    )
    different = ArtifactStatus(
        1,
        "published",
        ArtifactKind.PACKAGE,
        str(fixture.plan.artifact_path),
        str(fixture.plan.artifact_path / "manifest.json"),
        Sha256Digest("1" * 64),
        str(fixture.plan.launch_path),
        fixture.launch.sha256,
    )
    try:
        monkeypatch.setattr(
            publication, "_parse_artifact_status", lambda data: different
        )
        with pytest.raises(ArtifactValidationError, match="staged detached status"):
            publication._stage_status(fixture.plan, attempt, status, chunk_size=7)
    finally:
        close_error = filesystem._close_pinned_directory(attempt)
        assert close_error is None


@pytest.mark.unit
def test_filesystem_handles_empty_path_components_and_descriptorless_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[int] = []
    try:
        with pytest.raises(ArtifactStatusSecurityError):
            filesystem._open_attempt_chain("//", opened)
    finally:
        filesystem._close_all(opened)
    with pytest.raises(OSError, match="publication parent owner"):
        filesystem._pin_owned_directory(Path("//"))

    writer = filesystem._UnbufferedWriter.__new__(filesystem._UnbufferedWriter)
    writer._descriptor = None
    writer.close()
    assert writer.closed


@pytest.mark.integration
def test_filesystem_detects_private_staging_binding_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement = tmp_path / "replacement.bin"
    _write_private(replacement, b"replacement")
    parent = filesystem._pin_owned_directory(tmp_path)
    original_stat = filesystem._stat_entry
    try:

        def substituted_stat(name: str, *, dir_fd: int | None = None) -> os.stat_result:
            if name.startswith(".trafficlab-staging-"):
                return replacement.stat()
            return original_stat(name, dir_fd=dir_fd)

        monkeypatch.setattr(filesystem, "_stat_entry", substituted_stat)
        monkeypatch.setattr(filesystem, "_publication_token", lambda: "substitute")
        with pytest.raises(ArtifactWriteError, match="private publication staging"):
            filesystem._create_staged_file(parent, "artifact.bin")
    finally:
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.integration
def test_filesystem_detects_pinned_and_nested_directory_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = tmp_path / "child"
    child.mkdir(mode=0o700)
    pinned = filesystem._pin_owned_directory(child)
    try:
        os.rename(child, tmp_path / "moved-child")
        child.mkdir(mode=0o700)
        with pytest.raises(OSError, match="pinned package directory changed"):
            filesystem._revalidate_pinned_directory_binding(pinned)
    finally:
        close_error = filesystem._close_pinned_directory(pinned)
        assert close_error is None

    parent = filesystem._pin_owned_directory(tmp_path)
    staged = filesystem._create_staged_directory(parent, "artifact")
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    original_stat = filesystem._stat_entry
    try:

        def substituted_nested_stat(
            name: str,
            *,
            dir_fd: int | None = None,
        ) -> os.stat_result:
            if name == "nested":
                return other.stat()
            return original_stat(name, dir_fd=dir_fd)

        monkeypatch.setattr(filesystem, "_stat_entry", substituted_nested_stat)
        with pytest.raises(OSError, match="nested package binding changed"):
            filesystem._create_package_directories(staged, ("nested/member.bin",))
    finally:
        close_error = filesystem._close_staged_directory(staged)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.integration
def test_filesystem_rejects_root_and_member_binding_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_replacement = tmp_path / "root-replacement"
    root_replacement.mkdir(mode=0o700)
    file_replacement = tmp_path / "file-replacement"
    _write_private(file_replacement, b"replacement")
    parent = filesystem._pin_owned_directory(tmp_path)
    try:
        monkeypatch.setattr(filesystem, "_publication_token", lambda: "same")
        monkeypatch.setattr(filesystem, "_MAX_STAGING_COLLISIONS", 1)
        with pytest.raises(OSError, match="names collided"):
            filesystem._create_staged_directory(parent, ".trafficlab-staging-same")

        original_stat = filesystem._stat_entry

        def substituted_root_stat(
            name: str, *, dir_fd: int | None = None
        ) -> os.stat_result:
            if name == ".trafficlab-staging-same":
                return root_replacement.stat()
            return original_stat(name, dir_fd=dir_fd)

        monkeypatch.setattr(filesystem, "_stat_entry", substituted_root_stat)
        with pytest.raises(ArtifactWriteError, match="private package staging"):
            filesystem._create_staged_directory(parent, "artifact")
        monkeypatch.undo()
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None
        parent = filesystem._pin_owned_directory(tmp_path)

        staged = filesystem._create_staged_directory(parent, "artifact")
        try:
            original_stat = filesystem._stat_entry

            def substituted_member_stat(
                name: str,
                *,
                dir_fd: int | None = None,
            ) -> os.stat_result:
                if name == "member.bin":
                    return file_replacement.stat()
                return original_stat(name, dir_fd=dir_fd)

            monkeypatch.setattr(filesystem, "_stat_entry", substituted_member_stat)
            with pytest.raises(ArtifactWriteError, match="private package member"):
                filesystem._create_package_file(staged, "member.bin")
        finally:
            monkeypatch.undo()
            close_error = filesystem._close_staged_directory(staged)
            assert close_error is None
    finally:
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.integration
def test_filesystem_rejects_replaced_tree_member_source_and_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = filesystem._pin_owned_directory(tmp_path)
    staged = filesystem._create_staged_directory(parent, "artifact")
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    source = attempt / "source.bin"
    _write_private(source, b"source")
    alternate = attempt / "alternate.bin"
    _write_private(alternate, b"alternate")
    attempt_pin = filesystem._pin_attempt_directory(attempt)
    try:
        package_file, handle = filesystem._create_package_file(staged, "member.bin")
        handle.close()
        replacement = staged.path / "replacement.bin"
        _write_private(replacement, b"replacement")
        os.replace(replacement, package_file.path)
        with pytest.raises(OSError, match="package member binding changed"):
            filesystem._validate_package_tree(staged, ("member.bin",))

        original_stat = filesystem._stat_entry

        def substituted_source_stat(
            name: str,
            *,
            dir_fd: int | None = None,
        ) -> os.stat_result:
            if name == "source.bin":
                return alternate.stat()
            return original_stat(name, dir_fd=dir_fd)

        monkeypatch.setattr(filesystem, "_stat_entry", substituted_source_stat)
        with pytest.raises(OSError, match="package copy source binding changed"):
            filesystem._open_pinned_file(attempt_pin, "source.bin", source)
        monkeypatch.undo()
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None
        parent = filesystem._pin_owned_directory(tmp_path)

        status, handle = filesystem._create_staged_file(parent, "status.bin")
        assert handle.write(b"status") == len(b"status")
        handle.close()
        original_read = filesystem._read_fd
        changed = False

        def mutate_status_during_read(descriptor: int, count: int) -> bytes:
            nonlocal changed
            chunk = original_read(descriptor, count)
            if not changed:
                changed = True
                status.path.write_bytes(b"changed")
            return chunk

        monkeypatch.setattr(filesystem, "_read_fd", mutate_status_during_read)
        with pytest.raises(OSError, match="staged status changed during validation"):
            filesystem._snapshot_staged_status(status, max_bytes=16, chunk_size=3)
    finally:
        monkeypatch.undo()
        close_error = filesystem._close_staged_directory(staged)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(attempt_pin)
        assert close_error is None
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.integration
def test_package_revalidation_rejects_member_and_manifest_identity_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    changed = FileIdentity(PathKind.LOCAL, "alpha.bin", Sha256Digest("0" * 64))
    original_validate = publication._validate_local_file

    def changed_member_identity(
        root: Path,
        identity: FileIdentity,
        *,
        chunk_size: int,
    ) -> FileIdentity:
        if identity.path == "alpha.bin":
            return changed
        return original_validate(root, identity, chunk_size=chunk_size)

    monkeypatch.setattr(publication, "_validate_local_file", changed_member_identity)
    with pytest.raises(ArtifactValidationError, match="member digest changed"):
        _publish_fixture(fixture)
    monkeypatch.undo()

    manifest_tmp_path = tmp_path / "manifest"
    manifest_tmp_path.mkdir()
    fixture = _package_fixture(manifest_tmp_path)
    original_validate = publication._validate_local_file

    def changed_manifest_identity(
        root: Path,
        identity: FileIdentity,
        *,
        chunk_size: int,
    ) -> FileIdentity:
        if identity.path == "manifest.json":
            return FileIdentity(PathKind.LOCAL, "manifest.json", Sha256Digest("0" * 64))
        return original_validate(root, identity, chunk_size=chunk_size)

    monkeypatch.setattr(publication, "_validate_local_file", changed_manifest_identity)
    with pytest.raises(ArtifactValidationError, match="published package manifest"):
        _publish_fixture(fixture)


@pytest.mark.unit
def test_package_publication_rejects_unsafe_initial_filesystem_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    monkeypatch.setattr(
        filesystem,
        "_pin_attempt_directory",
        lambda path: (_ for _ in ()).throw(OSError(errno.EACCES, "denied")),
    )
    with pytest.raises(ArtifactValidationError, match="filesystem boundary"):
        _publish_fixture(fixture)


@pytest.mark.integration
def test_file_publication_validation_rejects_changed_published_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    plan = build_file_plan(attempt, tmp_path / "artifact.bin")
    parent = filesystem._pin_owned_directory(tmp_path)
    staged, handle = filesystem._create_staged_file(parent, plan.artifact_path.name)
    assert handle.write(b"artifact") == len(b"artifact")
    handle.close()
    try:
        os.rename(staged.path, plan.artifact_path)
        staged_identity = FileIdentity(
            PathKind.EXTERNAL,
            str(staged.path),
            Sha256Digest("1" * 64),
        )
        changed = FileIdentity(
            PathKind.EXTERNAL,
            str(plan.artifact_path),
            Sha256Digest("0" * 64),
        )
        monkeypatch.setattr(
            filesystem, "_revalidate_pinned_directory", lambda root: None
        )
        monkeypatch.setattr(
            publication,
            "_validate_external_file",
            lambda identity, *, chunk_size: changed,
        )
        with pytest.raises(ArtifactValidationError, match="published artifact failed"):
            publication._validate_published_artifact(
                plan,
                parent,
                staged,
                staged_identity,
                chunk_size=7,
            )
    finally:
        close_error = filesystem._close_pinned_directory(parent)
        assert close_error is None


@pytest.mark.unit
def test_filesystem_loads_without_renameat2_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    original_cdll = filesystem.ctypes.CDLL

    class _NoRenameAt2:
        pass

    monkeypatch.setattr(
        filesystem.ctypes, "CDLL", lambda *args, **kwargs: _NoRenameAt2()
    )
    importlib.reload(filesystem)
    assert filesystem._renameat2_symbol is None
    monkeypatch.setattr(filesystem.ctypes, "CDLL", original_cdll)
    importlib.reload(filesystem)


@pytest.mark.integration
def test_package_artifact_race_preserves_racer_and_staging_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    plan = fixture.plan
    original_rename = filesystem._atomic_rename_noreplace

    def racing_artifact_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_path: Path,
        orphan_path: Path | None = None,
    ) -> None:
        if destination_name == plan.artifact_path.name:
            plan.artifact_path.mkdir(mode=0o700)
            _write_private(plan.artifact_path / "racer.bin", b"racing package")
        original_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            source_path=source_path,
            orphan_path=orphan_path,
        )

    monkeypatch.setattr(filesystem, "_atomic_rename_noreplace", racing_artifact_rename)

    with pytest.raises(PublicationConflictError) as caught:
        _publish_fixture(fixture)

    retained = _retained_package(caught.value)
    assert (retained / "manifest.json").is_file()
    assert (plan.artifact_path / "racer.bin").read_bytes() == b"racing package"
    assert not plan.status_path.exists()
    assert caught.value.orphan_path == plan.artifact_path


@pytest.mark.integration
def test_package_status_race_preserves_racer_and_orphan_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _package_fixture(tmp_path)
    plan = fixture.plan
    original_rename = filesystem._atomic_rename_noreplace

    def racing_status_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_path: Path,
        orphan_path: Path | None = None,
    ) -> None:
        if destination_name == plan.status_path.name:
            _write_private(plan.status_path, b"racing status")
        original_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            source_path=source_path,
            orphan_path=orphan_path,
        )

    monkeypatch.setattr(filesystem, "_atomic_rename_noreplace", racing_status_rename)

    with pytest.raises(PublicationConflictError) as caught:
        _publish_fixture(fixture)

    assert caught.value.orphan_path == plan.artifact_path
    assert plan.artifact_path.is_dir()
    assert plan.status_path.read_bytes() == b"racing status"
    assert len(caught.value.retained_paths) == 1
    assert caught.value.retained_paths[0].is_file()


@pytest.mark.integration
@pytest.mark.parametrize("target", ["member", "manifest", "launch"])
def test_package_publication_rejects_post_rename_content_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    fixture = _package_fixture(tmp_path)
    plan = fixture.plan
    original_rename = filesystem._atomic_rename_noreplace

    def mutating_artifact_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_path: Path,
        orphan_path: Path | None = None,
    ) -> None:
        original_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
            source_path=source_path,
            orphan_path=orphan_path,
        )
        if destination_name == plan.artifact_path.name:
            relative = {
                "member": "alpha.bin",
                "manifest": "manifest.json",
                "launch": "launch.toml",
            }[target]
            (plan.artifact_path / relative).write_bytes(b"mutated after rename")

    monkeypatch.setattr(
        filesystem, "_atomic_rename_noreplace", mutating_artifact_rename
    )

    with pytest.raises(ArtifactValidationError) as caught:
        _publish_fixture(fixture)

    assert caught.value.orphan_path == plan.artifact_path
    assert caught.value.retained_paths == ()
    assert plan.artifact_path.is_dir()
    assert not plan.status_path.exists()
