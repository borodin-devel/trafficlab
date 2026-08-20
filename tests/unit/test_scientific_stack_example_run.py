"""Durable real full-workflow example-run evidence contracts."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from scripts import check_scientific_stack_example as example_run
from trafficlab.config import ExperimentConfig

_ROOT = Path(__file__).parents[2]
_EVIDENCE = _ROOT / "examples" / "scientific_stack" / "example_run.json"


def test_checked_example_run_recomputes_artifacts_and_result() -> None:
    content = _EVIDENCE.read_bytes()
    evidence = example_run.parse_and_validate_evidence(content, repository_root=_ROOT)

    assert evidence["source"]["commit"] == "a71d74b7b2dc27cea0a9eb00c375e510a0d7acbf"
    assert evidence["execution"]["exit_status"] == 0
    assert evidence["execution"]["target_argv"][-2:] == ["--url", evidence["execution"]["url"]]
    assert evidence["result"]["enabled_families"] == ["poisson_empirical", "markov_renewal", "mmpp"]
    assert evidence["result"]["winner_family"] == "markov_renewal"
    assert evidence["result"]["reference_packet_count"] == 84
    assert evidence["result"]["generated_packet_count"] == 48
    assert evidence["cleanup"] == {
        "containers": [],
        "networks": [],
        "project_name": "trafficlab-capture-7238353bd49c41909a3ed29c3153d281",
        "verified": True,
        "volumes": [],
    }
    assert content == example_run.canonical_json_bytes(evidence)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "exact",
        "command",
        "target_argv",
        "source_tree",
        "config",
        "lock",
        "inventory",
        "artifact",
        "aggregate",
        "cleanup",
    ],
)
def test_example_run_evidence_rejects_fabricated_or_unverifiable_facts(mutation: str) -> None:
    evidence = example_run.parse_and_validate_evidence(_EVIDENCE.read_bytes(), repository_root=_ROOT)
    changed = copy.deepcopy(evidence)
    if mutation == "unknown":
        changed["unknown"] = True
    elif mutation == "exact":
        changed["execution"]["command"] = tuple(changed["execution"]["command"])
    elif mutation == "command":
        changed["execution"]["command"] = ["true"]
    elif mutation == "target_argv":
        changed["execution"]["target_argv"] = ["--url", changed["execution"]["url"]]
    elif mutation == "source_tree":
        changed["source"]["tree"] = "0" * 40
    elif mutation == "config":
        changed["source"]["config_identity"]["sha256"] = "0" * 64
    elif mutation == "lock":
        changed["source"]["uv_lock_identity"]["sha256"] = "0" * 64
    elif mutation == "inventory":
        del changed["artifacts"]["ga_history.csv"]
    elif mutation == "artifact":
        changed["artifacts"]["similarity.json"]["sha256"] = "0" * 64
    elif mutation == "aggregate":
        changed["result"]["aggregate_score"] += 0.01
    else:
        changed["cleanup"]["project_name"] = "trafficlab-capture-00000000000000000000000000000000"

    with pytest.raises(ValueError):
        example_run.validate_evidence(changed, repository_root=_ROOT)


def test_example_run_parser_rejects_invalid_and_noncanonical_json() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        example_run.parse_and_validate_evidence(b"{")
    document = json.loads(_EVIDENCE.read_bytes())
    with pytest.raises(ValueError, match="canonical"):
        example_run.parse_and_validate_evidence(json.dumps(document, indent=2).encode("utf-8"), repository_root=_ROOT)


def test_example_run_validator_rejects_checked_config_policy_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    real_load = example_run.load_experiment

    def changed_load(path: Path) -> ExperimentConfig:
        config = real_load(path)
        if path.resolve() == example_run.CONFIG_PATH.resolve():
            return config.model_copy(
                update={"models": config.models.model_copy(update={"enabled": ("poisson_empirical",)})}
            )
        return config

    monkeypatch.setattr(example_run, "load_experiment", changed_load)
    evidence = json.loads(_EVIDENCE.read_bytes())
    with pytest.raises(ValueError, match="checked configuration"):
        example_run.validate_evidence(evidence, repository_root=_ROOT)


@pytest.mark.parametrize("mutation", ["families", "winner", "completed"])
def test_example_run_derivation_rejects_cross_artifact_contradictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
    copied = tmp_path / "artifacts"
    shutil.copytree(source, copied)
    if mutation == "families":
        monkeypatch.setattr(example_run, "_FAMILIES", ("poisson_empirical",))
    else:
        path = copied / "run.log"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        event = "final_validation_succeeded" if mutation == "winner" else "run_completed"
        record = next(item for item in records if item["event"] == event)
        if mutation == "winner":
            record["family"] = "mmpp"
        else:
            record["reference_packet_count"] += 1
        path.write_bytes(b"".join(example_run.canonical_json_bytes(item) for item in records))

    with pytest.raises(ValueError):
        example_run._derived_result(copied)  # pyright: ignore[reportPrivateUsage]
