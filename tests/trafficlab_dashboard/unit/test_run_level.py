from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import cast

import numpy as np
import pytest

import trafficlab.comparison.metrics as comparison_metrics
from tests.fixtures.paths import REPOSITORY_ROOT
from tests.support.config import valid_config_data
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.codec import parse_comparison_result
from trafficlab.comparison.diagnostics import MultiscaleDiagnostic
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.types import CandidateId, HistoryRow
from trafficlab_dashboard.aspects.base import BarPlotData, CalculationSettings, LinePlotData
from trafficlab_dashboard.aspects.run_level import (
    GaFitnessHistoryAspect,
    MultiscaleDiscrepancyAspect,
    SimilarityScoresAspect,
)
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun

_CHECKED_RUN = REPOSITORY_ROOT / "examples" / "scientific_stack" / "example_run_artifacts"


def _trace(*events: tuple[float, Direction, int]) -> TrafficTrace:
    return TrafficTrace.from_events(
        tuple(
            TraceEvent(timestamp=float(timestamp), direction=direction, frame_length=frame_length)
            for timestamp, direction, frame_length in events
        )
    )


def _checked_similarity() -> ComparisonResult:
    return parse_comparison_result((_CHECKED_RUN / "similarity.json").read_bytes())


def _experiment() -> ExperimentConfig:
    return ExperimentConfig.model_validate(valid_config_data(Path.cwd()))


def _history_rows() -> tuple[HistoryRow, ...]:
    return (
        HistoryRow(
            generation=0,
            scope="family",
            family="markov_renewal",
            candidate_count=4,
            valid_count=3,
            best_fitness=0.61,
            mean_fitness=0.58,
            best_identifier=CandidateId(birth_generation=0, birth_index=4),
        ),
        HistoryRow(
            generation=0,
            scope="family",
            family="mmpp",
            candidate_count=4,
            valid_count=2,
            best_fitness=0.55,
            mean_fitness=0.44,
            best_identifier=CandidateId(birth_generation=0, birth_index=2),
        ),
        HistoryRow(
            generation=0,
            scope="family",
            family="poisson_empirical",
            candidate_count=4,
            valid_count=4,
            best_fitness=0.59,
            mean_fitness=0.50,
            best_identifier=CandidateId(birth_generation=0, birth_index=1),
        ),
        HistoryRow(
            generation=0,
            scope="overall",
            family=None,
            candidate_count=12,
            valid_count=9,
            best_fitness=0.61,
            mean_fitness=0.51,
            best_identifier=CandidateId(birth_generation=0, birth_index=4),
        ),
        HistoryRow(
            generation=1,
            scope="family",
            family="markov_renewal",
            candidate_count=4,
            valid_count=4,
            best_fitness=0.72,
            mean_fitness=0.67,
            best_identifier=CandidateId(birth_generation=1, birth_index=1),
        ),
        HistoryRow(
            generation=1,
            scope="family",
            family="mmpp",
            candidate_count=4,
            valid_count=3,
            best_fitness=0.63,
            mean_fitness=0.57,
            best_identifier=CandidateId(birth_generation=1, birth_index=3),
        ),
        HistoryRow(
            generation=1,
            scope="family",
            family="poisson_empirical",
            candidate_count=4,
            valid_count=2,
            best_fitness=0.64,
            mean_fitness=0.49,
            best_identifier=CandidateId(birth_generation=1, birth_index=2),
        ),
        HistoryRow(
            generation=1,
            scope="overall",
            family=None,
            candidate_count=12,
            valid_count=9,
            best_fitness=0.72,
            mean_fitness=0.58,
            best_identifier=CandidateId(birth_generation=1, birth_index=1),
        ),
    )


