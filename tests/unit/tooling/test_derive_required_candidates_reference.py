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
from trafficlab.common.json import render_json_document


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
        ("editcap", "-r", str(result.source_snapshot), str(result.staged_extracted), "1-256"),
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
    assert document["range"] == {"first_packet": 1, "last_packet": 256}
    assert (output / "manifest.json").read_bytes() == render_json_document(document)
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


def test_derivation_uses_same_parent_snapshots_when_original_inputs_change(tmp_path: Path) -> None:
    source = tmp_path / "source.pcapng"
    metadata = tmp_path / "capture.json"
    output = tmp_path / "derived"
    source_bytes = b"original-source"
    metadata_bytes = b'{\n  "interface": "eth0",\n  "target_mac": "02:00:00:00:00:01"\n}\n'
    source.write_bytes(source_bytes)
    metadata.write_bytes(metadata_bytes)
    seen: list[tuple[Path, Path]] = []
    calls = 0

    def run(command: tuple[str, ...]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            source.write_bytes(b"replaced-source")
            metadata.write_bytes(b"replaced-metadata")
        input_path = Path(command[2] if command[0] == "editcap" else command[1])
        destination = Path(command[-2] if command[0] == "editcap" else command[-1])
        destination.write_bytes(input_path.read_bytes())

    def validate(snapshot_metadata: Path, snapshot_pcapng: Path) -> _Inspection:
        seen.append((snapshot_metadata, snapshot_pcapng))
        assert snapshot_metadata != metadata
        assert snapshot_pcapng != source
        return _Inspection(256)

    result = derive_required_candidates(
        source,
        metadata,
        packet_limit=256,
        output=output,
        tools=ToolPaths("editcap", "reordercap"),
        run=run,
        versions=lambda _tool: "version",
        validate=validate,
    )

    assert result.source_snapshot.parent.parent == result.output.parent
    assert (output / "reference.pcapng").read_bytes() == source_bytes
    assert (output / "capture.json").read_bytes() == metadata_bytes
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["source"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert manifest["source"]["sha256"] != hashlib.sha256(b"replaced-source").hexdigest()
    assert manifest["output"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert source.read_bytes() == b"replaced-source"
    assert metadata.read_bytes() == b"replaced-metadata"
    assert len(seen) == 1


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
    assert config.genetic.early_stopping_generations == {"small": 0, "medium": 2, "big": 3}[name]
    assert config.genetic.early_stopping_tolerance == {"small": 0.0, "medium": 0.0001, "big": 0.0001}[name]
    assert config.generation.trial.max_packets == {"small": 256, "medium": 512, "big": 10000}[name]
    assert config.generation.trial.max_output_bytes == {"small": 1000000, "medium": 1000000, "big": 10000000}[name]
    assert config.generation.trial.max_wall_seconds == {"small": 5.0, "medium": 10.0, "big": 30.0}[name]
    assert config.generation.final.max_packets == {"small": 1000, "medium": 1500, "big": 10000}[name]
    assert config.generation.final.max_output_bytes == {"small": 2000000, "medium": 3000000, "big": 20000000}[name]
    assert config.generation.final.max_wall_seconds == {"small": 10.0, "medium": 30.0, "big": 120.0}[name]
    assert tuple(config.models.enabled) == (
        "poisson_empirical",
        "markov_renewal",
        "mmpp",
        "nhpp",
        "acd",
        "markov_packet_train",
        "packet_hmm",
    )
