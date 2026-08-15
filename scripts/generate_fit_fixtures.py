#!/usr/bin/env python3
"""Generate or verify the deterministic Phase 5 offline fitting fixture tree."""

from __future__ import annotations

import argparse
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path

from trafficlab.compatibility import identify_bytes
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.fitting import FitDependencies, fit_experiment, read_fit_input
from trafficlab.genetic.checkpoint import parse_checkpoint, render_history_csv
from trafficlab.genetic.strategy import make_strategy_context, run_strategy
from trafficlab.models.registry import load_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import PreflightReport, PreparedExperiment
from trafficlab.trace import (
    CaptureMetadata,
    Direction,
    TraceEvent,
    normalize_reference,
    parse_capture_metadata,
    render_capture_metadata,
)

REPOSITORY = Path(__file__).resolve().parents[1]
FIT_DIRECTORY = REPOSITORY / "examples" / "data" / "fit"
ARTIFACT_NAMES = (
    "experiment.toml",
    "capture.json",
    "reference.pcapng",
    "checkpoint.json",
    "ga_history.csv",
    "best_model.json",
    "README.md",
)

_CONFIG_TEMPLATE = """\
[run]
directory = "."
minimum_free_bytes = 1
master_seed = 73
final_seed = 97

[target]
image = "trafficlab-offline-fixture:local"
argv = ["fixture-only"]
working_directory = "/work"

[capture]
image = "trafficlab-capture:local"
network_probe_url = "https://example.invalid/"
readiness_timeout_seconds = 2.0
workload_timeout_seconds = 5.0
flush_timeout_seconds = 2.0
total_timeout_seconds = 10.0

[generation.trial]
max_packets = 500
max_output_bytes = 1000000
max_wall_seconds = 5.0

[generation.final]
max_packets = 1000
max_output_bytes = 2000000
max_wall_seconds = 10.0

[genetic]
population_size = 6
generation_count = 1
tournament_size = 2
elite_count = 1
trial_seeds = [17]
duplicate_mutation_attempts = 1
early_stopping_generations = 0
early_stopping_tolerance = 0.0
resume = true

[models]
enabled = ["poisson_empirical", "markov_renewal", "mmpp"]

[models.poisson_empirical]
crossover_probability = 0.35
mutation_probability = 0.0
mutation_scale = 0.07

[models.poisson_empirical.c_lambda]
lower = 0.5
upper = 1.5

[models.markov_renewal]
crossover_probability = 1.0
mutation_probability = 0.0
mutation_scale = 0.06

[models.markov_renewal.q1]
lower = 0.15
upper = 0.45

[models.markov_renewal.q2]
lower = 0.55
upper = 0.9

[models.markov_renewal.alpha]
lower = 0.05
upper = 1.5

[models.markov_renewal.r]
lower = 1
upper = 4

[models.markov_renewal.c_t]
lower = 0.5
upper = 1.5

[models.mmpp]
crossover_probability = 0.45
mutation_probability = 0.0
mutation_scale = 0.08

[models.mmpp.q01]
lower = 0.2
upper = 2.0

[models.mmpp.q10]
lower = 0.2
upper = 2.0

[models.mmpp.lambda0]
lower = 0.5
upper = 3.0

[models.mmpp.lambda1]
lower = 3.0
upper = 8.0

[similarity]
iat_diagnostic_quantile = 0.75
acf_lags = [1]
acf_lag_weights = [1.0]
acf_iat_weight = 0.5
acf_size_weight = 0.5
multiscale_widths_seconds = [1.0, 2.5]
multiscale_scale_weights = [0.5, 0.5]
multiscale_packet_weight = 0.5
multiscale_byte_weight = 0.5
max_direction_bin_cells = 100

[similarity.method_weights]
frame_size_ks = 0.25
iat_ks = 0.25
autocorrelation = 0.25
multiscale_rate = 0.25
"""

_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_REFERENCE_EVENTS = (
    TraceEvent(20.0, Direction.OUTBOUND, 60),
    TraceEvent(20.4, Direction.INBOUND, 96),
    TraceEvent(20.9, Direction.OUTBOUND, 128),
    TraceEvent(21.5, Direction.OUTBOUND, 256),
    TraceEvent(22.0, Direction.INBOUND, 96),
    TraceEvent(22.4, Direction.INBOUND, 512),
    TraceEvent(23.0, Direction.OUTBOUND, 60),
    TraceEvent(23.5, Direction.INBOUND, 128),
    TraceEvent(24.0, Direction.OUTBOUND, 256),
    TraceEvent(24.6, Direction.INBOUND, 96),
    TraceEvent(25.0, Direction.OUTBOUND, 512),
    TraceEvent(25.4, Direction.OUTBOUND, 60),
    TraceEvent(26.0, Direction.INBOUND, 128),
    TraceEvent(26.5, Direction.OUTBOUND, 256),
    TraceEvent(27.0, Direction.INBOUND, 96),
    TraceEvent(27.6, Direction.INBOUND, 512),
    TraceEvent(28.0, Direction.OUTBOUND, 60),
    TraceEvent(28.4, Direction.INBOUND, 128),
    TraceEvent(29.0, Direction.OUTBOUND, 256),
    TraceEvent(29.5, Direction.INBOUND, 96),
    TraceEvent(30.0, Direction.OUTBOUND, 512),
)

