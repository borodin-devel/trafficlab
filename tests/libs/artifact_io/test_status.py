import tomllib
from dataclasses import FrozenInstanceError

import pytest

from trafficlab.libs.artifact_io import (
    CURRENT_ARTIFACT_STATUS_VERSION,
    MAX_ARTIFACT_STATUS_BYTES,
    ArtifactKind,
    ArtifactStatus,
    InvalidArtifactStatusError,
    parse_artifact_status,
    render_artifact_status,
)
from trafficlab.libs.lineage import Sha256Digest

ZERO = "0123456789abcdef" * 4
ONE = "fedcba9876543210" * 4

PACKAGE_GOLDEN = (
    b"schema_version = 1\n"
    b'state = "published"\n'
    b'artifact_kind = "package"\n'
    b'artifact_path = "/absolute/attempt/artifact"\n'
    b'digest_path = "/absolute/attempt/artifact/manifest.json"\n'
    b'sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"\n'
    b'launch_path = "/absolute/attempt/launch.toml"\n'
    b'launch_sha256 = "fedcba9876543210fedcba9876543210'
    b'fedcba9876543210fedcba9876543210"\n'
)


def _package_status() -> ArtifactStatus:
    return ArtifactStatus(
        schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
        state="published",
        artifact_kind=ArtifactKind.PACKAGE,
        artifact_path="/absolute/attempt/artifact",
        digest_path="/absolute/attempt/artifact/manifest.json",
        sha256=Sha256Digest(ZERO),
        launch_path="/absolute/attempt/launch.toml",
        launch_sha256=Sha256Digest(ONE),
    )


def _file_status(
    *, artifact_path: str = "/absolute/output/capture.pcapng"
) -> ArtifactStatus:
    return ArtifactStatus(
        schema_version=CURRENT_ARTIFACT_STATUS_VERSION,
        state="published",
        artifact_kind=ArtifactKind.FILE,
        artifact_path=artifact_path,
        digest_path=artifact_path,
        sha256=Sha256Digest(ZERO),
        launch_path="/absolute/attempt/launch.toml",
        launch_sha256=Sha256Digest(ONE),
    )


@pytest.mark.unit
def test_package_status_has_exact_golden_bytes_and_terminal_lf() -> None:
    rendered = render_artifact_status(_package_status())

    assert rendered == PACKAGE_GOLDEN
    assert rendered.endswith(b"\n")
    assert b"\r" not in rendered
    assert not rendered.startswith(b"\xef\xbb\xbf")
    assert parse_artifact_status(rendered) == _package_status()


@pytest.mark.unit
def test_file_status_round_trips_with_identical_canonical_bytes() -> None:
    status = _file_status()
    rendered = render_artifact_status(status)

    assert b'artifact_kind = "file"\n' in rendered
    assert rendered.count(b'/absolute/output/capture.pcapng"\n') == 2
    assert render_artifact_status(parse_artifact_status(rendered)) == rendered


@pytest.mark.unit
def test_status_toml_escaping_is_deterministic_and_tomllib_compatible() -> None:
    path = '/absolute/quote"/back\\slash/\u96ea\U0001f600file'
    rendered = render_artifact_status(_file_status(artifact_path=path))
    loaded = tomllib.loads(rendered.decode("utf-8"))

    assert loaded["artifact_path"] == path
    assert loaded["digest_path"] == path
    assert b'quote\\"' in rendered
    assert b"back\\\\slash" in rendered
    assert b"\\u96EA" in rendered
    assert b"\\U0001F600" in rendered
    assert render_artifact_status(parse_artifact_status(rendered)) == rendered


@pytest.mark.unit
def test_status_rejects_unicode_line_separator_with_sanitized_diagnostic() -> None:
    separator = "\u2028"

    with pytest.raises(InvalidArtifactStatusError) as caught:
        _file_status(artifact_path=f"/absolute/before{separator}after")

    message = str(caught.value)
    assert message.splitlines() == [message]
    assert separator not in message


