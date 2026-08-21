"""Command-line boundary for trafficlab."""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from trafficlab import __version__
from trafficlab.common.config_io import load_experiment
from trafficlab.common.errors import TrafficlabError

if TYPE_CHECKING:
    from trafficlab.capture.stage import CaptureResult
    from trafficlab.comparison.schema import ComparisonResult
    from trafficlab.fitting.stage import FitStageResult
    from trafficlab.generation.stage import GenerationStageResult
    from trafficlab.pipeline.types import RunResult
    from trafficlab.preflight.stage import PreparedExperiment

PrepareExperiment = Callable[[Path], "PreparedExperiment"]
CompareExperiment = Callable[[Path], "ComparisonResult"]
CaptureExperiment = Callable[[Path], "CaptureResult"]
GenerateExperiment = Callable[[Path], "GenerationStageResult"]
FitExperiment = Callable[[Path], "FitStageResult"]
RunExperiment = Callable[[Path], "RunResult"]


def _prepare_experiment(path: Path) -> "PreparedExperiment":
    from trafficlab.preflight.stage import open_or_prepare_experiment

    return open_or_prepare_experiment(path)


def _run_full_preflight(path: Path) -> "PreparedExperiment":
    from trafficlab.preflight.stage import run_preflight

    return run_preflight(path, config_only=False)


def _compare_experiment(path: Path) -> "ComparisonResult":
    from trafficlab.comparison.stage import compare_experiment

    return compare_experiment(path)


def build_parser() -> argparse.ArgumentParser:
    """Build the trafficlab argument parser."""
    parser = argparse.ArgumentParser(prog="trafficlab")
    parser.add_argument("--version", action="version", version=f"trafficlab {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("experiment", type=Path, metavar="EXPERIMENT")
    preflight_parser.add_argument("--config-only", action="store_true")
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("experiment", type=Path, metavar="EXPERIMENT")
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("experiment", type=Path, metavar="EXPERIMENT")
    generate_parser = commands.add_parser("generate")
    generate_parser.add_argument("experiment", type=Path, metavar="EXPERIMENT")
    fit_parser = commands.add_parser("fit")
    fit_parser.add_argument("experiment", type=Path, metavar="EXPERIMENT")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("experiment", type=Path, metavar="EXPERIMENT")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    prepare: PrepareExperiment = _prepare_experiment,
    full_preflight: PrepareExperiment | None = None,
    compare: CompareExperiment = _compare_experiment,
    capture: CaptureExperiment | None = None,
    generate: GenerateExperiment | None = None,
    fit: FitExperiment | None = None,
    run: RunExperiment | None = None,
) -> int:
    """Parse command-line arguments and return an exit status."""
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        parser.print_usage(sys.stderr)
        return 2
    try:
        parsed = parser.parse_args(arguments)
    except SystemExit as error:
        return int(error.code) if error.code is not None else 0

    command = parsed.command

    try:
        if command == "preflight":
            if parsed.config_only:
                prepared = prepare(parsed.experiment)
            elif full_preflight is None:
                prepared = _run_full_preflight(parsed.experiment)
            else:
                prepared = full_preflight(parsed.experiment)
            print(f"preflight: prepared {prepared.run_directory}")
            return 0

        if command == "capture":
            if capture is None:
                from trafficlab.capture.stage import capture_experiment

                capture = capture_experiment
            try:
                result = capture(parsed.experiment)
            except KeyboardInterrupt:
                print("capture: interrupted by user; inspect run.log and retry capture", file=sys.stderr)
                return 130
            print(f"capture: packets={result.packet_count} output={result.reference_path}")
            return 0

        if command == "generate":
            if generate is None:
                from trafficlab.generation.stage import generate_experiment

                generate = generate_experiment
            generated = generate(parsed.experiment)
            print(f"generate: packets={len(generated.trace)} output={generated.generated_path}")
            return 0

        if command == "fit":
            if fit is None:
                from trafficlab.fitting.stage import fit_experiment

                fit = fit_experiment
            fitted = fit(parsed.experiment)
            reused = str(fitted.reused_best_model).lower()
            print(
                f"fit: family={fitted.best_model.family} fitness={fitted.outcome.winner.fitness:.6f} "
                f"output={fitted.best_model_path} reused={reused}"
            )
            return 0

        if command == "run":
            if run is None:
                from trafficlab.pipeline.stage import run_experiment

                run = run_experiment
            try:
                completed = run(parsed.experiment)
            except KeyboardInterrupt:
                print("run: interrupted by user; inspect run.log and retry run", file=sys.stderr)
                return 130
            print(
                f"run: family={completed.fit.outcome.winner.family} "
                f"fitness={completed.fit.outcome.winner.fitness:.6f} "
                f"reference_packets={completed.capture.packet_count} "
                f"generated_packets={len(completed.generation.trace)} "
                f"aggregate_score={completed.comparison.aggregate_score:.6f} "
                f"output={completed.run_directory}"
            )
            return 0

        run_directory = load_experiment(parsed.experiment).run.directory
        result = compare(parsed.experiment)
    except TrafficlabError as error:
        print(f"{command}: {error}; {error.corrective_action}", file=sys.stderr)
        return error.exit_code

    print(f"compare: aggregate_score={result.aggregate_score:.6f} output={run_directory / 'similarity.json'}")
    return 0


def entrypoint() -> NoReturn:
    """Run the command-line entrypoint."""
    raise SystemExit(main())
