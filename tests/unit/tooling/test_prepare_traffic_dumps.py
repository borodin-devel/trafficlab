"""Safe external traffic-dump preparation behavior."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import prepare_traffic_dumps as prepare
from tests.support.scapy_fixtures import EncodedEthernetFrame, encode_ethernet_frames, encode_events
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import PcapngPacket
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent

_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_ROOT = Path(__file__).parents[3]
_OTHER = bytes.fromhex("0242ac110003")
_TARGET = bytes.fromhex("0242ac110002")
_PEER = bytes.fromhex("020000000001")


def _ethernet(source: bytes, destination: bytes, ethertype: int = 0x88B5) -> bytes:
    return destination + source + ethertype.to_bytes(2, byteorder="big") + b"payload"


def test_output_path_adds_prefix_and_normalizes_capture_extension(tmp_path: Path) -> None:
    pcap = tmp_path / "legacy.pcap"
    pcapng = tmp_path / "ordered.pcapng"

    assert prepare.output_path(pcap, prefix="trafficlab-ready-") == tmp_path / "trafficlab-ready-legacy.pcapng"
    assert prepare.output_path(pcapng, prefix="trafficlab-ready-") == tmp_path / "trafficlab-ready-ordered.pcapng"


def test_discover_inputs_recurses_sorts_and_ignores_prepared_outputs(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    first = tmp_path / "a.PCAP"
    second = nested / "b.pcapng"
    prepared = nested / "trafficlab-ready-b.pcapng"
    unrelated = tmp_path / "notes.txt"
    for path in (first, second, prepared, unrelated):
        path.write_bytes(b"input")

    assert prepare.discover_inputs((tmp_path,), prefix="trafficlab-ready-") == (first, second)


def test_discover_inputs_rejects_an_explicit_already_prepared_capture(tmp_path: Path) -> None:
    prepared = tmp_path / "trafficlab-ready-capture.pcapng"
    prepared.write_bytes(b"prepared")

    with pytest.raises(ValueError, match="already has output prefix"):
        prepare.discover_inputs((prepared,), prefix="trafficlab-ready-")


@pytest.mark.parametrize("include_valid_capture", [False, True])
def test_discover_inputs_rejects_an_unsupported_explicit_file_even_in_a_mixed_batch(
    tmp_path: Path,
    include_valid_capture: bool,
) -> None:
    unsupported = tmp_path / "capture.pcap.gz"
    unsupported.write_bytes(b"unsupported")
    paths = [unsupported]
    if include_valid_capture:
        capture = tmp_path / "capture.pcapng"
        capture.write_bytes(b"capture")
        paths.append(capture)

    with pytest.raises(ValueError, match="unsupported explicit input"):
        prepare.discover_inputs(tuple(paths), prefix="trafficlab-ready-")


def test_plan_conversions_rejects_an_existing_destination_without_changing_it(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcap"
    destination = tmp_path / "trafficlab-ready-capture.pcapng"
    source.write_bytes(b"source")
    destination.write_bytes(b"existing")

    with pytest.raises(ValueError, match="already exists"):
        prepare.plan_conversions((source,), prefix="trafficlab-ready-")

    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"existing"


def test_plan_conversions_rejects_two_inputs_that_map_to_one_output(tmp_path: Path) -> None:
    pcap = tmp_path / "capture.pcap"
    pcapng = tmp_path / "capture.pcapng"
    pcap.write_bytes(b"pcap")
    pcapng.write_bytes(b"pcapng")

    with pytest.raises(ValueError, match="same output"):
        prepare.plan_conversions((pcap, pcapng), prefix="trafficlab-ready-")


def test_plan_conversions_rejects_relative_and_absolute_aliases_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(b"capture")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="same output"):
        prepare.plan_conversions((Path("capture.pcapng"), source), prefix="trafficlab-ready-")


def test_organized_output_path_uses_the_source_stem_directory_and_filename(tmp_path: Path) -> None:
    source = tmp_path / "nested" / "capture.pcap"
    organized_root = tmp_path / "prepared"

    assert prepare.organized_output_path(source, organized_root=organized_root, prefix="trafficlab-ready-") == (
        organized_root / "capture" / "trafficlab-ready-capture.pcapng"
    )


def test_find_prior_generated_outputs_reports_recursive_prefixed_captures(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    generated = nested / "trafficlab-ready-capture.pcapng"
    generated.write_bytes(b"prepared")

    assert prepare.find_prior_generated_outputs((tmp_path,), prefix="trafficlab-ready-") == (generated,)


def test_plan_organized_conversions_rejects_duplicate_source_stems(tmp_path: Path) -> None:
    first = tmp_path / "first" / "capture.pcap"
    second = tmp_path / "second" / "capture.pcapng"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    with pytest.raises(ValueError, match="duplicate source stem"):
        prepare.plan_organized_conversions(
            (first, second), organized_root=tmp_path / "prepared", prefix="trafficlab-ready-"
        )


def test_plan_organized_conversions_rejects_existing_final_directory(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(b"capture")
    final_directory = tmp_path / "prepared" / "capture"
    final_directory.mkdir(parents=True)

    with pytest.raises(ValueError, match="output already exists"):
        prepare.plan_organized_conversions((source,), organized_root=tmp_path / "prepared", prefix="trafficlab-ready-")


def test_preflight_organized_conversions_rejects_relative_and_absolute_source_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(b"capture")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="path alias"):
        prepare.preflight_organized_conversions(
            (Path("capture.pcapng"), source),
            organized_root=tmp_path / "prepared",
            prefix="trafficlab-ready-",
        )


def test_preflight_organized_conversions_rejects_recursive_generated_outputs(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    generated_directory = tmp_path / "prepared" / "capture"
    generated_directory.mkdir(parents=True)
    (generated_directory / "trafficlab-ready-capture.pcapng").write_bytes(b"prepared")
    source.write_bytes(b"capture")

    with pytest.raises(ValueError, match="generated output"):
        prepare.preflight_organized_conversions(
            (tmp_path,), organized_root=tmp_path / "prepared", prefix="trafficlab-ready-"
        )


def test_convert_capture_creates_validated_copy_without_modifying_source_or_leaving_temporaries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "capture.pcap"
    destination = tmp_path / "trafficlab-ready-capture.pcapng"
    source.write_bytes(b"original")
    conversion = prepare.Conversion(source, destination)
    command_count = 0
    validated: list[bytes] = []

    def run(command: tuple[str, ...]) -> None:
        nonlocal command_count
        command_count += 1
        if command_count == 1:
            assert command[:3] == ("/tools/editcap", "-F", "pcapng")
            assert Path(command[3]) == source
            Path(command[4]).write_bytes(b"converted")
            return
        assert command_count == 2
        assert command[0] == "/tools/reordercap"
        assert Path(command[1]).read_bytes() == b"converted"
        Path(command[2]).write_bytes(b"ordered")

    def validate(path: Path) -> None:
        validated.append(path.read_bytes())

    prepare.convert_capture(
        conversion,
        tools=prepare.ToolPaths("/tools/editcap", "/tools/reordercap"),
        run=run,
        validate=validate,
    )

    assert source.read_bytes() == b"original"
    assert destination.read_bytes() == b"ordered"
    assert validated == [b"ordered"]
    assert command_count == 2
    assert sorted(path.name for path in tmp_path.iterdir()) == ["capture.pcap", "trafficlab-ready-capture.pcapng"]


def test_convert_capture_does_not_publish_when_validation_fails(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    destination = tmp_path / "trafficlab-ready-capture.pcapng"
    source.write_bytes(b"original")

    def run(command: tuple[str, ...]) -> None:
        Path(command[-1]).write_bytes(b"candidate")

    def reject(_path: Path) -> None:
        raise ValueError("invalid capture")

    with pytest.raises(ValueError, match="invalid capture"):
        prepare.convert_capture(
            prepare.Conversion(source, destination),
            tools=prepare.ToolPaths("/tools/editcap", "/tools/reordercap"),
            run=run,
            validate=reject,
        )

    assert source.read_bytes() == b"original"
    assert not destination.exists()
    assert [path.name for path in tmp_path.iterdir()] == ["capture.pcapng"]


def test_infer_target_mac_prefers_more_transmissions_after_total_appearance_tie(tmp_path: Path) -> None:
    capture = tmp_path / "capture.pcapng"
    capture.write_bytes(
        encode_ethernet_frames(
            (
                EncodedEthernetFrame(0.0, _ethernet(_TARGET, _PEER)),
                EncodedEthernetFrame(1.0, _ethernet(_TARGET, _PEER)),
                EncodedEthernetFrame(2.0, _ethernet(_PEER, _TARGET)),
                EncodedEthernetFrame(3.0, _ethernet(_OTHER, _PEER)),
                EncodedEthernetFrame(4.0, _ethernet(_TARGET, _OTHER)),
            )
        )
    )

    inferred = prepare.infer_target_mac(capture)

    assert inferred.target_mac == "02:42:ac:11:00:02"
    assert inferred.transmitted_packet_count == 3
    assert inferred.source_count == 3
    assert inferred.destination_count == 1
    assert inferred.total_appearances == 4


def test_infer_target_mac_prefers_total_appearances_before_transmissions(tmp_path: Path) -> None:
    target = bytes.fromhex("00163e0ca5da")
    louder_peer = bytes.fromhex("eeffffffffff")
    peer_a = bytes.fromhex("020000000010")
    peer_b = bytes.fromhex("020000000011")
    peer_c = bytes.fromhex("020000000012")
    peer_d = bytes.fromhex("020000000013")
    peer_e = bytes.fromhex("020000000014")
    capture = tmp_path / "capture.pcapng"
    capture.write_bytes(
        encode_ethernet_frames(
            (
                EncodedEthernetFrame(0.0, _ethernet(target, louder_peer)),
                EncodedEthernetFrame(1.0, _ethernet(target, peer_a)),
                EncodedEthernetFrame(2.0, _ethernet(peer_b, target)),
                EncodedEthernetFrame(3.0, _ethernet(peer_c, target)),
                EncodedEthernetFrame(4.0, _ethernet(louder_peer, target)),
                EncodedEthernetFrame(5.0, _ethernet(louder_peer, peer_d)),
                EncodedEthernetFrame(6.0, _ethernet(louder_peer, peer_e)),
            )
        )
    )

    inferred = prepare.infer_target_mac(capture)

    assert inferred.target_mac == "00:16:3e:0c:a5:da"
    assert inferred.transmitted_packet_count == 2
    assert inferred.source_count == 2
    assert inferred.destination_count == 3
    assert inferred.total_appearances == 5


def test_infer_target_mac_breaks_equal_transmissions_and_totals_by_ascending_mac_text(tmp_path: Path) -> None:
    lower = bytes.fromhex("00163e0ca5da")
    higher = bytes.fromhex("02163e0ca5da")
    lower_peer = bytes.fromhex("020000000010")
    higher_peer = bytes.fromhex("020000000011")
    ignored_source = bytes.fromhex("010000000001")
    capture = tmp_path / "capture.pcapng"
    capture.write_bytes(
        encode_ethernet_frames(
            (
                EncodedEthernetFrame(0.0, _ethernet(lower, lower_peer)),
                EncodedEthernetFrame(1.0, _ethernet(ignored_source, lower)),
                EncodedEthernetFrame(2.0, _ethernet(higher, higher_peer)),
                EncodedEthernetFrame(3.0, _ethernet(ignored_source, higher)),
            )
        )
    )

    inferred = prepare.infer_target_mac(capture)

    assert inferred.target_mac == "00:16:3e:0c:a5:da"
    assert inferred.transmitted_packet_count == 1
    assert inferred.source_count == 1
    assert inferred.destination_count == 1
    assert inferred.total_appearances == 2


def test_infer_target_mac_rejects_captures_without_an_eligible_bidirectional_unicast_candidate(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "capture.pcapng"
    capture.write_bytes(
        encode_ethernet_frames(
            (
                EncodedEthernetFrame(0.0, _ethernet(_TARGET, b"\xff" * 6)),
                EncodedEthernetFrame(1.0, _ethernet(_TARGET, _PEER)),
            )
        )
    )

    with pytest.raises(ValueError, match="no eligible target MAC"):
        prepare.infer_target_mac(capture)


def test_infer_target_mac_rejects_a_short_ethernet_frame(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    capture = tmp_path / "capture.pcapng"
    capture.write_bytes(b"unused")
    short_packet = PcapngPacket(
        event=TraceEvent(0.0, Direction.OUTBOUND, 7),
        ethernet_frame=b"shorty!",
    )

    def stub_read_pcapng_packets(
        _source_input: Path,
        _metadata: CaptureMetadata,
        *,
        source: Path,
        deadline: float | None = None,
    ) -> tuple[PcapngPacket, ...]:
        del _source_input, _metadata, source, deadline
        return (short_packet,)

    monkeypatch.setattr(prepare, "read_pcapng_packets", stub_read_pcapng_packets)

    with pytest.raises(ValueError, match="invalid Ethernet frame"):
        prepare.infer_target_mac(capture)


def test_render_inferred_capture_metadata_is_canonical_json() -> None:
    rendered = prepare.render_inferred_capture_metadata("02:42:ac:11:00:02")

    assert rendered == b'{\n  "interface": "eth0",\n  "target_mac": "02:42:ac:11:00:02"\n}\n'
    assert json.loads(rendered) == {"interface": "eth0", "target_mac": "02:42:ac:11:00:02"}


def test_convert_capture_to_organized_directory_publishes_a_validated_pair_and_cleans_staging(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(
        encode_ethernet_frames(
            (
                EncodedEthernetFrame(0.0, _ethernet(_TARGET, _PEER)),
                EncodedEthernetFrame(1.0, _ethernet(_TARGET, _PEER)),
                EncodedEthernetFrame(2.0, _ethernet(_PEER, _TARGET)),
            )
        )
    )
    conversion = prepare.OrganizedConversion(
        source=source,
        directory=tmp_path / "prepared" / "capture",
        capture_path=tmp_path / "prepared" / "capture" / "trafficlab-ready-capture.pcapng",
        metadata_path=tmp_path / "prepared" / "capture" / "capture.json",
    )
    command_count = 0

    def run(command: tuple[str, ...]) -> None:
        nonlocal command_count
        command_count += 1
        Path(command[-1]).write_bytes(source.read_bytes())

    inspection = prepare.convert_capture_to_organized_directory(
        conversion,
        tools=prepare.ToolPaths("/tools/editcap", "/tools/reordercap"),
        run=run,
    )

    assert command_count == 2
    assert validate_capture_pair(conversion.metadata_path, conversion.capture_path, deadline=None) == inspection
    assert json.loads(conversion.metadata_path.read_bytes()) == {
        "interface": "eth0",
        "target_mac": "02:42:ac:11:00:02",
    }
    assert sorted(path.name for path in (tmp_path / "prepared" / "capture").iterdir()) == [
        "capture.json",
        "trafficlab-ready-capture.pcapng",
    ]
    assert not any(path.name.startswith(".trafficlab-dump-") for path in tmp_path.iterdir())


def test_convert_capture_to_organized_directory_does_not_publish_when_pair_validation_fails(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(b"original")
    conversion = prepare.OrganizedConversion(
        source=source,
        directory=tmp_path / "prepared" / "capture",
        capture_path=tmp_path / "prepared" / "capture" / "trafficlab-ready-capture.pcapng",
        metadata_path=tmp_path / "prepared" / "capture" / "capture.json",
    )

    def run(command: tuple[str, ...]) -> None:
        Path(command[-1]).write_bytes(b"not-a-valid-pcapng")

    with pytest.raises(TrafficlabError):
        prepare.convert_capture_to_organized_directory(
            conversion,
            tools=prepare.ToolPaths("/tools/editcap", "/tools/reordercap"),
            run=run,
        )

    assert source.read_bytes() == b"original"
    assert not conversion.directory.exists()
    assert not (tmp_path / "prepared").exists()


def test_plan_organized_conversions_rejects_a_broken_symlink_destination(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(b"capture")
    destination = tmp_path / "prepared" / "capture"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(tmp_path / "missing-target")

    with pytest.raises(ValueError, match="output already exists"):
        prepare.plan_organized_conversions((source,), organized_root=tmp_path / "prepared", prefix="trafficlab-ready-")


def test_convert_capture_to_organized_directory_preserves_a_raced_destination_and_cleans_stage(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(
        encode_ethernet_frames(
            (
                EncodedEthernetFrame(0.0, _ethernet(_TARGET, _PEER)),
                EncodedEthernetFrame(1.0, _ethernet(_PEER, _TARGET)),
            )
        )
    )
    conversion = prepare.OrganizedConversion(
        source=source,
        directory=tmp_path / "prepared" / "capture",
        capture_path=tmp_path / "prepared" / "capture" / "trafficlab-ready-capture.pcapng",
        metadata_path=tmp_path / "prepared" / "capture" / "capture.json",
    )

    def run(command: tuple[str, ...]) -> None:
        Path(command[-1]).write_bytes(source.read_bytes())

    def publish(stage_directory: Path, destination: Path) -> None:
        destination.mkdir(parents=True)
        prepare.publish_directory_no_replace(stage_directory, destination)

    with pytest.raises(ValueError, match="output already exists"):
        prepare.convert_capture_to_organized_directory(
            conversion,
            tools=prepare.ToolPaths("/tools/editcap", "/tools/reordercap"),
            run=run,
            publish=publish,
        )

    assert conversion.directory.is_dir()
    assert list(conversion.directory.iterdir()) == []
    assert not any(path.name.startswith(".trafficlab-dump-") for path in (tmp_path / "prepared").parent.iterdir())


def test_convert_capture_to_organized_directory_reports_cleanup_failures_without_hiding_the_primary_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(b"original")
    conversion = prepare.OrganizedConversion(
        source=source,
        directory=tmp_path / "prepared" / "capture",
        capture_path=tmp_path / "prepared" / "capture" / "trafficlab-ready-capture.pcapng",
        metadata_path=tmp_path / "prepared" / "capture" / "capture.json",
    )

    def run(command: tuple[str, ...]) -> None:
        Path(command[-1]).write_bytes(b"not-a-valid-pcapng")

    def fail_cleanup(path: Path | None) -> None:
        raise OSError(f"cleanup failed for {path}")

    with pytest.raises(TrafficlabError, match="cleanup incomplete") as caught:
        prepare.convert_capture_to_organized_directory(
            conversion,
            tools=prepare.ToolPaths("/tools/editcap", "/tools/reordercap"),
            run=run,
            cleanup=fail_cleanup,
        )

    assert "invalid PCAPNG: Scapy could not decode the capture" in str(caught.value)
    assert not conversion.directory.exists()


def test_publish_directory_no_replace_rejects_a_preexisting_empty_directory(tmp_path: Path) -> None:
    stage_directory = tmp_path / "stage"
    stage_directory.mkdir()
    (stage_directory / "capture.json").write_text("{}", encoding="utf-8")
    destination = tmp_path / "prepared" / "capture"
    destination.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        prepare.publish_directory_no_replace(stage_directory, destination)

    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert stage_directory.is_dir()


def test_publish_directory_no_replace_rejects_a_broken_symlink(tmp_path: Path) -> None:
    stage_directory = tmp_path / "stage"
    stage_directory.mkdir()
    destination = tmp_path / "prepared" / "capture"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(tmp_path / "missing-target")

    with pytest.raises(FileExistsError):
        prepare.publish_directory_no_replace(stage_directory, destination)

    assert destination.is_symlink()
    assert not destination.exists()
    assert stage_directory.is_dir()


def test_validate_capture_uses_the_production_parser_and_positive_reference_window(tmp_path: Path) -> None:
    capture = tmp_path / "capture.pcapng"
    capture.write_bytes(
        encode_events(
            (
                TraceEvent(10.0, Direction.OUTBOUND, 60),
                TraceEvent(10.5, Direction.INBOUND, 80),
            ),
            _METADATA,
        )
    )

    assert prepare.validate_capture(capture) == prepare.PreparedCapture(
        packet_count=2,
        observation_window_seconds=0.5,
    )


@pytest.mark.parametrize("prefix", ["", ".", "..", "nested/ready-", "nested\\ready-"])
def test_validate_prefix_rejects_values_that_could_escape_the_source_directory(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        prepare.validate_prefix(prefix)


def test_find_tools_reports_every_missing_wireshark_program() -> None:
    available = {"editcap": "/tools/editcap", "reordercap": None}

    with pytest.raises(ValueError, match="reordercap"):
        prepare.find_tools(lambda name: available[name])


def test_run_command_reports_program_status_and_stderr(tmp_path: Path) -> None:
    program = tmp_path / "failure"
    program.write_text("#!/bin/sh\necho broken >&2\nexit 23\n", encoding="utf-8")
    program.chmod(0o755)

    with pytest.raises(RuntimeError, match="status 23: broken"):
        prepare.run_command((str(program),))


def test_cli_creates_prefixed_validated_capture_without_changing_input(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    content = encode_events(
        (
            TraceEvent(1.0, Direction.OUTBOUND, 60),
            TraceEvent(2.0, Direction.INBOUND, 80),
        ),
        _METADATA,
    )
    source.write_bytes(content)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    editcap = bin_directory / "editcap"
    editcap.write_text('#!/bin/sh\ncp "$3" "$4"\n', encoding="utf-8")
    editcap.chmod(0o755)
    reordercap = bin_directory / "reordercap"
    reordercap.write_text('#!/bin/sh\ncp "$1" "$2"\n', encoding="utf-8")
    reordercap.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"

    completed = subprocess.run(
        (sys.executable, str(_ROOT / "scripts" / "prepare_traffic_dumps.py"), str(source)),
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    destination = tmp_path / "trafficlab-ready-capture.pcapng"
    assert completed.returncode == 0, completed.stderr
    assert str(destination) in completed.stdout
    assert source.read_bytes() == content
    assert prepare.validate_capture(destination).packet_count == 2


def test_cli_creates_one_validated_capture_pair_per_source_when_organized_root_is_selected(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    content = encode_events(
        (
            TraceEvent(1.0, Direction.OUTBOUND, 60),
            TraceEvent(1.5, Direction.OUTBOUND, 60),
            TraceEvent(2.0, Direction.INBOUND, 80),
        ),
        _METADATA,
    )
    source.write_bytes(content)
    organized_root = tmp_path / "prepared"
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    editcap = bin_directory / "editcap"
    editcap.write_text('#!/bin/sh\ncp "$3" "$4"\n', encoding="utf-8")
    editcap.chmod(0o755)
    reordercap = bin_directory / "reordercap"
    reordercap.write_text('#!/bin/sh\ncp "$1" "$2"\n', encoding="utf-8")
    reordercap.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"

    completed = subprocess.run(
        (
            sys.executable,
            str(_ROOT / "scripts" / "prepare_traffic_dumps.py"),
            str(source),
            "--organized-root",
            str(organized_root),
        ),
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    output_directory = organized_root / "capture"
    capture_path = output_directory / "trafficlab-ready-capture.pcapng"
    metadata_path = output_directory / "capture.json"
    assert completed.returncode == 0, completed.stderr
    assert str(capture_path) in completed.stdout
    assert str(metadata_path) in completed.stdout
    assert "02:42:ac:11:00:02" in completed.stdout
    assert "packets=3" in completed.stdout
    assert "outbound=2" in completed.stdout
    assert "inbound=1" in completed.stdout
    inspection = validate_capture_pair(metadata_path, capture_path, deadline=None)
    assert inspection.packet_count == 3
    assert source.read_bytes() == content
    assert sorted(path.name for path in output_directory.iterdir()) == [
        "capture.json",
        "trafficlab-ready-capture.pcapng",
    ]


def test_cli_rejects_a_non_default_prefix_in_organized_mode(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    source.write_bytes(
        encode_events(
            (
                TraceEvent(1.0, Direction.OUTBOUND, 60),
                TraceEvent(2.0, Direction.INBOUND, 80),
            ),
            _METADATA,
        )
    )
    completed = subprocess.run(
        (
            sys.executable,
            str(_ROOT / "scripts" / "prepare_traffic_dumps.py"),
            str(source),
            "--organized-root",
            str(tmp_path / "prepared"),
            "--prefix",
            "custom-",
        ),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "organized output requires the default prefix" in completed.stderr


def test_cli_creates_a_nested_organized_root_when_missing(tmp_path: Path) -> None:
    source = tmp_path / "capture.pcapng"
    content = encode_events(
        (
            TraceEvent(1.0, Direction.OUTBOUND, 60),
            TraceEvent(1.5, Direction.OUTBOUND, 60),
            TraceEvent(2.0, Direction.INBOUND, 80),
        ),
        _METADATA,
    )
    source.write_bytes(content)
    organized_root = tmp_path / "out" / "prepared"
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    editcap = bin_directory / "editcap"
    editcap.write_text('#!/bin/sh\ncp "$3" "$4"\n', encoding="utf-8")
    editcap.chmod(0o755)
    reordercap = bin_directory / "reordercap"
    reordercap.write_text('#!/bin/sh\ncp "$1" "$2"\n', encoding="utf-8")
    reordercap.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"

    completed = subprocess.run(
        (
            sys.executable,
            str(_ROOT / "scripts" / "prepare_traffic_dumps.py"),
            str(source),
            "--organized-root",
            str(organized_root),
        ),
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    output_directory = organized_root / "capture"
    assert completed.returncode == 0, completed.stderr
    assert output_directory.is_dir()
    assert sorted(path.name for path in output_directory.iterdir()) == [
        "capture.json",
        "trafficlab-ready-capture.pcapng",
    ]