@pytest.mark.unit
def test_status_values_are_frozen_and_slotted() -> None:
    status = _package_status()

    assert not hasattr(status, "__dict__")
    with pytest.raises(FrozenInstanceError):
        status.state = "other"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("schema_version", True, id="boolean-version"),
        pytest.param("schema_version", 2, id="unsupported-version"),
        pytest.param("schema_version", "1", id="string-version"),
        pytest.param("state", "failed", id="unsupported-state"),
        pytest.param("state", 1, id="non-string-state"),
        pytest.param("artifact_kind", "file", id="string-kind"),
        pytest.param("sha256", ZERO, id="string-sha256"),
        pytest.param("launch_sha256", ONE, id="string-launch-sha256"),
    ),
)
def test_direct_status_rejects_wrong_runtime_or_fixed_fields(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "schema_version": 1,
        "state": "published",
        "artifact_kind": ArtifactKind.FILE,
        "artifact_path": "/absolute/artifact",
        "digest_path": "/absolute/artifact",
        "sha256": Sha256Digest(ZERO),
        "launch_path": "/absolute/launch.toml",
        "launch_sha256": Sha256Digest(ONE),
    }
    values[field] = value

    with pytest.raises(InvalidArtifactStatusError):
        ArtifactStatus(**values)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "path"),
    (
        pytest.param("artifact_path", "", id="empty-artifact"),
        pytest.param("artifact_path", "relative", id="relative-artifact"),
        pytest.param("artifact_path", "//ambiguous", id="double-root-artifact"),
        pytest.param("artifact_path", "/path/../escape", id="dotdot-artifact"),
        pytest.param("artifact_path", "/path/./file", id="dot-artifact"),
        pytest.param("artifact_path", "/path//file", id="repeated-artifact"),
        pytest.param("artifact_path", "/path/file/", id="trailing-artifact"),
        pytest.param("artifact_path", "/bad\npath", id="control-artifact"),
        pytest.param("digest_path", "relative", id="relative-digest"),
        pytest.param("launch_path", "/bad\x7fpath", id="control-launch"),
        pytest.param("launch_path", 7, id="non-string-launch"),
    ),
)
def test_direct_status_rejects_noncanonical_paths(field: str, path: object) -> None:
    values: dict[str, object] = {
        "schema_version": 1,
        "state": "published",
        "artifact_kind": ArtifactKind.FILE,
        "artifact_path": "/absolute/artifact",
        "digest_path": "/absolute/artifact",
        "sha256": Sha256Digest(ZERO),
        "launch_path": "/absolute/launch.toml",
        "launch_sha256": Sha256Digest(ONE),
    }
    values[field] = path
    if field == "artifact_path":
        values["digest_path"] = path

    with pytest.raises(InvalidArtifactStatusError):
        ArtifactStatus(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_package_digest_must_be_direct_manifest_child() -> None:
    for digest_path in (
        "/absolute/attempt/manifest.json",
        "/absolute/attempt/artifact/nested/manifest.json",
        "/absolute/attempt/artifact/other.json",
    ):
        with pytest.raises(InvalidArtifactStatusError):
            ArtifactStatus(
                1,
                "published",
                ArtifactKind.PACKAGE,
                "/absolute/attempt/artifact",
                digest_path,
                Sha256Digest(ZERO),
                "/absolute/attempt/launch.toml",
                Sha256Digest(ONE),
            )


@pytest.mark.unit
def test_file_digest_path_must_equal_artifact_path() -> None:
    with pytest.raises(InvalidArtifactStatusError):
        ArtifactStatus(
            1,
            "published",
            ArtifactKind.FILE,
            "/absolute/artifact",
            "/absolute/other",
            Sha256Digest(ZERO),
            "/absolute/launch.toml",
            Sha256Digest(ONE),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param("not bytes", id="string"),
        pytest.param(bytearray(PACKAGE_GOLDEN), id="bytearray"),
        pytest.param(memoryview(PACKAGE_GOLDEN), id="memoryview"),
    ),
)
def test_parser_requires_bytes(data: object) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param(b"", id="empty"),
        pytest.param(b" " * (MAX_ARTIFACT_STATUS_BYTES + 1), id="oversized"),
        pytest.param(b"\xef\xbb\xbf" + PACKAGE_GOLDEN, id="bom"),
        pytest.param(PACKAGE_GOLDEN.replace(b"\n", b"\r\n"), id="crlf"),
        pytest.param(PACKAGE_GOLDEN + b"\x00", id="nul"),
        pytest.param(b"schema_version = \xff\n", id="invalid-utf8"),
        pytest.param(b"schema_version = [\n", id="invalid-toml"),
    ),
)
def test_parser_rejects_unsafe_or_invalid_byte_envelopes(data: bytes) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param(PACKAGE_GOLDEN + b'unknown = "value"\n', id="unknown-key"),
        pytest.param(
            b"\n".join(PACKAGE_GOLDEN.splitlines()[:-1]) + b"\n",
            id="missing-key",
        ),
        pytest.param(PACKAGE_GOLDEN + b"schema_version = 1\n", id="duplicate-key"),
        pytest.param(b"[nested]\n" + PACKAGE_GOLDEN, id="nested-table"),
        pytest.param(
            b"\n".join(
                (
                    PACKAGE_GOLDEN.splitlines()[1],
                    PACKAGE_GOLDEN.splitlines()[0],
                    *PACKAGE_GOLDEN.splitlines()[2:],
                )
            )
            + b"\n",
            id="reordered-keys",
        ),
    ),
)
def test_parser_rejects_unknown_missing_duplicate_nested_or_reordered_keys(
    data: bytes,
) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("old", "new"),
    (
        pytest.param(
            b"schema_version = 1", b"schema_version = true", id="bool-version"
        ),
        pytest.param(
            b"schema_version = 1", b"schema_version = 1.0", id="float-version"
        ),
        pytest.param(
            b"schema_version = 1", b'schema_version = "1"', id="string-version"
        ),
        pytest.param(
            b"schema_version = 1", b"schema_version = 2", id="unsupported-version"
        ),
        pytest.param(b'state = "published"', b'state = "failed"', id="state"),
        pytest.param(
            b'artifact_kind = "package"', b'artifact_kind = "directory"', id="kind"
        ),
        pytest.param(
            b'sha256 = "' + ZERO.encode() + b'"', b"sha256 = 7", id="digest-type"
        ),
        pytest.param(
            b'sha256 = "' + ZERO.encode() + b'"',
            b'sha256 = "' + ("A" * 64).encode() + b'"',
            id="digest-spelling",
        ),
    ),
)
def test_parser_rejects_wrong_field_types_or_fixed_values(
    old: bytes, new: bytes
) -> None:
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(PACKAGE_GOLDEN.replace(old, new))


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    (
        pytest.param(
            PACKAGE_GOLDEN.replace(b"schema_version = 1", b"schema_version=1"),
            id="whitespace",
        ),
        pytest.param(
            PACKAGE_GOLDEN.replace(b'state = "published"', b"state = 'published'"),
            id="literal-quote",
        ),
        pytest.param(
            PACKAGE_GOLDEN.replace(
                b'state = "published"', b'state = "p\\u0075blished"'
            ),
            id="equivalent-escape",
        ),
        pytest.param(PACKAGE_GOLDEN[:-1], id="missing-terminal-lf"),
        pytest.param(PACKAGE_GOLDEN + b"# comment\n", id="comment"),
    ),
)
def test_parser_rejects_toml_equivalent_noncanonical_bytes(data: bytes) -> None:
    assert tomllib.loads(data.decode("utf-8"))["state"] == "published"
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(data)


