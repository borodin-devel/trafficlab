import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import (
    CaptureMetadata,
    deterministic_peer_mac,
    load_capture_metadata,
    parse_capture_metadata,
    render_capture_metadata,
)


class CaptureMetadataWithGeneration(CaptureMetadata):
    """A test-only extension exposing inherited model-wide scalar strictness."""

    generation: int


class CaptureMetadataWithLabel(CaptureMetadata):
    """A valid extension that rendering must not publish."""

    label: str


def test_capture_metadata_loads_exact_fields_and_normalizes_target_mac(tmp_path: Path) -> None:
    """Losing the canonical MAC form would misclassify Ethernet frame directions."""
    path = tmp_path / "capture.json"
    path.write_text('{"interface":"eth0","target_mac":"02:42:AC:11:00:02"}', encoding="utf-8")

    metadata = load_capture_metadata(path)

    assert metadata == CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")


def test_capture_metadata_parses_exact_in_memory_bytes_with_source_context(tmp_path: Path) -> None:
    """Forcing comparison to reopen a path would separate evaluated metadata from its byte identity."""
    source = tmp_path / "capture.json"
    content = b'{"interface":"eth0","target_mac":"02:42:AC:11:00:02"}'

    metadata = parse_capture_metadata(content, source=source)

    assert metadata == CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")


def test_capture_metadata_byte_parser_reports_its_source() -> None:
    """An in-memory parse error must still identify the artifact that supplied the bytes."""
    source = Path("run/capture.json")

    with pytest.raises(TrafficlabError, match=r"run/capture\.json.*not valid UTF-8"):
        parse_capture_metadata(b"\xff", source=source)


def test_render_capture_metadata_emits_the_exact_two_field_document() -> None:
    """Extra rendered metadata would create an undocumented artifact contract."""
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")

    rendered = render_capture_metadata(metadata)

    assert rendered == b'{\n  "interface": "eth0",\n  "target_mac": "02:42:ac:11:00:02"\n}\n'
    assert json.loads(rendered) == {"interface": "eth0", "target_mac": "02:42:ac:11:00:02"}


def test_capture_metadata_model_is_strict_for_declared_subclass_scalars() -> None:
    """Removing global strict mode would silently coerce future metadata scalar fields."""
    with pytest.raises(ValidationError, match="generation"):
        CaptureMetadataWithGeneration(
            interface="eth0",
            target_mac="02:42:ac:11:00:02",
            generation="1",  # type: ignore[arg-type]
        )


def test_render_capture_metadata_omits_declared_subclass_fields() -> None:
    """Publishing a valid extension field would violate the exact artifact schema."""
    metadata = CaptureMetadataWithLabel(
        interface="eth0",
        target_mac="02:42:ac:11:00:02",
        label="not-for-capture-json",
    )

    rendered = render_capture_metadata(metadata)

    assert rendered == b'{\n  "interface": "eth0",\n  "target_mac": "02:42:ac:11:00:02"\n}\n'
    assert json.loads(rendered) == {"interface": "eth0", "target_mac": "02:42:ac:11:00:02"}


@pytest.mark.parametrize(
    "document",
    [
        {"interface": "eth0"},
        {"target_mac": "02:42:ac:11:00:02"},
        {"interface": "eth0", "target_mac": "02:42:ac:11:00:02", "extra": "forbidden"},
    ],
    ids=["missing-target-mac", "missing-interface", "unknown-field"],
)
def test_invalid_capture_metadata_fields_are_translated_to_trafficlab_errors(
    tmp_path: Path, document: dict[str, str]
) -> None:
    """Leaking model errors would leave capture users without a corrective action."""
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(TrafficlabError, match="invalid capture metadata") as error:
        load_capture_metadata(path)

    assert error.value.corrective_action == "correct capture.json and retry"


