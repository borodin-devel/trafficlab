"""Safe external traffic-dump preparation behavior."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import prepare_traffic_dumps as prepare
from tests.support.scapy_fixtures import encode_events
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent

_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_ROOT = Path(__file__).parents[3]


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
