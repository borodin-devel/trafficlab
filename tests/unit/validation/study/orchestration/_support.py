"""Shared typed setup for this decomposed validation suite."""

from __future__ import annotations

import hashlib
import platform
import shutil
import time as time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.common as vs_common
import scripts.validation_study.evidence as vs_evidence
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.records as vs_records
import scripts.validation_study.workloads as vs_workloads
import trafficlab.common.config as trafficlab_common_config
import trafficlab.common.config_io as trafficlab_common_config_io
from tests.support.validation_study.artifacts import write_retained_prerequisite_evidence
from tests.support.validation_study.builders import (
    frozen,
    study_result_value,
    terminal_checkpoint_and_best,
    transfer_responses,
    trial_result,
    valid_prerequisite,
    valid_result_document,
    write_checked_configs,
)
from tests.support.validation_study.constants import CAPTURE_IMAGE_ID, HASH, ROOT
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.trace import Direction, TraceEvent

STUDY_PHASE_CAPTURE_TAG = "trafficlab-validation-study-1:study-capture"

COLLECTION_PHASE_CAPTURE_TAG = "trafficlab-validation-study-1:collection-capture"


def write_collection_compatible_inputs(repository_root: Path) -> Path:
    """Write retained inputs that bind the local revalidation boundary exactly."""

    repository_root.mkdir()
    shutil.copy2(ROOT / "uv.lock", repository_root / "uv.lock")
    prerequisite, _contents = write_checked_configs(repository_root, capture_image_id=CAPTURE_IMAGE_ID)
    tools = cast(vs_common.JsonObject, vs_common.thaw_json(prerequisite.tools))
    tools.update(
        {
            "host_architecture": platform.machine(),
            "kernel_release": platform.release(),
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "uv_lock_sha256": hashlib.sha256((repository_root / "uv.lock").read_bytes()).hexdigest(),
        }
    )
    images = cast(vs_common.JsonObject, vs_common.thaw_json(prerequisite.images))
    images["capture_image_id"] = CAPTURE_IMAGE_ID
    prerequisite = replace(prerequisite, tools=frozen(tools), images=frozen(images))
    prerequisite = write_retained_prerequisite_evidence(repository_root, prerequisite)
    capture_root = repository_root / "docker" / "capture"
    shutil.copy2(ROOT / "docker" / "capture" / "image-lock.json", capture_root / "image-lock.json")
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    prerequisite_path.write_bytes(vs_prereq_codec.render_prerequisite_results(prerequisite))
    return prerequisite_path


