from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import generate_model_fixtures as fixture_generator
from scripts import generate_similarity_fixtures as similarity_fixture_generator
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace

_ROOT = Path(__file__).parents[4]
_MODEL_BYTES = (PIPELINE_FIXTURE_ROOT / "models" / "best_model.json").read_bytes()
_GENERATED_BYTES = (PIPELINE_FIXTURE_ROOT / "models" / "generated.pcapng").read_bytes()


def test_similarity_builder_publishes_the_model_that_owns_generated_bytes(tmp_path: Path) -> None:
    run_directory = similarity_fixture_generator._build_temporary_run(tmp_path)  # pyright: ignore[reportPrivateUsage]

    assert (run_directory / "best_model.json").is_file()
    assert (run_directory / "generated.pcapng").is_file()
    assert (run_directory / "similarity.json").is_file()


def test_normal_mode_writes_both_traffic_model_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping model bytes from the builder would leave best_model.json stale or absent."""
    model_path = tmp_path / "models" / "best_model.json"
    generated_path = tmp_path / "models" / "generated.pcapng"
    monkeypatch.setattr(fixture_generator, "_MODEL_PATH", model_path)
    monkeypatch.setattr(fixture_generator, "_GENERATED_PATH", generated_path)
    monkeypatch.setattr(sys, "argv", ["generate_model_fixtures.py"])

    assert fixture_generator.main() == 0

    assert model_path.read_bytes() == _MODEL_BYTES
    assert generated_path.read_bytes() == _GENERATED_BYTES


def test_check_mode_accepts_both_exact_traffic_model_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The complete fixture gate must accept the exact model and capture pair together."""
    model_path = tmp_path / "models" / "best_model.json"
    generated_path = tmp_path / "models" / "generated.pcapng"
    model_path.parent.mkdir()
    model_path.write_bytes(_MODEL_BYTES)
    generated_path.write_bytes(_GENERATED_BYTES)
    monkeypatch.setattr(fixture_generator, "_MODEL_PATH", model_path)
    monkeypatch.setattr(fixture_generator, "_GENERATED_PATH", generated_path)
    monkeypatch.setattr(sys, "argv", ["generate_model_fixtures.py", "--check"])

    assert fixture_generator.main() == 0


def test_builder_rejects_missing_poisson_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    base = fixture_generator.load_experiment(fixture_generator._EXAMPLE_CONFIG)  # pyright: ignore[reportPrivateUsage]
    models = base.models.model_copy(update={"poisson_empirical": None})
    config = base.model_copy(update={"models": models})

    def load_without_poisson(_path: Path) -> ExperimentConfig:
        return config

    monkeypatch.setattr(fixture_generator, "load_experiment", load_without_poisson)

    with pytest.raises(TrafficlabError, match="Poisson bounds are absent"):
        fixture_generator._build_fixture()  # pyright: ignore[reportPrivateUsage]


def test_builder_translates_parent_fixture_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    real_read = Path.read_bytes

    def fail_capture(path: Path) -> bytes:
        if path.name == "capture.json":
            raise OSError("parent read sentinel")
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", fail_capture)

    with pytest.raises(
        TrafficlabError,
        match="parent canonical-trace and offline-similarity fixture.*parent read sentinel",
    ):
        fixture_generator._build_fixture()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("defect", ["outside-window", "round-trip"])
def test_builder_rejects_invalid_parsed_generated_output(
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    real_parse = fixture_generator.read_pcapng_bytes

    def parse_with_invalid_generated(
        content: bytes,
        metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        if source.name == "reference.pcapng":
            return real_parse(content, metadata, source=source)
        if defect == "outside-window":
            return TrafficTrace.from_events((TraceEvent(11.0, Direction.OUTBOUND, 60),))
        return TrafficTrace.from_events((TraceEvent(0.0, Direction.INBOUND, 60),))

    monkeypatch.setattr(fixture_generator, "read_pcapng_bytes", parse_with_invalid_generated)

    with pytest.raises(TrafficlabError, match="observation window" if defect == "outside-window" else "round-trip"):
        fixture_generator._build_fixture()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("defect", ["missing", "modified"])
def test_check_mode_rejects_a_missing_or_modified_best_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    """Checking only generated.pcapng would accept missing or corrupt fitted-model lineage."""
    model_path = tmp_path / "models" / "best_model.json"
    generated_path = tmp_path / "models" / "generated.pcapng"
    model_path.parent.mkdir()
    generated_path.write_bytes(_GENERATED_BYTES)
    if defect == "modified":
        model_path.write_bytes(
            _MODEL_BYTES.replace(b'"capture_identity":{"sha256":"', b'"capture_identity":{"sha256":"0', 1)
        )
    monkeypatch.setattr(fixture_generator, "_MODEL_PATH", model_path)
    monkeypatch.setattr(fixture_generator, "_GENERATED_PATH", generated_path)
    monkeypatch.setattr(sys, "argv", ["generate_model_fixtures.py", "--check"])

    with pytest.raises(TrafficlabError, match=r"best_model\.json"):
        fixture_generator.main()