def test_capture_metadata_requires_the_literal_eth0_interface() -> None:
    """A different interface would invalidate the Docker capture topology assumption."""
    with pytest.raises(ValidationError, match="eth0"):
        CaptureMetadata(interface="ens3", target_mac="02:42:ac:11:00:02")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "target_mac",
    [
        "02:42:ac:11:00",
        "02:42:ac:11:00:02:03",
        "02-42-ac-11-00-02",
        "02:42:ac:11:00:gg",
        "0242:ac:11:00:02",
    ],
)
def test_capture_metadata_rejects_malformed_target_macs(target_mac: str) -> None:
    """A malformed MAC cannot safely be compared with parsed Ethernet headers."""
    with pytest.raises(ValidationError, match="target_mac"):
        CaptureMetadata(interface="eth0", target_mac=target_mac)


@pytest.mark.parametrize("target_mac", ["00:00:00:00:00:00", "01:42:ac:11:00:02"])
def test_capture_metadata_rejects_zero_and_multicast_target_macs(target_mac: str) -> None:
    """Zero or multicast targets cannot identify one captured endpoint."""
    with pytest.raises(ValidationError, match="target_mac"):
        CaptureMetadata(interface="eth0", target_mac=target_mac)


def test_capture_metadata_is_frozen() -> None:
    """Mutating metadata after parsing could make one artifact use two target MACs."""
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")

    with pytest.raises(ValidationError, match="frozen_instance"):
        metadata.target_mac = "02:42:ac:11:00:03"


@pytest.mark.parametrize(
    ("target_mac", "peer_mac"),
    [
        ("02:42:ac:11:00:02", "02:00:00:00:00:01"),
        ("02:00:00:00:00:01", "02:00:00:00:00:02"),
        ("02:00:00:00:00:01".upper(), "02:00:00:00:00:02"),
    ],
)
def test_deterministic_peer_mac_avoids_the_target_collision(target_mac: str, peer_mac: str) -> None:
    """A peer equal to the target would make generated frame direction ambiguous."""
    assert deterministic_peer_mac(target_mac) == peer_mac


def test_invalid_utf8_capture_metadata_is_translated_to_a_trafficlab_error(tmp_path: Path) -> None:
    """Leaking a decoding error would bypass the package error boundary."""
    path = tmp_path / "capture.json"
    path.write_bytes(b'{"interface":"eth0","target_mac":"\xff"}')

    with pytest.raises(TrafficlabError, match="not valid UTF-8") as error:
        load_capture_metadata(path)

    assert error.value.corrective_action == "save capture.json as valid UTF-8 and retry"


def test_invalid_json_capture_metadata_is_translated_to_a_trafficlab_error(tmp_path: Path) -> None:
    """Leaking a JSON parser error would hide the capture artifact remediation."""
    path = tmp_path / "capture.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="invalid JSON in capture metadata") as error:
        load_capture_metadata(path)

    assert error.value.corrective_action == "correct capture.json JSON and retry"


@pytest.mark.parametrize(
    "content",
    [
        b'{"interface":"eth0","interface":"eth0","target_mac":"02:42:ac:11:00:02"}',
        b'{"interface":"eth0","target_mac":"02:42:ac:11:00:02","unknown":NaN}',
        b'{"interface":"eth0","target_mac":"02:42:ac:11:00:02","unknown":Infinity}',
    ],
    ids=("duplicate-key", "nan-constant", "infinity-constant"),
)
def test_capture_metadata_rejects_ambiguous_json_before_model_validation(content: bytes) -> None:
    """Duplicate keys and nonfinite constants must fail as JSON, never reach Pydantic as trusted structure."""
    source = Path("run/capture.json")

    with pytest.raises(TrafficlabError, match=r"invalid JSON in capture metadata run/capture\.json") as error:
        parse_capture_metadata(content, source=source)

    assert error.value.corrective_action == "correct capture.json JSON and retry"


def test_missing_capture_metadata_is_translated_to_a_trafficlab_error(tmp_path: Path) -> None:
    """Leaking a missing-file error would make absent capture metadata hard to diagnose."""
    with pytest.raises(TrafficlabError, match="could not read capture metadata") as error:
        load_capture_metadata(tmp_path / "capture.json")

    assert error.value.corrective_action == "verify capture.json exists and is readable"
