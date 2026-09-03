#!/usr/bin/env python3
"""Strictly validate and reproduce saved required-candidate run artifacts."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from trafficlab.capture.stage import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config_io import load_configuration_pair
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import align_generated, normalize_reference, parse_capture_metadata
from trafficlab.comparison.codec import parse_comparison_result, similarity_settings_identity
from trafficlab.comparison.metrics import compare_final_traces
from trafficlab.fitting.genetic.checkpoint import parse_checkpoint
from trafficlab.fitting.genetic.strategy import FitOutcome, make_strategy_context
from trafficlab.fitting.stage import FitStageResult
from trafficlab.generation.models.fitted_model import load_best_model
from trafficlab.generation.stage import GenerationStageResult, reproduce_generated_pcapng
from trafficlab.pipeline.validation import validate_final_artifacts
from trafficlab.preflight.stage import open_or_prepare_experiment


@dataclass(frozen=True, slots=True)
class RunCheckResult:
    """Validated saved-run identities and reproduction outcomes."""

    run_directory: Path
    reference_packet_count: int
    generated_packet_count: int
    winner_family: str
    aggregate_score: float
    fitness_method_count: int
    postfit_diagnostic_count: int
    generated_bytes_reproduced: bool
    comparison_reproduced: bool


def check_run(run_directory: Path) -> RunCheckResult:
    """Strictly validate one saved run without fitting or publishing artifacts."""
    run = run_directory.resolve()
    config_path = run / "experiment.toml"
    pair = load_configuration_pair(config_path)
    if pair.realized.run.directory != run:
        raise TrafficlabError(
            "configured run directory does not match the supplied saved run directory",
            corrective_action="provide the matching saved run directory without publishing a new one",
        )
    prepared = open_or_prepare_experiment(config_path)
    metadata_path = run / "capture.json"
    reference_path = run / "reference.pcapng"
    metadata_bytes = metadata_path.read_bytes()
    reference_bytes = reference_path.read_bytes()
    metadata = parse_capture_metadata(metadata_bytes, source=metadata_path)
    inspection = validate_capture_pair(metadata_path, reference_path, deadline=None)
    reference_trace = read_pcapng_bytes(reference_bytes, metadata, source=reference_path)
    reference, window = normalize_reference(reference_trace)
    snapshot_bytes = (run / "experiment.toml").read_bytes()
    context = make_strategy_context(
        pair.realized,
        reference,
        window,
        run,
        experiment_identity=identify_bytes(snapshot_bytes),
        reference_identity=identify_bytes(reference_bytes),
        capture_identity=identify_bytes(metadata_bytes),
    )
    state = parse_checkpoint((run / "checkpoint.json").read_bytes(), context.compatibility)
    winner = next(candidate for candidate in state.population if candidate.identifier == state.best_identifier)
    if state.terminal_reason not in ("hard_limit", "early_stop"):
        raise TrafficlabError(
            "checkpoint is not terminal",
            corrective_action="check a completed required-candidate run",
        )
    best_path = run / "best_model.json"
    best_bytes = best_path.read_bytes()
    best = load_best_model(best_bytes, source=best_path)
    fit = FitStageResult(
        config_path,
        run,
        best_path,
        best,
        FitOutcome(winner, (), state.generation, state.terminal_reason, state.family_priority),
        best.observation_window_seconds,
        True,
    )
    _, reproduced = reproduce_generated_pcapng(best, metadata, clock=lambda: 0.0)
    generated_path = run / "generated.pcapng"
    generated_bytes = generated_path.read_bytes()
    generated_bytes_reproduced = reproduced.content == generated_bytes
    if not generated_bytes_reproduced:
        raise TrafficlabError(
            "generated.pcapng does not equal saved-model/seed reproduction",
            corrective_action="regenerate the saved generated artifact from best_model.json",
        )
    generated_trace = read_pcapng_bytes(generated_bytes, metadata, source=generated_path)
    generation = GenerationStageResult(
        run,
        generated_path,
        generated_trace,
        best.final_seed,
        best.observation_window_seconds,
        True,
    )
    comparison_path = run / "similarity.json"
    comparison_bytes = comparison_path.read_bytes()
    comparison = parse_comparison_result(comparison_bytes)
    recomputed = compare_final_traces(
        reference,
        align_generated(generated_trace, window),
        window,
        pair.realized.similarity,
        {
            "capture_json": identify_bytes(metadata_bytes),
            "generated_pcapng": identify_bytes(generated_bytes),
            "reference_pcapng": identify_bytes(reference_bytes),
            "similarity_settings": similarity_settings_identity(pair.realized.similarity),
        },
    )
    comparison_reproduced = recomputed == comparison
    if not comparison_reproduced:
        raise TrafficlabError(
            "similarity.json does not equal saved-input recomputation",
            corrective_action="recompute comparison from the saved reference, generated trace, and settings",
        )
    capture = CaptureResult(run, reference_path, inspection.packet_count, 0, reused=True)
    validate_final_artifacts(prepared, capture, fit, generation, comparison)
    return RunCheckResult(
        run,
        inspection.packet_count,
        len(generated_trace),
        best.family,
        comparison.aggregate_score,
        len(comparison.methods.model_dump()),
        len(comparison.postfit_diagnostics.model_dump()) if comparison.postfit_diagnostics is not None else 0,
        generated_bytes_reproduced,
        comparison_reproduced,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the saved-run checker argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path, nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate every requested run and print compact machine-readable lines."""
    arguments = build_parser().parse_args(argv)
    try:
        for directory in arguments.run_directory:
            result = check_run(directory)
            print(f"strict_artifacts=pass run={result.run_directory}")
            print(
                "reproduction=pass "
                f"generated_bytes_equal={str(result.generated_bytes_reproduced).lower()} "
                f"comparison_equal={str(result.comparison_reproduced).lower()}"
            )
            print(
                f"packets={result.reference_packet_count} generated_packets={result.generated_packet_count} "
                f"winner={result.winner_family} aggregate={result.aggregate_score:.12f} "
                f"fitness_methods={result.fitness_method_count} postfit_diagnostics={result.postfit_diagnostic_count}"
            )
    except (OSError, TrafficlabError, ValueError, StopIteration) as error:
        print(f"check_required_candidate_run: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
