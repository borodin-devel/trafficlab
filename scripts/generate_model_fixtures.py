#!/usr/bin/env python3
"""Generate or verify the deterministic traffic-model generation fixture."""

from __future__ import annotations

import argparse
from pathlib import Path

from trafficlab.artifacts import quantize_generated_trace
from trafficlab.compatibility import identify_bytes
from trafficlab.config_io import load_experiment
from trafficlab.errors import TrafficlabError
from trafficlab.models.registry import (
    POISSON_FAMILY,
    get_family,
    load_best_model,
    make_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.pcapng import encode_pcapng_trace, parse_pcapng_bytes_trace
from trafficlab.trace import normalize_reference, parse_capture_metadata

_REPOSITORY = Path(__file__).resolve().parents[1]
_EXAMPLE_CONFIG = _REPOSITORY / "examples" / "configs" / "minimal.toml"
_EXAMPLE_DATA = _REPOSITORY / "examples" / "data"
_MODEL_PATH = _EXAMPLE_DATA / "models" / "best_model.json"
_GENERATED_PATH = _EXAMPLE_DATA / "models" / "generated.pcapng"


def _build_fixture() -> tuple[bytes, bytes]:
    config = load_experiment(_EXAMPLE_CONFIG)
    bounds = config.models.poisson_empirical
    if bounds is None:
        raise TrafficlabError(
            f"Poisson bounds are absent from {_EXAMPLE_CONFIG}",
            corrective_action="restore models.poisson_empirical in the minimal example configuration",
        )

    capture_path = _EXAMPLE_DATA / "capture.json"
    reference_path = _EXAMPLE_DATA / "reference.pcapng"
    try:
        capture_content = capture_path.read_bytes()
        reference_content = reference_path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read parent canonical-trace and offline-similarity fixture input: {error}",
            corrective_action="restore the checked-in canonical-trace and offline-similarity fixture capture and reference artifacts",
        ) from error

    metadata = parse_capture_metadata(capture_content, source=capture_path)
    parsed = parse_pcapng_bytes_trace(reference_content, metadata, source=reference_path)
    reference, window = normalize_reference(parsed)
    artifact = make_best_model(
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
    model_content = render_best_model(artifact)
    loaded = load_best_model(model_content, source=_MODEL_PATH)
    generated = (
        get_family(loaded.family)
        .generate(
            runtime_fitted_model(loaded),
            config.run.final_seed,
            loaded.observation_window_seconds,
            config.generation.final,
        )
        .require_complete()
    )
    rendered_trace = quantize_generated_trace(generated, loaded.observation_window_seconds)
    generated_content = encode_pcapng_trace(rendered_trace, metadata)
    parsed_generated = parse_pcapng_bytes_trace(generated_content, metadata, source=_GENERATED_PATH)
    if parsed_generated.timestamps[-1] > loaded.observation_window_seconds:
        raise TrafficlabError(
            "traffic-model generation fixture generated capture exceeds its stored observation window",
            corrective_action="report the production PCAPNG generation defect",
        )
    if parsed_generated != rendered_trace:
        raise TrafficlabError(
            "traffic-model generation fixture generated capture did not round-trip",
            corrective_action="report the production PCAPNG generation defect",
        )
    return model_content, generated_content


def _check_fixture(path: Path, expected: bytes) -> None:
    try:
        actual = path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read checked-in traffic-model generation fixture {path}: {error}",
            corrective_action="run the traffic-model generation fixture generator without --check",
        ) from error
    if actual != expected:
        raise TrafficlabError(
            f"checked-in traffic-model generation fixture differs from deterministic production output: {path}",
            corrective_action="run the traffic-model generation fixture generator without --check and commit the result",
        )


def _write_fixture(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError as error:
        raise TrafficlabError(
            f"could not write traffic-model generation fixture {path}: {error}",
            corrective_action="verify the traffic-model generation fixture directory is writable",
        ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="byte-compare both checked-in traffic-model generation artifacts"
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    model_content, generated_content = _build_fixture()
    artifacts = ((_MODEL_PATH, model_content), (_GENERATED_PATH, generated_content))
    if arguments.check:
        for path, content in artifacts:
            _check_fixture(path, content)
        print("model fixtures: checked-in bytes match deterministic production output")
    else:
        for path, content in artifacts:
            _write_fixture(path, content)
        print(f"model fixtures: wrote {_MODEL_PATH}, {_GENERATED_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrafficlabError as error:
        print(f"traffic-model generation fixture: {error}; {error.corrective_action}")
        raise SystemExit(error.exit_code) from None