def install_primary_orchestration_doubles(
    monkeypatch: pytest.MonkeyPatch,
    expected: vs_records.StudyResults,
    events: list[str],
) -> None:
    records = iter(expected.runs)

    def prepare(
        _root: Path,
        _study_id: str,
        run_id: str,
        _workload: vs_workloads.WorkloadSpec,
    ) -> dict[str, tuple[Path, int]]:
        events.append(f"scratch:{run_id}")
        return {}

    def archive(
        _root: Path,
        _study_id: str,
        run_id: str,
        workload: vs_workloads.WorkloadSpec,
        _prepared: object,
        *,
        object_size_bytes: int,
    ) -> tuple[vs_common.JsonObject, ...]:
        assert object_size_bytes == 4_194_304
        events.append(f"archive:{run_id}")
        return tuple(
            cast(vs_common.JsonObject, value) for value in transfer_responses("study-1", run_id, workload.name)
        )

    def extract(
        _root: Path,
        spec: vs_records.StudyRunSpec,
        _workload: vs_workloads.WorkloadSpec,
        _result: object,
        elapsed: float,
        _responses: tuple[vs_common.JsonObject, ...],
    ) -> vs_records.StudyRunRecord:
        events.append(f"extract:{spec.run_id}:{elapsed}")
        return next(records)

    def load_reference(run_directory: Path) -> tuple[TraceEvent, ...]:
        events.append(f"trace:{run_directory.name}")
        return (TraceEvent(0.0, Direction.OUTBOUND, 60), TraceEvent(1.0, Direction.INBOUND, 80))

    def variation(
        _records: Sequence[vs_records.StudyRunRecord],
        _traces: object,
        _settings: object,
    ) -> tuple[vs_common.FrozenJsonObject, vs_common.FrozenJsonObject, vs_common.FrozenJsonObject]:
        events.append("variation")
        return expected.natural_variation

    def summaries(
        _records: Sequence[vs_records.StudyRunRecord],
    ) -> tuple[vs_common.FrozenJsonObject, vs_common.FrozenJsonObject, vs_common.FrozenJsonObject]:
        events.append("summaries")
        return expected.workload_summaries

    def reproduction(*_args: object, **_kwargs: object) -> vs_records.ReproductionRecord:
        events.append("reproduction")
        return expected.reproduction

    def publish(*_args: object, **_kwargs: object) -> None:
        events.append("publish")

    monkeypatch.setattr(vs_cli, "prepare_transfer_scratch", prepare)
    monkeypatch.setattr(vs_cli, "archive_transfer_evidence", archive)
    monkeypatch.setattr(vs_cli, "extract_primary_record", extract)
    monkeypatch.setattr(vs_cli, "load_reference_trace", load_reference, raising=False)
    monkeypatch.setattr(vs_cli, "natural_variation", variation)
    monkeypatch.setattr(vs_cli, "workload_summaries", summaries)
    monkeypatch.setattr(vs_cli, "run_cli_reproduction", reproduction, raising=False)
    monkeypatch.setattr(vs_cli, "publish_results", publish)
    monkeypatch.setattr(platform, "python_version", lambda: "3.12.3")
    monkeypatch.setattr(platform, "platform", lambda: "Linux-test")


def source_record_and_config(
    repository_root: Path,
) -> tuple[vs_records.StudyRunRecord, trafficlab_common_config.ExperimentConfig, vs_workloads.WorkloadSpec]:
    document = valid_result_document(repository_root)
    source = study_result_value(document).runs[3]
    workload = {item.name: item for item in vs_workloads.workload_specs(valid_prerequisite().url)}["streaming"]
    base = vs_workloads.build_base_config(
        workload,
        repository_root=repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    source_directory = repository_root / source.run_directory
    source_config = base.model_copy(
        update={"run": base.run.model_copy(update={"directory": source_directory.resolve()})}
    )
    source_directory.mkdir(parents=True)
    (source_directory / "experiment.toml").write_bytes(
        trafficlab_common_config_io.render_effective_config(source_config)
    )
    return source, base, workload


def reject_direct_reproduction_mutation(mutation: str, repository_root: Path) -> bool:
    if mutation == "reused-log":
        with pytest.raises(ValueError, match="reused"):
            vs_evidence.fresh_run_log_proofs(
                (
                    {"event": "capture_published", "stage": "capture", "reused": False},
                    {"event": "best_model_reused"},
                    {"event": "comparison_succeeded", "reused": False},
                    {"event": "run_completed"},
                )
            )
        return True
    if mutation == "evaluate-final-count":
        with pytest.raises(ValueError, match="exactly one"):
            vs_evidence.sole_final_trial((trial_result(97, 0.5), trial_result(97, 0.5)))
        return True
    if mutation == "unbound-published-comparison":
        _state, _best, comparison = terminal_checkpoint_and_best(repository_root)
        with pytest.raises(ValueError, match="lineage"):
            vs_evidence._require_published_lineage(  # pyright: ignore[reportPrivateUsage]
                comparison,
                comparison,
                {"capture.json": b"capture", "reference.pcapng": b"reference", "generated.pcapng": b"generated"},
                ContentIdentity(size=1, sha256=HASH),
            )
        return True
    return False
