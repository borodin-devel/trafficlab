"""Focused retained-window checks for Validation Study natural variation."""

from __future__ import annotations

from dataclasses import replace
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
    frozen_window = max(config.similarity.multiscale_widths_seconds)
    records: list[auditor._Training] = []  # pyright: ignore[reportPrivateUsage]
    for workload in _WORKLOADS:
        for repeat in (1, 2, 3):
            raw_window = frozen_window + (repeat - 1) * 0.25
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


def _frozen_windows(config: ExperimentConfig) -> dict[str, object]:
    frozen_window = max(config.similarity.multiscale_widths_seconds)
    return {workload: frozen_window for workload in _WORKLOADS}


def _held() -> dict[str, HeldOutEvaluation]:
    return {
        workload: cast(HeldOutEvaluation, SimpleNamespace(comparison=cast(ComparisonResult, object())))
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


def test_report_inputs_crops_unequal_raw_windows_at_the_frozen_config_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    training = _training(config)
    frozen_windows = _frozen_windows(config)
    captured: list[tuple[tuple[TraceEvent, ...], tuple[TraceEvent, ...], float]] = []
    _patch_nonvariation_report_dependencies(monkeypatch, captured)
    report = auditor._report_inputs(  # pyright: ignore[reportPrivateUsage]
        training,
        _held(),
        natural_variation_windows=frozen_windows,
    )

    frozen_window = max(config.similarity.multiscale_widths_seconds)
    assert report["natural_variation"]
    assert len(captured) == 18
    assert {window for _reference, _generated, window in captured} == {frozen_window}
    assert all(
        event.timestamp <= window
        for reference, generated, window in captured
        for trace in (reference, generated)
        for event in trace
    )
    assert any(len(trace) == 1 for reference, generated, _window in captured for trace in (reference, generated))


def test_report_inputs_rejects_a_forged_frozen_protocol_window(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    frozen_windows = _frozen_windows(config)
    frozen_windows["short"] = max(config.similarity.multiscale_widths_seconds) + 0.25
    comparisons: list[tuple[tuple[TraceEvent, ...], tuple[TraceEvent, ...], float]] = []
    _patch_nonvariation_report_dependencies(monkeypatch, comparisons)

    with pytest.raises(auditor._Issue) as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._report_inputs(  # pyright: ignore[reportPrivateUsage]
            _training(config),
            _held(),
            natural_variation_windows=frozen_windows,
        )

    assert captured.value.kind == "scientific_semantics_incompatible"
    assert captured.value.affected == "protocol.json"


def test_report_inputs_rejects_a_raw_window_shorter_than_the_frozen_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    frozen_window = max(config.similarity.multiscale_widths_seconds)
    training = _training(config)
    shortened = (replace(training[0], window=frozen_window - 0.25), *training[1:])
    comparisons: list[tuple[tuple[TraceEvent, ...], tuple[TraceEvent, ...], float]] = []
    _patch_nonvariation_report_dependencies(monkeypatch, comparisons)

    with pytest.raises(auditor._Issue) as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._report_inputs(  # pyright: ignore[reportPrivateUsage]
            shortened,
            _held(),
            natural_variation_windows=_frozen_windows(config),
        )

    assert captured.value.kind == "scientific_semantics_incompatible"
    assert captured.value.affected == "training/short/r1"
