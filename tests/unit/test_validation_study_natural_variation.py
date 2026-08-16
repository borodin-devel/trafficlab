"""Focused reference-window checks for Validation Study natural variation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from scripts import audit_validation_study as auditor
from scripts.run_validation_study import PUBLISHED_METHOD_ORDER, HeldOutEvaluation
from trafficlab.comparison import ComparisonResult
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_configuration_pair
from trafficlab.genetic.checkpoint import CheckpointState
from trafficlab.models.registry import BestModel
from trafficlab.trace import Direction, TraceEvent

_ROOT = Path(__file__).resolve().parents[2]
_WORKLOADS = ("short", "streaming", "bursty")


def _config() -> ExperimentConfig:
    return load_configuration_pair(_ROOT / "examples" / "data" / "fit" / "experiment.toml").realized


def _training(config: ExperimentConfig) -> tuple[auditor._Training, ...]:  # pyright: ignore[reportPrivateUsage]
    records: list[auditor._Training] = []  # pyright: ignore[reportPrivateUsage]
    for workload in _WORKLOADS:
        for repeat in (1, 2, 3):
            raw_window = 0.05 + (repeat - 1) * 0.25
            records.append(
                auditor._Training(  # pyright: ignore[reportPrivateUsage]
                    workload=workload,
                    repeat=repeat,
                    directory=Path(f"training/{workload}/r{repeat}"),
                    contents={},
                    config=config,
                    reference=(
                        TraceEvent(0.0, Direction.OUTBOUND, 64),
                        TraceEvent(raw_window, Direction.INBOUND, 96),
                    ),
                    window=raw_window,
                    runtime_seconds=float(repeat),
                    checkpoint=cast(CheckpointState, SimpleNamespace(best_fitness=0.5)),
                    best_model=cast(BestModel, object()),
                    comparison=cast(ComparisonResult, object()),
                )
            )
    return tuple(records)


def _held() -> dict[str, HeldOutEvaluation]:
    return {
        workload: cast(
            HeldOutEvaluation,
            SimpleNamespace(comparison=cast(ComparisonResult, object()), observation_window_seconds=1.0),
        )
        for workload in _WORKLOADS
    }


def _patch_nonvariation_report_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[tuple[tuple[TraceEvent, ...], tuple[TraceEvent, ...], float]],
) -> None:
    score: dict[str, object] = {
        "aggregate": 0.5,
        "methods": {method: 0.5 for method in PUBLISHED_METHOD_ORDER},
    }

    def score_for(_comparison: object) -> dict[str, object]:
        return score

    def no_weight_analysis(_training: object) -> list[dict[str, object]]:
        return []

    def no_invalid_chromosomes(_training: object) -> list[dict[str, object]]:
        return []

    def mmpp_winner_family(_training: object) -> str:
        return "mmpp"

    def capture_comparison(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        _settings: object,
    ) -> ComparisonResult:
        captured.append((reference, generated, window))
        return cast(ComparisonResult, object())

    monkeypatch.setattr(auditor, "_score", score_for)
    monkeypatch.setattr(auditor, "_controlled_weight_analysis", no_weight_analysis)
    monkeypatch.setattr(auditor, "_invalid_chromosome_diagnostics", no_invalid_chromosomes)
    monkeypatch.setattr(auditor, "_winner_family", mmpp_winner_family)
    monkeypatch.setattr(auditor, "compare_traces", capture_comparison)


def test_report_inputs_derives_each_directional_reference_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    training = _training(config)
    captured: list[tuple[tuple[TraceEvent, ...], tuple[TraceEvent, ...], float]] = []
    _patch_nonvariation_report_dependencies(monkeypatch, captured)
    report = auditor._report_inputs(  # pyright: ignore[reportPrivateUsage]
        training,
        _held(),
    )

    assert report["natural_variation"]
    assert len(captured) == 18
    assert {window for _reference, _generated, window in captured} == {0.05, 0.3, 0.55}
    assert all(
        reference[-1].timestamp == window and generated[-1].timestamp <= window
        for reference, generated, window in captured
    )