def _run(
    *,
    similarity: ComparisonResult | None = None,
    history: tuple[HistoryRow, ...] | None = None,
    experiment: ExperimentConfig | None = None,
    unavailable: Mapping[str, str] | None = None,
) -> DashboardRun:
    return DashboardRun(
        directory=Path.cwd() / "run",
        identities=ArtifactIdentities(
            reference_sha256="1" * 64,
            generated_sha256="2" * 64,
            capture_sha256="3" * 64,
            similarity_sha256="4" * 64 if similarity is not None else None,
            best_model_sha256=None,
            history_sha256="5" * 64 if history is not None else None,
        ),
        metadata=CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:10"),
        reference=_trace(
            (0.0, Direction.OUTBOUND, 100),
            (0.5, Direction.INBOUND, 120),
            (1.0, Direction.OUTBOUND, 140),
        ),
        generated=_trace(
            (0.0, Direction.OUTBOUND, 90),
            (0.4, Direction.INBOUND, 110),
            (0.9, Direction.OUTBOUND, 130),
        ),
        window=1.0,
        similarity=similarity,
        best_model=None,
        history=history,
        experiment=experiment,
        unavailable=MappingProxyType(dict(unavailable or {})),
    )


def test_similarity_scores_use_stored_values_without_recomputation() -> None:
    similarity = _checked_similarity()

    data = SimilarityScoresAspect().calculate(
        _run(similarity=similarity),
        CalculationSettings.default(),
    )

    assert isinstance(data, BarPlotData)
    assert data.categories == ("Frame-size KS", "IAT KS", "Autocorrelation", "Multiscale", "Aggregate")
    assert data.values.tolist() == pytest.approx(
        [
            similarity.methods["frame_size_ks"].score,
            similarity.methods["iat_ks"].score,
            similarity.methods["autocorrelation"].score,
            similarity.methods["multiscale_rate"].score,
            similarity.aggregate_score,
        ]
    )
    assert data.y_label == "Score"
    assert data.y_limits == (0.0, 1.0)
    assert data.metadata["method_identifiers"] == (
        "frame_size_ks",
        "iat_ks",
        "autocorrelation",
        "multiscale_rate",
        "aggregate",
    )
    assert data.metadata["component_weights"] == pytest.approx(
        (
            similarity.methods["frame_size_ks"].weight,
            similarity.methods["iat_ks"].weight,
            similarity.methods["autocorrelation"].weight,
            similarity.methods["multiscale_rate"].weight,
        )
    )
    assert data.metadata["observation_window_seconds"] == similarity.observation_window_seconds
    assert type(data.metadata) is MappingProxyType
    assert not data.values.flags.writeable
    with pytest.raises(TypeError):
        data.metadata["extra"] = "forbidden"  # type: ignore[index]


def test_multiscale_discrepancy_uses_stored_scale_diagnostics_and_preserves_metadata() -> None:
    similarity = _checked_similarity()
    diagnostics = cast(MultiscaleDiagnostic, similarity.methods["multiscale_rate"].diagnostics)

    data = MultiscaleDiscrepancyAspect().calculate(
        _run(similarity=similarity),
        CalculationSettings.default(),
    )

    assert isinstance(data, LinePlotData)
    assert tuple(series.label for series in data.series) == ("Packet discrepancy", "Byte discrepancy")
    assert data.x_label == "Scale width (s)"
    assert data.y_label == "Discrepancy"
    assert data.y_limits == (0.0, 1.0)
    assert [series.x.tolist() for series in data.series] == [
        pytest.approx(list(diagnostics.widths)),
        pytest.approx(list(diagnostics.widths)),
    ]
    assert data.series[0].y.tolist() == pytest.approx(
        [scale.feature_discrepancies.packet for scale in diagnostics.scales]
    )
    assert data.series[1].y.tolist() == pytest.approx(
        [scale.feature_discrepancies.byte for scale in diagnostics.scales]
    )
    assert cast(tuple[float, ...], data.metadata["scale_weights"]) == pytest.approx(diagnostics.scale_weights)
    assert cast(tuple[float, ...], data.metadata["scale_discrepancies"]) == pytest.approx(
        diagnostics.scale_discrepancies
    )
    assert cast(tuple[int, ...], data.metadata["direction_bin_cell_counts"]) == diagnostics.direction_bin_cell_counts
    assert cast(int, data.metadata["total_direction_bin_cells"]) == diagnostics.total_direction_bin_cells
    assert cast(Mapping[str, float], data.metadata["feature_weights"]) == {
        "packet": diagnostics.feature_weights.packet,
        "byte": diagnostics.feature_weights.byte,
    }
    assert cast(Mapping[str, float], data.metadata["aggregate_feature_discrepancies"]) == {
        "packet": diagnostics.feature_discrepancies.packet,
        "byte": diagnostics.feature_discrepancies.byte,
    }
    assert cast(float, data.metadata["discrepancy"]) == diagnostics.discrepancy
    for series in data.series:
        assert not series.x.flags.writeable
        assert not series.y.flags.writeable


