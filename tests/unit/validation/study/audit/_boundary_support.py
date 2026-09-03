"""Shared typed setup for this decomposed validation suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import scripts.validation_study.common as vs_common
from tests.support.validation_study.artifacts import (
    candidate_index,
    rewrite_candidate_manifest,
    write_candidate_index,
    write_canonical_json,
)
from trafficlab.common.compatibility import identify_bytes


def write_candidate_lifecycle(candidate: Path) -> dict[str, object]:
    """Add the complete collection-cleanup contract without calling the producer."""

    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_bytes()))
    environment = cast(dict[str, object], json.loads((candidate / "environment.json").read_bytes()))
    study_id = cast(str, protocol["study_id"])

    def row(relative: str, run_id: str) -> dict[str, object]:
        records = [json.loads(line) for line in (candidate / relative / "run.log").read_bytes().splitlines()]
        capture = next(record for record in records if record["event"] == "capture_published")
        project_name = capture["project_name"]
        assert isinstance(project_name, str)
        return {
            "cleanup_verified": True,
            "directory": relative,
            "project_name": project_name,
            "run_id": run_id,
        }

    lifecycle: dict[str, object] = {
        "held_out": [
            row(f"held_out/{workload}", f"held-out-{workload}") for workload in ("short", "streaming", "bursty")
        ],
        "phase_capture_image": {
            "capture_image_id": environment["capture_image_id"],
            "cleanup_verified": True,
            "post_cleanup_inspect_exit_status": 1,
            "tag": f"trafficlab-validation-{study_id}:collection-capture",
        },
        "schema_version": 1,
        "study_id": study_id,
        "training": [
            row(f"training/{workload}/r{repeat}", run_id)
            for _order, run_id, workload, repeat in vs_common.PRIMARY_ORDER
        ],
    }
    write_canonical_json(candidate / "lifecycle.json", lifecycle)
    index = candidate_index(candidate)
    ownership = cast(dict[str, str], index["ownership"])
    lineage = cast(dict[str, object], index["lineage"])
    ownership["lifecycle.json"] = "study-lifecycle"
    lineage["lifecycle.json"] = {"relation": "lifecycle"}
    index["lifecycle"] = "lifecycle.json"
    index["ownership"] = ownership
    index["lineage"] = lineage
    index["schema_version"] = 5
    write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)
    return lifecycle


def rewrite_training_configuration_identities(candidate: Path, *, workload: str, repeat: int) -> None:
    """Keep an intentional configuration mutation internally self-consistent."""

    index = candidate_index(candidate)
    record = next(
        item
        for item in cast(list[dict[str, object]], index["training"])
        if item["workload"] == workload and item["repeat"] == repeat
    )
    directory = f"training/{workload}/r{repeat}"
    for field, relative in (
        ("portable_config_identity", f"configs/training-{workload}-r{repeat}.portable.toml"),
        ("realized_config_identity", f"configs/training-{workload}-r{repeat}.realized.toml"),
        ("run_config_identity", f"{directory}/experiment.toml"),
    ):
        record[field] = identify_bytes((candidate / relative).read_bytes()).as_dict()
    checkpoint_path = candidate / directory / "checkpoint.json"
    checkpoint = cast(dict[str, object], json.loads(checkpoint_path.read_bytes()))
    checkpoint["experiment_identity"] = identify_bytes(
        (candidate / directory / "experiment.toml").read_bytes()
    ).as_dict()
    write_canonical_json(checkpoint_path, checkpoint)
    write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)
