from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest

from scripts import run_phase7_study as study
from trafficlab.artifacts import append_run_log
from trafficlab.capture import CaptureResult
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import compare_experiment
from trafficlab.fitting import fit_experiment
from trafficlab.generation import generate_experiment
from trafficlab.genetic.types import METHOD_ORDER
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment
from trafficlab.run import RunDependencies, run_experiment

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_FIT_FIXTURE = _ROOT / "examples" / "data" / "fit"
_CAPTURE_BYTES = (_FIT_FIXTURE / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_FIXTURE / "reference.pcapng").read_bytes()


def test_phase7_extraction_uses_real_three_family_artifacts_fresh_seed_and_lineage(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    study_id = "study-1"
    run_id = "01-short-r1"
    url = "https://downloads.example.test/object.bin"
    workload = study.workload_specs(url)[0]
    (repository_root / "examples" / "phase7" / ".study-work" / "mount" / study_id).mkdir(parents=True)
    config = study.build_base_config(
        workload,
        repository_root=repository_root,
        study_id=study_id,
        url=url,
        capture_image_id=f"sha256:{'d' * 64}",
    )
    experiment_path = repository_root / "runs" / "phase7" / study_id / "realized-configs" / f"{run_id}.toml"
    study._render_realized_config(config, experiment_path)  # pyright: ignore[reportPrivateUsage]

    def capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
        metadata_path = prepared.run_directory / "capture.json"
        reference_path = prepared.run_directory / "reference.pcapng"
        metadata_path.write_bytes(_CAPTURE_BYTES)
        reference_path.write_bytes(_REFERENCE_BYTES)
        inspection = validate_capture_pair(metadata_path, reference_path, deadline=None)
        append_run_log(
            prepared.run_directory,
            {
                "event": "capture_published",
                "packet_count": inspection.packet_count,
                "path": str(reference_path),
                "project_name": "trafficlab-phase7-integration",
                "reused": False,
                "stage": "capture",
            },
        )
        return CaptureResult(prepared.run_directory, reference_path, inspection.packet_count, 0, reused=False)

    result = run_experiment(
        experiment_path,
        dependencies=RunDependencies(
            open_or_prepare_experiment,
            capture,
            fit_experiment,
            generate_experiment,
            compare_experiment,
        ),
    )
    evidence_directory = repository_root / "examples" / "phase7" / ".study-work" / "evidence" / study_id / run_id
    evidence_directory.mkdir(parents=True)
    header_bytes = b"HTTP/1.1 206 Response\r\nContent-Range: bytes 0-262143/4194304\r\nContent-Length: 262144\r\n\r\n"
    header_path = evidence_directory / "short.headers"
    header_path.write_bytes(header_bytes)
    header_path.chmod(0o600)
    transfer_responses: tuple[study.JsonObject, ...] = (
        {
            "transfer_index": 0,
            "requested_start": 0,
            "requested_end": 262143,
            "status": 206,
            "content_length": 262144,
            "content_range": "bytes 0-262143/4194304",
            "header_archive_path": header_path.relative_to(repository_root).as_posix(),
            "header_sha256": hashlib.sha256(header_bytes).hexdigest(),
            "scratch_precreate_mode": 438,
            "archive_mode": 384,
            "inode_preserved": True,
        },
    )
    run_spec = study.StudyRunSpec(
        1,
        run_id,
        "short",
        1,
        experiment_path,
        config.run.directory,
        evidence_directory,
    )

    record = study.extract_primary_record(
        repository_root,
        run_spec,
        workload,
        result,
        1.25,
        transfer_responses,
    )

    assert tuple(item["family"] for item in record.family_champions) == (
        "markov_renewal",
        "mmpp",
        "poisson_empirical",
    )
    assert all(item["selection_seeds"] == (17, 29) for item in record.family_champions)
    held_out = cast(study.JsonObject, study._thaw_json(record.held_out))  # pyright: ignore[reportPrivateUsage]
    held_out_score = cast(study.JsonObject, held_out["score"])
    methods = cast(study.JsonObject, held_out_score["methods"])
    artifact_sha256 = cast(
        study.JsonObject,
        study._thaw_json(record.artifact_sha256),  # pyright: ignore[reportPrivateUsage]
    )
    input_sha256 = result.comparison.input_sha256
    assert input_sha256 is not None
    assert held_out["seed"] == 97
    assert tuple(methods) == METHOD_ORDER
    assert artifact_sha256["capture.json"] == input_sha256["capture_json"]
    assert artifact_sha256["reference.pcapng"] == input_sha256["reference_pcapng"]
    assert artifact_sha256["generated.pcapng"] == input_sha256["generated_pcapng"]
    assert sorted(path.name for path in config.run.directory.iterdir()) == sorted(study.ARTIFACT_NAMES)
