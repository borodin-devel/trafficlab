#!/usr/bin/env python3
"""Generate or verify the deterministic Phase 2 offline-comparison fixtures."""

import argparse
import tempfile
from pathlib import Path

from trafficlab.artifacts import create_run_directory
from trafficlab.comparison import compare_experiment
from trafficlab.compatibility import identify_bytes
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.generation import reproduce_generated_pcapng
from trafficlab.models.registry import POISSON_FAMILY, load_best_model, make_best_model, render_best_model
from trafficlab.pcapng import parse_pcapng, write_pcapng
from trafficlab.trace import (
    CaptureMetadata,
    Direction,
    TraceEvent,
    load_capture_metadata,
    normalize_reference,
    render_capture_metadata,
)

_REPOSITORY = Path(__file__).resolve().parents[1]
_EXAMPLE_CONFIG = _REPOSITORY / "examples" / "configs" / "minimal.toml"
_EXAMPLE_DATA = _REPOSITORY / "examples" / "data"
_ARTIFACT_NAMES = (
    "capture.json",
    "reference.pcapng",
    "best_model.json",
    "generated.pcapng",
    "similarity.json",
)

_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_REFERENCE_EVENTS = (
    TraceEvent(10.0, Direction.OUTBOUND, 60),
    TraceEvent(11.0, Direction.INBOUND, 100),
    TraceEvent(13.0, Direction.OUTBOUND, 140),
    TraceEvent(16.0, Direction.INBOUND, 100),
    TraceEvent(20.0, Direction.OUTBOUND, 60),
)


def _build_temporary_run(root: Path) -> Path:
    base = load_experiment(_EXAMPLE_CONFIG)
    run_directory = root / "run"
    config = base.model_copy(update={"run": base.run.model_copy(update={"directory": run_directory})})
    caller_path = root / "experiment.toml"
    caller_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    capture_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    model_path = run_directory / "best_model.json"
    generated_path = run_directory / "generated.pcapng"
    capture_content = render_capture_metadata(_METADATA)
    capture_path.write_bytes(capture_content)
    write_pcapng(reference_path, _REFERENCE_EVENTS, _METADATA)
    reference_content = reference_path.read_bytes()
    bounds = config.models.poisson_empirical
    if bounds is None:
        raise TrafficlabError(
            "minimal example configuration has no Poisson model bounds",
            corrective_action="restore models.poisson_empirical before generating Phase 2 fixtures",
        )
    reference, window = normalize_reference(_REFERENCE_EVENTS)
    model = make_best_model(
        POISSON_FAMILY,
        reference,
        (1.0,),
        reference_identity=identify_bytes(reference_content),
        capture_identity=identify_bytes(capture_content),
        final_seed=config.run.final_seed,
        final_limits=config.generation.final,
        W=window,
        bounds=bounds,
    )
    model_content = render_best_model(model)
    model_path.write_bytes(model_content)
    loaded = load_best_model(model_content, source=model_path)
    _, _, generated_content = reproduce_generated_pcapng(loaded, _METADATA, clock=lambda: 0.0)
    generated_path.write_bytes(generated_content)
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
    if parsed_reference != _REFERENCE_EVENTS:
        raise TrafficlabError(
            "reference PCAPNG events do not match the hand-listed canonical fixture",
            corrective_action="restore the Phase 2 reference events and regenerate",
        )
    model_path = run_directory / "best_model.json"
    model = load_best_model(model_path.read_bytes(), source=model_path)
    _, _, expected_generated = reproduce_generated_pcapng(model, metadata, clock=lambda: 0.0)
    if (run_directory / "generated.pcapng").read_bytes() != expected_generated:
        raise TrafficlabError(
            "generated PCAPNG is not owned by the retained fitted model",
            corrective_action="regenerate the Phase 2 model and generated capture together",
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
        print("similarity fixtures: checked-in bytes match deterministic production output")
    else:
        _write_artifacts(generated)
        print(f"similarity fixtures: wrote {', '.join(_ARTIFACT_NAMES)} to {_EXAMPLE_DATA}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrafficlabError as error:
        print(f"similarity fixtures: {error}; {error.corrective_action}")
        raise SystemExit(error.exit_code) from None