def test_ga_fitness_history_uses_canonical_lexical_family_order_not_encounter_or_enabled_order() -> None:
    history = (
        _history_rows()[2],
        _history_rows()[0],
        _history_rows()[1],
        _history_rows()[3],
        _history_rows()[6],
        _history_rows()[4],
        _history_rows()[5],
        _history_rows()[7],
    )

    data = GaFitnessHistoryAspect().calculate(
        _run(history=history, experiment=_experiment()),
        CalculationSettings.default(),
    )

    assert isinstance(data, LinePlotData)
    assert tuple(series.label for series in data.series) == (
        "Markov Renewal",
        "MMPP",
        "Poisson empirical",
        "Overall",
    )
    assert [series.x.tolist() for series in data.series] == [
        pytest.approx([0.0, 1.0]),
        pytest.approx([0.0, 1.0]),
        pytest.approx([0.0, 1.0]),
        pytest.approx([0.0, 1.0]),
    ]
    assert [series.y.tolist() for series in data.series] == [
        pytest.approx([0.61, 0.72]),
        pytest.approx([0.55, 0.63]),
        pytest.approx([0.59, 0.64]),
        pytest.approx([0.61, 0.72]),
    ]
    assert cast(tuple[str, ...], data.metadata["series_identifiers"]) == (
        "markov_renewal",
        "mmpp",
        "poisson_empirical",
        "overall",
    )
    candidate_counts = cast(Mapping[str, tuple[int, ...]], data.metadata["candidate_counts"])
    valid_counts = cast(Mapping[str, tuple[int, ...]], data.metadata["valid_counts"])
    best_birth_generations = cast(Mapping[str, tuple[int, ...]], data.metadata["best_birth_generations"])
    best_birth_indices = cast(Mapping[str, tuple[int, ...]], data.metadata["best_birth_indices"])
    assert candidate_counts["mmpp"] == (4, 4)
    assert valid_counts["poisson_empirical"] == (4, 2)
    assert best_birth_generations["overall"] == (0, 1)
    assert best_birth_indices["markov_renewal"] == (4, 1)


def test_ga_fitness_history_projection_orders_each_series_by_generation() -> None:
    data = GaFitnessHistoryAspect().calculate(
        _run(history=tuple(reversed(_history_rows())), experiment=_experiment()),
        CalculationSettings.default(),
    )

    assert [series.x.tolist() for series in data.series] == [[0.0, 1.0]] * 4


def test_ga_fitness_history_requires_loader_reported_experiment_availability() -> None:
    with pytest.raises(ValueError, match="experiment.toml is missing"):
        GaFitnessHistoryAspect().calculate(
            _run(
                history=_history_rows(),
                experiment=None,
                unavailable={"ga_fitness_history": "experiment.toml is missing"},
            ),
            CalculationSettings.default(),
        )


def test_run_level_aspects_use_only_stored_artifacts_and_not_compare_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    similarity = _checked_similarity()
    history = _history_rows()
    run = _run(similarity=similarity, history=history, experiment=_experiment())

    def fail_compare_traces(*args: object, **kwargs: object) -> object:
        raise AssertionError("run-level dashboard aspects must not recompute compare_traces")

    monkeypatch.setattr(comparison_metrics, "compare_traces", fail_compare_traces)

    similarity_data = SimilarityScoresAspect().calculate(
        _run(similarity=similarity, experiment=None),
        CalculationSettings.default(),
    )
    multiscale_data = MultiscaleDiscrepancyAspect().calculate(
        _run(similarity=similarity, experiment=None),
        CalculationSettings.default(),
    )
    history_data = GaFitnessHistoryAspect().calculate(run, CalculationSettings.default())

    assert similarity_data.values.tolist()
    assert multiscale_data.series[0].y.tolist()
    assert history_data.series[-1].label == "Overall"


