#!/usr/bin/env python3
"""Generate or verify the deterministic Phase 2 offline-comparison fixtures."""

import argparse
import tempfile
from pathlib import Path

from trafficlab.artifacts import create_run_directory
from trafficlab.comparison import compare_experiment
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.pcapng import parse_pcapng, write_pcapng
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, load_capture_metadata, render_capture_metadata

_REPOSITORY = Path(__file__).resolve().parents[1]
_EXAMPLE_CONFIG = _REPOSITORY / "examples" / "configs" / "minimal.toml"
_EXAMPLE_DATA = _REPOSITORY / "examples" / "data"
_ARTIFACT_NAMES = ("capture.json", "reference.pcapng", "generated.pcapng", "similarity.json")

_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_REFERENCE_EVENTS = (
    TraceEvent(10.0, Direction.OUTBOUND, 60),
    TraceEvent(11.0, Direction.INBOUND, 100),
    TraceEvent(13.0, Direction.OUTBOUND, 140),
    TraceEvent(16.0, Direction.INBOUND, 100),
    TraceEvent(20.0, Direction.OUTBOUND, 60),
)
_GENERATED_EVENTS = (
    TraceEvent(100.0, Direction.OUTBOUND, 60),
    TraceEvent(102.0, Direction.OUTBOUND, 80),
    TraceEvent(103.0, Direction.INBOUND, 100),
    TraceEvent(107.0, Direction.INBOUND, 160),
    TraceEvent(110.0, Direction.OUTBOUND, 60),
    TraceEvent(111.0, Direction.INBOUND, 200),
)


def _build_temporary_run(root: Path) -> Path:
    base = load_experiment(_EXAMPLE_CONFIG)
    run_directory = root / "run"
    config = base.model_copy(update={"run": base.run.model_copy(update={"directory": run_directory})})
    caller_path = root / "experiment.toml"
    caller_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    (run_directory / "capture.json").write_bytes(render_capture_metadata(_METADATA))
    write_pcapng(run_directory / "reference.pcapng", _REFERENCE_EVENTS, _METADATA)
    write_pcapng(run_directory / "generated.pcapng", _GENERATED_EVENTS, _METADATA)
    compare_experiment(caller_path)
    return run_directory


def _validate_canonical_events(run_directory: Path) -> None:
    metadata = load_capture_metadata(run_directory / "capture.json")
    if metadata != _METADATA:
        raise TrafficlabError(
            "generated capture metadata does not match the hand-listed fixture",
            corrective_action="restore the Phase 2 fixture metadata and regenerate",
        )
    parsed_reference = parse_pcapng(run_directory / "reference.pcapng", metadata)
    parsed_generated = parse_pcapng(run_directory / "generated.pcapng", metadata)
    if parsed_reference != _REFERENCE_EVENTS or parsed_generated != _GENERATED_EVENTS:
        raise TrafficlabError(
            "generated PCAPNG events do not match the hand-listed canonical fixtures",
            corrective_action="restore the Phase 2 fixture events and regenerate",
        )


def _read_artifacts(run_directory: Path) -> dict[str, bytes]:
    return {name: (run_directory / name).read_bytes() for name in _ARTIFACT_NAMES}


def _check_artifacts(generated: dict[str, bytes]) -> None:
    for name in _ARTIFACT_NAMES:
        path = _EXAMPLE_DATA / name
        try:
            checked_in = path.read_bytes()
        except OSError as error:
            raise TrafficlabError(
                f"could not read checked-in Phase 2 fixture {path}: {error}",
                corrective_action="run the fixture generator without --check",
            ) from error
        if checked_in != generated[name]:
            raise TrafficlabError(
                f"checked-in Phase 2 fixture differs from deterministic production output: {path}",
                corrective_action="run the fixture generator without --check and commit the result",
            )


def _write_artifacts(generated: dict[str, bytes]) -> None:
    _EXAMPLE_DATA.mkdir(parents=True, exist_ok=True)
    for name in _ARTIFACT_NAMES:
        path = _EXAMPLE_DATA / name
        try:
            path.write_bytes(generated[name])
        except OSError as error:
            raise TrafficlabError(
                f"could not write Phase 2 fixture {path}: {error}",
                corrective_action="verify the example data directory is writable",
            ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild in a temporary run and byte-compare every checked-in artifact",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    with tempfile.TemporaryDirectory(prefix="trafficlab-phase2-") as temporary:
        run_directory = _build_temporary_run(Path(temporary))
        _validate_canonical_events(run_directory)
        generated = _read_artifacts(run_directory)
    if arguments.check:
        _check_artifacts(generated)
        print("phase 2 fixtures: checked-in bytes match deterministic production output")
    else:
        _write_artifacts(generated)
        print(f"phase 2 fixtures: wrote {', '.join(_ARTIFACT_NAMES)} to {_EXAMPLE_DATA}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrafficlabError as error:
        print(f"phase 2 fixtures: {error}; {error.corrective_action}")
        raise SystemExit(error.exit_code) from None
