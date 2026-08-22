from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace as replace_dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import ExperimentConfig, FloatBounds, GenerationLimits, PoissonConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, render_capture_metadata
from trafficlab.fitting.genetic.strategy import FitOutcome, StrategyContext, make_strategy_context, run_strategy
from trafficlab.fitting.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.fitting.stage import FitDependencies, fit_experiment
from trafficlab.generation.models.fitted_model import make_best_model, render_best_model
from trafficlab.generation.models.registry import POISSON_FAMILY
from trafficlab.preflight.types import PreflightReport, PreparedExperiment


def replace_record[Record](record: Record, **changes: object) -> Record:
    """Build deliberate immutable-record states at the fitting test boundary."""
    if isinstance(record, BaseModel):
        values = {name: getattr(record, name) for name in type(record).model_fields}
        values.update(changes)
        return cast(Record, type(record).model_construct(**values))
    return cast(Record, replace_dataclass(cast(Any, record), **changes))


RAW_REFERENCE = (
    TraceEvent(10.0, Direction.OUTBOUND, 64),
    TraceEvent(11.0, Direction.INBOUND, 128),
    TraceEvent(12.0, Direction.OUTBOUND, 256),
)
NORMALIZED_REFERENCE = TrafficTrace.from_events(
    tuple(replace_record(event, timestamp=event.timestamp - 10.0) for event in RAW_REFERENCE)
)
METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")


def build_config(valid_config_data: dict[str, object], run_directory: Path) -> ExperimentConfig:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["poisson_empirical"]
    models["markov_renewal"] = None
    models["mmpp"] = None
    genetic = cast(dict[str, object], data["genetic"])
    genetic.update(
        population_size=2,
        generation_count=0,
        tournament_size=2,
        elite_count=1,
        trial_seeds=[101],
        resume=True,
    )
    return ExperimentConfig.model_validate(data)


def build_prepared(config: ExperimentConfig, experiment_path: Path) -> PreparedExperiment:
    return PreparedExperiment(experiment_path, config, PreflightReport(config, ()), config.run.directory)


def build_trial(seed: int, score: float = 0.75) -> TrialResult:
    methods = tuple(MethodTrialResult(name=name, score=score, diagnostics={"literal": score}) for name in METHOD_ORDER)
    return TrialResult(seed=seed, aggregate_score=score, methods=cast(Any, methods))


def build_outcome(config: ExperimentConfig, *, genes: tuple[float, ...] = (1.0,)) -> FitOutcome:
    winner = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="poisson_empirical",
        genes=genes,
        status="valid",
        fitness=0.75,
        trials=(build_trial(config.genetic.trial_seeds[0]),),
        invalid=None,
        duplicate_diagnostics=(),
    )
    return FitOutcome(winner, (build_trial(config.run.final_seed),), 0, "hard_limit", ("poisson_empirical",))


def build_inputs(config: ExperimentConfig, *, snapshot: bytes | None = None) -> dict[Path, bytes]:
    run_directory = config.run.directory
    return {
        run_directory / "experiment.toml": render_effective_config(config) if snapshot is None else snapshot,
        run_directory / "capture.json": render_capture_metadata(METADATA),
        run_directory / "reference.pcapng": encode_pcapng(RAW_REFERENCE, METADATA),
    }


def build_dependencies(
    config: ExperimentConfig,
    experiment_path: Path,
    inputs: dict[Path, bytes],
    strategy: Callable[[StrategyContext], FitOutcome],
    *,
    reads: list[str] | None = None,
) -> FitDependencies:
    def read(path: Path) -> bytes:
        if reads is not None:
            reads.append(path.name)
        return inputs[path]

    return FitDependencies(lambda _path: build_prepared(config, experiment_path), read, strategy)


def valid_best_bytes(*, gene: float = 1.0, reference_hash: str = "a" * 64) -> bytes:
    model = make_best_model(
        POISSON_FAMILY,
        NORMALIZED_REFERENCE,
        (gene,),
        reference_identity=ContentIdentity(size=1, sha256=reference_hash),
        capture_identity=ContentIdentity(size=1, sha256="b" * 64),
        final_seed=101,
        final_limits=GenerationLimits(max_packets=1, max_output_bytes=1, max_wall_seconds=1.0),
        W=2.0,
        bounds=PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0)),
    )
    return render_best_model(model)


def strategy_context_for_inputs(config: ExperimentConfig, inputs: dict[Path, bytes]) -> StrategyContext:
    run_directory = config.run.directory
    return make_strategy_context(
        config,
        NORMALIZED_REFERENCE,
        2.0,
        run_directory,
        experiment_identity=identify_bytes(inputs[run_directory / "experiment.toml"]),
        reference_identity=identify_bytes(inputs[run_directory / "reference.pcapng"]),
        capture_identity=identify_bytes(inputs[run_directory / "capture.json"]),
    )


def create_real_terminal_run(
    valid_config_data: dict[str, object], tmp_path: Path
) -> tuple[Path, ExperimentConfig, dict[Path, bytes]]:
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    base_config = build_config(valid_config_data, run_directory)
    poisson = base_config.models.poisson_empirical
    assert poisson is not None
    config = base_config.model_copy(
        update={
            "models": base_config.models.model_copy(
                update={
                    "poisson_empirical": poisson.model_copy(update={"c_lambda": FloatBounds(lower=20.0, upper=21.0)})
                }
            )
        }
    )
    inputs = build_inputs(config)
    result = fit_experiment(
        experiment_path,
        dependencies=build_dependencies(config, experiment_path, inputs, run_strategy),
    )
    assert result.outcome.terminal_reason == "hard_limit"
    assert (run_directory / "checkpoint.json").is_file()
    assert (run_directory / "ga_history.csv").is_file()
    assert result.best_model_path.is_file()
    return experiment_path, config, inputs