_README = b"""\
# Deterministic offline fitting fixture

This directory is a tiny, Docker-free Phase 5 fit captured entirely through production codecs and the real fitting
stage. Regenerate it with `uv run --locked python scripts/generate_fit_fixtures.py`; verify every expected path and
byte with `uv run --locked python scripts/generate_fit_fixtures.py --check`.

The reference contains 21 Ethernet events from timestamp 20.0 through 30.0, so the one normalized observation
window is exactly `W = 10.0` seconds. Registry metadata remains lexical for display, while master seed 73 derives
the neutral family priority `mmpp`, `markov_renewal`, `poisson_empirical` before any search draw. Population size is
6, with quota 2 per family, elite count 1, generation count 1 (evaluated generations 0 and 1), tournament size 2,
duplicate mutation attempts 1, selection seeds `[17]`, and the distinct final-validation seed 97. Resume is enabled
and early stopping is disabled.

Every family deliberately uses nondefault operators:

- `markov_renewal`: crossover 1.0, mutation 0.0, normalized scale 0.06.
- `mmpp`: crossover 0.45, mutation 0.0, normalized scale 0.08.
- `poisson_empirical`: crossover 0.35, mutation 0.0, normalized scale 0.07.

Zero ordinary mutation makes the different-family forced-mutation boundary directly observable in the integration
trace. Trial guards are 500 packets, 1,000,000 bytes, and 5.0 seconds; final guards are 1,000 packets, 2,000,000
bytes, and 10.0 seconds. The checked checkpoint is terminal generation 1, `ga_history.csv` is its exact derived
projection, and `best_model.json` is the independently final-validated winner.
"""


def _fixture_config() -> ExperimentConfig:
    try:
        document = tomllib.loads(_CONFIG_TEMPLATE)
        config = ExperimentConfig.model_validate(document)
        rendered = render_effective_config(config)
        reparsed = ExperimentConfig.model_validate(tomllib.loads(rendered.decode("utf-8")))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as error:
        raise TrafficlabError(
            f"invalid deterministic fit fixture configuration: {error}",
            corrective_action="correct the checked fixture generator configuration",
        ) from error
    if reparsed != config:
        raise TrafficlabError(
            "deterministic fit fixture configuration did not round-trip",
            corrective_action="report the production effective-configuration codec defect",
        )
    return config


def _prepared_fixture(config: ExperimentConfig, run_directory: Path) -> PreparedExperiment:
    return PreparedExperiment(
        source=Path("examples/data/fit/experiment.toml"),
        config=config,
        report=PreflightReport(config, ()),
        run_directory=run_directory,
    )


def _validate_fixture_tree(tree: dict[str, bytes], run_directory: Path) -> None:
    if tuple(tree) != ARTIFACT_NAMES:
        raise TrafficlabError(
            "deterministic fit fixture tree has the wrong paths",
            corrective_action="restore the complete ordered Phase 5 fixture artifact set",
        )
    try:
        config = ExperimentConfig.model_validate(tomllib.loads(tree["experiment.toml"].decode("utf-8")))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as error:
        raise TrafficlabError(
            f"invalid generated fit experiment fixture: {error}",
            corrective_action="report the production effective-configuration codec defect",
        ) from error
    metadata = parse_capture_metadata(tree["capture.json"], source=Path("capture.json"))
    parsed = parse_pcapng_bytes(tree["reference.pcapng"], metadata, source=Path("reference.pcapng"))
    reference, window = normalize_reference(parsed)
    context = make_strategy_context(
        config,
        reference,
        window,
        run_directory,
        experiment_identity=identify_bytes(tree["experiment.toml"]),
        reference_identity=identify_bytes(tree["reference.pcapng"]),
        capture_identity=identify_bytes(tree["capture.json"]),
    )
    checkpoint = parse_checkpoint(tree["checkpoint.json"], context.compatibility)
    if render_history_csv(checkpoint) != tree["ga_history.csv"]:
        raise TrafficlabError(
            "generated fit history is not the exact checkpoint projection",
            corrective_action="report the production checkpoint/history codec defect",
        )
    best = load_best_model(tree["best_model.json"], source=Path("best_model.json"))
    if render_best_model(best) != tree["best_model.json"] or best.observation_window_seconds != window:
        raise TrafficlabError(
            "generated best model is not canonical for the fixture observation window",
            corrective_action="report the production best-model codec defect",
        )
    try:
        tree["README.md"].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise TrafficlabError(
            f"generated fit fixture README is not UTF-8: {error}",
            corrective_action="save the fixture README as UTF-8",
        ) from error


