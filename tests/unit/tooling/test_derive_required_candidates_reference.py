"""Tests for reproducible development-reference derivation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.derive_required_candidates_reference import (
    ToolPaths,
    derive_required_candidates,
)


@dataclass(frozen=True)
class _Inspection:
    packet_count: int
    first_timestamp: float = 10.0
    last_timestamp: float = 13.5


def test_derivation_orders_range_validates_and_publishes_provenance_atomically(tmp_path: Path) -> None:
    source = tmp_path / "source.pcapng"
    metadata = tmp_path / "capture.json"
    output = tmp_path / "derived"
    source_bytes = b"source-pcapng"
    metadata_bytes = b'{\n  "interface": "eth0",\n  "target_mac": "02:00:00:00:00:01"\n}\n'
    source.write_bytes(source_bytes)
    metadata.write_bytes(metadata_bytes)
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...]) -> None:
        calls.append(command)
        destination = command[-2] if command[0] == "editcap" else command[-1]
        Path(destination).write_bytes(b"converted" if command[0] == "editcap" else b"ordered")

    result = derive_required_candidates(
        source,
        metadata,
        packet_limit=256,
        output=output,
        tools=ToolPaths("editcap", "reordercap"),
        run=run,
        versions=lambda _tool: "Wireshark 4.2.2",
        validate=lambda _metadata, _pcapng: _Inspection(256),
    )

    assert calls == [
        ("editcap", "-r", str(source), str(result.staged_extracted), "1-256"),
        ("reordercap", str(result.staged_extracted), str(result.staged_ordered)),
    ]
    assert (output / "reference.pcapng").read_bytes() == b"ordered"
    assert (output / "capture.json").read_bytes() == metadata_bytes
    document = json.loads((output / "manifest.json").read_text())
    assert document["source"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert document["capture"]["sha256"] == hashlib.sha256(metadata_bytes).hexdigest()
    assert document["output"]["sha256"] == hashlib.sha256(b"ordered").hexdigest()
    assert document["tools"] == {"editcap": "Wireshark 4.2.2", "reordercap": "Wireshark 4.2.2"}
    assert document["packet_count"] == 256
    assert document["W"] == 3.5
    assert {path.name for path in output.iterdir()} == {"reference.pcapng", "capture.json", "manifest.json"}
    assert source.read_bytes() == source_bytes
    assert metadata.read_bytes() == metadata_bytes


def test_derivation_refuses_existing_output_without_running_tools(tmp_path: Path) -> None:
    source = tmp_path / "source.pcapng"
    metadata = tmp_path / "capture.json"
    output = tmp_path / "derived"
    source.write_bytes(b"source")
    metadata.write_bytes(b"metadata")
    output.mkdir()
    sentinel = output / "reference.pcapng"
    sentinel.write_bytes(b"existing")
    calls: list[tuple[str, ...]] = []

    with pytest.raises(FileExistsError):
        derive_required_candidates(
            source,
            metadata,
            packet_limit=256,
            output=output,
            tools=ToolPaths("editcap", "reordercap"),
            run=calls.append,
            versions=lambda _tool: "version",
            validate=lambda _metadata, _pcapng: _Inspection(256),
        )

    assert calls == []
    assert sentinel.read_bytes() == b"existing"


def test_manifest_and_pair_are_not_published_when_atomic_directory_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import derive_required_candidates_reference as tool

    source = tmp_path / "source.pcapng"
    metadata = tmp_path / "capture.json"
    output = tmp_path / "derived"
    source.write_bytes(b"source")
    metadata.write_bytes(b'{\n  "interface": "eth0",\n  "target_mac": "02:00:00:00:00:01"\n}\n')

    def run(command: tuple[str, ...]) -> None:
        destination = command[-2] if command[0] == "editcap" else command[-1]
        Path(destination).write_bytes(b"converted" if command[0] == "editcap" else b"ordered")

    def fail_publish(_stage: Path, _destination: Path) -> None:
        raise OSError("publication interrupted")

    monkeypatch.setattr(tool, "_publish_directory_no_replace", fail_publish)
    with pytest.raises(OSError, match="publication interrupted"):
        tool.derive_required_candidates(
            source,
            metadata,
            packet_limit=256,
            output=output,
            tools=ToolPaths("editcap", "reordercap"),
            run=run,
            versions=lambda _tool: "version",
            validate=lambda _metadata, _pcapng: _Inspection(256),
        )
    assert not output.exists()
    assert not tuple(tmp_path.glob(".derived.*"))


@pytest.mark.parametrize(
    ("name", "packet_limit", "population", "generations", "seeds"),
    (
        ("small", 256, 8, 1, [17]),
        ("medium", 512, 12, 3, [17, 29]),
        ("big", None, 21, 10, [17, 29, 43]),
    ),
)
def test_profiles_are_exact_and_strict(
    name: str, packet_limit: int | None, population: int, generations: int, seeds: list[int]
) -> None:
    import tomllib

    from trafficlab.common.config import ExperimentConfig

    document = tomllib.loads((Path("examples/required_candidates") / f"{name}.toml").read_text())
    config = ExperimentConfig.model_validate(document)
    assert config.genetic.population_size == population
    assert config.genetic.generation_count == generations
    assert list(config.genetic.trial_seeds) == seeds
    assert config.genetic.early_stopping_generations <= generations
    assert config.genetic.early_stopping_tolerance >= 0.0
    if packet_limit is not None:
        assert config.generation.trial.max_packets >= packet_limit
    assert tuple(config.models.enabled) == (
        "poisson_empirical",
        "markov_renewal",
        "mmpp",
        "nhpp",
        "acd",
        "markov_packet_train",
        "packet_hmm",
    )
