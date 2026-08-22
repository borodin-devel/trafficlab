from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import trafficlab.cli as cli_module
import trafficlab.generation.stage as generation_module
from tests.support.generation import (
    CAPTURE_BYTES,
    MODEL_BYTES,
    expected_scapy_final_content,
    log_records,
    prepare_stage_run,
)
from tests.support.scapy_fixtures import encode_precise_events
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    Direction,
    TraceEvent,
    TrafficTrace,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.generation.models.common import GenerationResult, ModelFamily
from trafficlab.generation.models.fitted_model import (
    BestModel,
    load_best_model,
    make_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.generation.models.registry import (
    get_family,
)
from trafficlab.generation.stage import generate_experiment
from trafficlab.preflight.types import PreparedExperiment

pytestmark = pytest.mark.integration


def test_stage_uses_authoritative_preparation_single_read_lineage_and_no_reference_open(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bypassing preparation or reopening lineage paths could generate from a non-authoritative snapshot."""
    experiment_path, run_directory, config = prepare_stage_run(valid_config_data, tmp_path)
    best_path = run_directory / "best_model.json"
    capture_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    real_prepare = generation_module.open_or_prepare_experiment
    real_read_bytes = Path.read_bytes
    real_open = Path.open
    prepared_calls: list[Path] = []
    reads = {best_path: 0, capture_path: 0}

    def observe_prepare(path: Path) -> PreparedExperiment:
        prepared_calls.append(path)
        return real_prepare(path)

    def count_input_reads(path: Path) -> bytes:
        if path in reads:
            reads[path] += 1
        return real_read_bytes(path)

    def reject_reference_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == reference_path:
            raise AssertionError("generation opened reference.pcapng")
        return cast(Any, real_open(path, *args, **kwargs))

    monkeypatch.setattr(generation_module, "open_or_prepare_experiment", observe_prepare)
    monkeypatch.setattr(Path, "read_bytes", count_input_reads)
    monkeypatch.setattr(Path, "open", reject_reference_open)

    result = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert prepared_calls == [experiment_path]
    assert reads == {best_path: 1, capture_path: 1}
    assert result.run_directory == run_directory
    assert result.seed == config.run.final_seed
    assert result.observation_window_seconds == 10.0
    assert result.generated_path == run_directory / "generated.pcapng"
    assert result.reused is False
    assert result.trace == read_pcapng_bytes(
        result.generated_path.read_bytes(),
        parse_capture_metadata(CAPTURE_BYTES, source=capture_path),
        source=result.generated_path,
    )
    assert not reference_path.exists()


def test_stage_hashes_parses_and_rechecks_the_same_model_and_capture_bytes(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing and parsing use cached bytes, then publication rechecks their authoritative paths."""
    experiment_path, _run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    model_seen: list[bytes] = []
    capture_seen: list[bytes] = []
    real_load = load_best_model
    real_parse = parse_capture_metadata

    def observe_model(content: bytes, *, source: Path) -> BestModel:
        model_seen.append(content)
        return real_load(content, source=source)

    def observe_capture(content: bytes, *, source: Path) -> CaptureMetadata:
        capture_seen.append(content)
        return real_parse(content, source=source)

    monkeypatch.setattr(generation_module, "load_best_model", observe_model)
    monkeypatch.setattr(generation_module, "parse_capture_metadata", observe_capture)

    result = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert model_seen == [MODEL_BYTES]
    assert capture_seen == [CAPTURE_BYTES]
    assert len(result.trace)
    assert (
        hashlib.sha256(capture_seen[0]).hexdigest()
        == load_best_model(model_seen[0], source=Path("observed-best_model.json")).capture_sha256
    )


def test_stage_uses_only_stored_family_window_and_configured_final_seed_and_limits(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refitting, recomputing W, or using trial settings would make final output diverge from the winning model."""
    experiment_path, _run_directory, config = prepare_stage_run(valid_config_data, tmp_path)
    best = load_best_model(MODEL_BYTES, source=Path("best_model.json"))
    real_family = get_family(best.family)
    calls: list[tuple[object, int, float, object, object]] = []

    def observe_generate(
        model: object,
        seed: int,
        W: float,
        limits: object,
        *,
        clock: Callable[[], float],
    ) -> GenerationResult:
        calls.append((model, seed, W, limits, clock))
        return real_family.generate(runtime_fitted_model(best), seed, W, config.generation.final, clock=clock)

    observed_family = cast(
        ModelFamily,
        SimpleNamespace(
            name=real_family.name,
            gene_names=real_family.gene_names,
            generate=observe_generate,
        ),
    )

    def observed_get_family(name: str) -> ModelFamily:
        assert name == best.family
        return observed_family

    monkeypatch.setattr(generation_module, "get_family", observed_get_family)

    def supplied_clock() -> float:
        return 0.0

    result = generate_experiment(experiment_path, clock=supplied_clock)

    assert result.observation_window_seconds == best.observation_window_seconds
    assert calls == [
        (
            runtime_fitted_model(best),
            config.run.final_seed,
            best.observation_window_seconds,
            config.generation.final,
            supplied_clock,
        )
    ]


def test_stage_keeps_a_binary_resolution_endpoint_inside_its_stored_window(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nearest-nanosecond rendering must not move a valid binary-derived endpoint beyond stored W."""
    experiment_path, run_directory, config = prepare_stage_run(valid_config_data, tmp_path)
    metadata = parse_capture_metadata(CAPTURE_BYTES, source=run_directory / "capture.json")
    binary_reference = encode_precise_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(3 / 1024, Direction.INBOUND, 80),
        ),
        metadata,
        resolution=0x8A,
    )
    parsed_reference = read_pcapng_bytes(binary_reference, metadata, source=Path("binary-reference.pcapng"))
    reference, window = normalize_reference(parsed_reference)
    assert window == 3 / 1024

    family = get_family("poisson_empirical")
    bounds = config.models.poisson_empirical
    assert bounds is not None
    artifact = make_best_model(
        family,
        reference,
        (1.0,),
        reference_identity=identify_bytes(binary_reference),
        capture_identity=identify_bytes(CAPTURE_BYTES),
        final_seed=config.run.final_seed,
        final_limits=config.generation.final,
        W=window,
        bounds=bounds,
    )
    (run_directory / "best_model.json").write_bytes(render_best_model(artifact))

    def generate_endpoint(
        _model: object,
        _seed: int,
        W: float,
        _limits: object,
        *,
        clock: Callable[[], float],
    ) -> GenerationResult:
        assert clock() == 0.0
        return GenerationResult(
            True,
            TrafficTrace.from_events(
                (
                    TraceEvent(0.0, Direction.OUTBOUND, 60),
                    TraceEvent(W, Direction.INBOUND, 80),
                )
            ),
        )

    endpoint_family = cast(
        ModelFamily,
        SimpleNamespace(name=family.name, gene_names=family.gene_names, generate=generate_endpoint),
    )

    def get_endpoint_family(_name: str) -> ModelFamily:
        return endpoint_family

    monkeypatch.setattr(generation_module, "get_family", get_endpoint_family)

    result = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert result.observation_window_seconds == window
    assert result.trace[-1].timestamp == 0.002929
    assert all(0.0 <= timestamp <= window for timestamp in result.trace.timestamps)


def test_stage_success_logs_exact_publication_and_reuse_records(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    experiment_path, run_directory, config = prepare_stage_run(valid_config_data, tmp_path)

    published = generate_experiment(experiment_path, clock=lambda: 0.0)
    reused = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert published.reused is False
    assert reused.reused is True
    common = {
        "observation_window_seconds": 10.0,
        "packet_count": len(published.trace),
        "path": str(run_directory / "generated.pcapng"),
        "seed": config.run.final_seed,
        "stage": "generate",
    }
    assert log_records(run_directory)[-2:] == [
        {"event": "generated_pcapng_published", **common},
        {"event": "generated_pcapng_reused", **common},
    ]


def test_cli_generated_capture_matches_scapy_output_and_final_settings(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The public command and fixture generator must reproduce one byte-stable final-seed capture."""
    experiment_path, run_directory, config = prepare_stage_run(valid_config_data, tmp_path)
    assert cli_module.main(["generate", str(experiment_path)]) == 0

    captured = capsys.readouterr()
    generated_content = (run_directory / "generated.pcapng").read_bytes()
    metadata = parse_capture_metadata(CAPTURE_BYTES, source=run_directory / "capture.json")
    parsed = read_pcapng_bytes(generated_content, metadata, source=run_directory / "generated.pcapng")
    best = load_best_model(MODEL_BYTES, source=run_directory / "best_model.json")

    assert generated_content == expected_scapy_final_content(config)
    assert parsed
    assert all(0.0 <= event.timestamp <= best.observation_window_seconds for event in parsed)
    assert captured.out == f"generate: packets={len(parsed)} output={run_directory / 'generated.pcapng'}\n"
    assert captured.err == ""


def test_installed_generate_reproduces_checked_fixture_from_isolated_working_directory(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    """The installed entry point must not depend on a source checkout import or a process-only test injection."""
    experiment_path, run_directory, config = prepare_stage_run(valid_config_data, tmp_path)
    working_directory = tmp_path / "installed-entry-cwd"
    source_shadow = working_directory / "src" / "trafficlab"
    source_shadow.mkdir(parents=True)
    (source_shadow / "__init__.py").write_text(
        'raise RuntimeError("installed entry point imported a working-directory source shadow")\n',
        encoding="utf-8",
    )
    installed_script = Path(sys.executable).with_name("trafficlab")
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}

    completed = subprocess.run(
        [str(installed_script), "generate", str(experiment_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=working_directory,
        env=environment,
    )

    generated = (run_directory / "generated.pcapng").read_bytes()
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"generate: packets=3 output={run_directory / 'generated.pcapng'}\n"
    assert completed.stderr == ""
    assert generated == expected_scapy_final_content(config)