def generate_fixture_tree() -> dict[str, bytes]:
    """Run the real fit stage in an isolated directory and return all portable deterministic fixture bytes."""
    config = _fixture_config()
    experiment_content = render_effective_config(config)
    capture_content = render_capture_metadata(_METADATA)
    reference_content = encode_pcapng(_REFERENCE_EVENTS, _METADATA)
    with tempfile.TemporaryDirectory(prefix="trafficlab-fit-fixture-") as temporary:
        run_directory = Path(temporary) / "run"
        run_directory.mkdir()
        (run_directory / "experiment.toml").write_bytes(experiment_content)
        (run_directory / "capture.json").write_bytes(capture_content)
        (run_directory / "reference.pcapng").write_bytes(reference_content)
        (run_directory / "run.log").write_bytes(b"")
        prepared = _prepared_fixture(config, run_directory)
        dependencies = FitDependencies(lambda _path: prepared, read_fit_input, run_strategy)
        fit_experiment(Path("examples/data/fit/experiment.toml"), dependencies=dependencies)
        tree = {
            "experiment.toml": experiment_content,
            "capture.json": capture_content,
            "reference.pcapng": reference_content,
            "checkpoint.json": (run_directory / "checkpoint.json").read_bytes(),
            "ga_history.csv": (run_directory / "ga_history.csv").read_bytes(),
            "best_model.json": (run_directory / "best_model.json").read_bytes(),
            "README.md": _README,
        }
        _validate_fixture_tree(tree, run_directory)
        return tree


def _fixture_label(name: str) -> str:
    return (Path("examples") / "data" / "fit" / name).as_posix()


def compare_fixture_tree(expected: dict[str, bytes]) -> int:
    """Compare every expected byte/path and reject every unexpected entry, reporting all relative paths."""
    mismatches: list[str] = []
    for name in sorted(expected):
        path = FIT_DIRECTORY / name
        try:
            actual = path.read_bytes()
        except OSError:
            actual = None
        if actual != expected[name]:
            mismatches.append(f"mismatched fixture path: {_fixture_label(name)}")
    try:
        actual_names = {
            path.relative_to(FIT_DIRECTORY).as_posix() for path in FIT_DIRECTORY.rglob("*") if path.is_file()
        }
    except OSError as error:
        raise TrafficlabError(
            f"could not inspect checked fit fixture directory {FIT_DIRECTORY}: {error}",
            corrective_action="verify the fixture directory is readable",
        ) from error
    for name in sorted(actual_names - set(expected)):
        mismatches.append(f"unexpected fixture path: {_fixture_label(name)}")
    for message in mismatches:
        print(message, file=sys.stderr)
    return int(bool(mismatches))


def write_fixture_tree(expected: dict[str, bytes]) -> None:
    """Write exactly the complete expected tree below examples/data/fit and nowhere else in the repository."""
    if tuple(expected) != ARTIFACT_NAMES or any(Path(name).name != name for name in expected):
        raise TrafficlabError(
            "refusing to write an invalid fit fixture path set",
            corrective_action="restore the exact flat Phase 5 fixture artifact names",
        )
    try:
        FIT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        for name, content in expected.items():
            (FIT_DIRECTORY / name).write_bytes(content)
    except OSError as error:
        raise TrafficlabError(
            f"could not write deterministic fit fixture tree: {error}",
            corrective_action="verify examples/data/fit is writable",
        ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare every checked-in fit fixture path and byte")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    expected = generate_fixture_tree()
    if arguments.check:
        status = compare_fixture_tree(expected)
        if status == 0:
            print("phase 5 fit fixtures: checked-in paths and bytes match deterministic production output")
        return status
    write_fixture_tree(expected)
    print(f"phase 5 fit fixtures: wrote {', '.join(ARTIFACT_NAMES)} to {FIT_DIRECTORY}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrafficlabError as error:
        print(f"phase 5 fit fixtures: {error}; {error.corrective_action}", file=sys.stderr)
        raise SystemExit(error.exit_code) from None