def test_multiscale_discrepancy_reduces_each_rendered_series_above_general_cap() -> None:
    count = 20_001
    similarity = _checked_similarity()
    original = cast(MultiscaleDiagnostic, similarity.methods.multiscale_rate.diagnostics)
    widths = tuple(np.linspace(0.00001, original.observation_window_seconds, num=count, dtype=np.float64))
    packet_values = np.linspace(0.1, 0.9, num=count, dtype=np.float64)
    byte_values = np.linspace(0.9, 0.1, num=count, dtype=np.float64)
    scales = tuple(
        original.scales[0].model_copy(
            update={
                "width_seconds": width,
                "direction_bin_cell_count": 2,
                "feature_discrepancies": original.feature_weights.model_copy(
                    update={"packet": float(packet), "byte": float(byte)}
                ),
                "discrepancy": float((packet + byte) / 2.0),
            }
        )
        for width, packet, byte in zip(widths, packet_values, byte_values, strict=True)
    )
    diagnostics = original.model_copy(
        update={
            "widths": widths,
            "scale_weights": tuple(1.0 / count for _ in range(count)),
            "direction_bin_cell_counts": tuple(2 for _ in range(count)),
            "total_direction_bin_cells": 2 * count,
            "scales": scales,
            "scale_discrepancies": tuple(0.5 for _ in range(count)),
        }
    )
    method = similarity.methods.multiscale_rate.model_copy(update={"diagnostics": diagnostics})
    run = _run(
        similarity=similarity.model_copy(
            update={"methods": similarity.methods.model_copy(update={"multiscale_rate": method})}
        )
    )

    data = MultiscaleDiscrepancyAspect().calculate(run, CalculationSettings.default())

    assert [len(series.x) for series in data.series] == [20_000, 20_000]
    assert [series.sample_count for series in data.series] == [count, count]
    assert data.series[0].x[[0, -1]].tolist() == pytest.approx([widths[0], widths[-1]])
    assert float(np.min(data.series[0].y)) == pytest.approx(0.1)
    assert float(np.max(data.series[0].y)) == pytest.approx(0.9)
    assert len(cast(tuple[float, ...], data.metadata["scale_weights"])) == count
    assert len(cast(tuple[int, ...], data.metadata["direction_bin_cell_counts"])) == count


def test_ga_history_reduces_each_rendered_series_above_general_cap_and_keeps_full_metadata() -> None:
    count = 20_001
    experiment = _experiment()
    experiment = experiment.model_copy(update={"models": experiment.models.model_copy(update={"enabled": ("mmpp",)})})
    rows: list[HistoryRow] = []
    for generation in range(count):
        fitness = float(generation) / float(count - 1)
        identifier = CandidateId(birth_generation=generation, birth_index=0)
        rows.extend(
            (
                HistoryRow(
                    generation=generation,
                    scope="family",
                    family="mmpp",
                    candidate_count=1,
                    valid_count=1,
                    best_fitness=fitness,
                    mean_fitness=fitness,
                    best_identifier=identifier,
                ),
                HistoryRow(
                    generation=generation,
                    scope="overall",
                    family=None,
                    candidate_count=1,
                    valid_count=1,
                    best_fitness=fitness,
                    mean_fitness=fitness,
                    best_identifier=identifier,
                ),
            )
        )

    data = GaFitnessHistoryAspect().calculate(
        _run(history=tuple(rows), experiment=experiment),
        CalculationSettings.default(),
    )

    assert [len(series.x) for series in data.series] == [20_000, 20_000]
    assert [series.sample_count for series in data.series] == [count, count]
    assert data.series[0].x[[0, -1]].tolist() == [0.0, 20_000.0]
    assert data.series[0].y[[0, -1]].tolist() == [0.0, 1.0]
    assert len(cast(Mapping[str, tuple[int, ...]], data.metadata["candidate_counts"])["mmpp"]) == count
    assert len(cast(Mapping[str, tuple[int, ...]], data.metadata["valid_counts"])["overall"]) == count