@pytest.mark.unit
def test_parser_rejects_noncanonical_path_and_digest_relationships() -> None:
    relative = PACKAGE_GOLDEN.replace(
        b'artifact_path = "/absolute/attempt/artifact"',
        b'artifact_path = "relative"',
    )
    wrong_manifest = PACKAGE_GOLDEN.replace(
        b'digest_path = "/absolute/attempt/artifact/manifest.json"',
        b'digest_path = "/absolute/attempt/artifact/other.json"',
    )

    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(relative)
    with pytest.raises(InvalidArtifactStatusError):
        parse_artifact_status(wrong_manifest)


@pytest.mark.unit
def test_renderer_rejects_non_status_and_oversized_output() -> None:
    with pytest.raises(InvalidArtifactStatusError):
        render_artifact_status(object())  # type: ignore[arg-type]

    huge_path = "/" + "a" * MAX_ARTIFACT_STATUS_BYTES
    with pytest.raises(InvalidArtifactStatusError):
        render_artifact_status(_file_status(artifact_path=huge_path))


@pytest.mark.unit
def test_status_errors_are_sanitized_single_line_and_do_not_repeat_input() -> None:
    secret = b"DO_NOT_REPEAT_THIS_RAW_STATUS"
    malformed = PACKAGE_GOLDEN + b'unknown = "' + secret + b'"\n'

    with pytest.raises(InvalidArtifactStatusError) as caught:
        parse_artifact_status(malformed)

    message = str(caught.value)
    assert message.splitlines() == [message]
    assert secret.decode() not in message


@pytest.mark.unit
def test_status_rejects_unencodable_path_without_repeating_it() -> None:
    unencodable = "\udcff"

    with pytest.raises(InvalidArtifactStatusError) as caught:
        _file_status(artifact_path=f"/absolute/{unencodable}")

    message = str(caught.value)
    assert message.splitlines() == [message]
    assert unencodable not in message
