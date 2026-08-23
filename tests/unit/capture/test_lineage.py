import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import tomli_w

import trafficlab.artifacts.capture as artifact_module
import trafficlab.capture.lineage as lineage_module
import trafficlab.capture.stage as capture_module
import trafficlab.common.compatibility as compatibility
from tests.support.capture import Clock, DockerDouble, prepared_capture, seed_capture_lineage
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.capture.docker.types import CommandResult
from trafficlab.capture.stage import capture_experiment, capture_prepared_experiment
from trafficlab.common.compatibility import identify_file
from trafficlab.common.config import MountConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata
from trafficlab.preflight.stage import open_or_prepare_experiment
from trafficlab.preflight.types import (
    CaptureEnvironmentIdentity,
    MountedInputIdentity,
)


def test_pre_workload_reuse_preserves_the_preexisting_reference_pair_without_cleanup(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid existing pair is reused before any Docker project exists or needs cleanup."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_bytes = render_capture_metadata(metadata)
    pcapng_bytes = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    (prepared.run_directory / "capture.json").write_bytes(metadata_bytes)
    (prepared.run_directory / "reference.pcapng").write_bytes(pcapng_bytes)
    seed_capture_lineage(prepared)
    docker = DockerDouble("cleanup_failure")

    result = capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert result.reused is True
    assert docker.calls == []
    assert (prepared.run_directory / "capture.json").read_bytes() == metadata_bytes
    assert (prepared.run_directory / "reference.pcapng").read_bytes() == pcapng_bytes


def test_public_capture_reuses_a_locally_validated_pair_without_docker(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """The public capture boundary must not run full Docker preflight for exact reuse."""
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    prepared = open_or_prepare_experiment(experiment_path)
    prepared = replace(
        prepared,
        report=replace(
            prepared.report,
            environment_identity=CaptureEnvironmentIdentity(
                host_architecture="linux/amd64",
                target_reference=prepared.config.target.image,
                target_content_id="sha256:" + ("c" * 64),
                capture_reference=prepared.config.capture.image,
                capture_content_id=("sha256:704e90f23055657bb8ad7108bf6650b5e83fb2b711a1168725441599b8a73859"),
                capture_tool_version="4.0.17",
            ),
        ),
    )
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    seed_capture_lineage(prepared)

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"exact public reuse touched Docker operation {name}")

    result = capture_experiment(experiment_path, docker=cast(Any, NoDocker()), clock=lambda: 100.0)

    assert result.reused is True
    assert result.packet_count == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("capture_content_id", "sha256:" + ("d" * 64)),
        ("capture_tool_version", "4.0.18"),
    ],
)
def test_public_capture_reuse_rejects_a_valid_format_environment_that_differs_from_the_checked_lock(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    """Config-only reuse must bind the recorded capture identity to the checked lock before Docker."""
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    prepared = open_or_prepare_experiment(experiment_path)
    prepared = replace(
        prepared,
        report=replace(
            prepared.report,
            environment_identity=CaptureEnvironmentIdentity(
                host_architecture="linux/amd64",
                target_reference=prepared.config.target.image,
                target_content_id="sha256:" + ("c" * 64),
                capture_reference=prepared.config.capture.image,
                capture_content_id="sha256:704e90f23055657bb8ad7108bf6650b5e83fb2b711a1168725441599b8a73859",
                capture_tool_version="4.0.17",
            ),
        ),
    )
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    seed_capture_lineage(prepared)
    pair_before = {name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")}
    log_path = prepared.run_directory / "run.log"
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    publication = next(record for record in records if record["event"] == "capture_published")
    environment = cast(dict[str, object], publication["capture_environment_identity"])
    environment[field] = replacement
    log_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"lock-incompatible public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=cast(Any, NoDocker()), clock=lambda: 100.0)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "affected_evidence": "capture pair",
        "authority": "primary",
        "corrective_action": "select its matching run or a new run directory",
        "detail": "capture pair has another identity",
        "evidence_state": "preserved",
        "kind": "artifact_stale",
        "stage": "capture",
    }
    assert {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    } == pair_before


def test_capture_lineage_persists_ordered_read_only_file_and_directory_identities(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "request.txt"
    second = tmp_path / "settings.json"
    directory = tmp_path / "directory-input"
    first.write_bytes(b"request-v1")
    second.write_bytes(b'{"value":1}\n')
    directory.mkdir()
    (directory / "nested.txt").write_bytes(b"nested-input")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(first), "target": "/work/request.txt", "read_only": True},
        {"source": str(directory), "target": "/work/directory-input", "read_only": True},
        {"source": str(second), "target": "/work/settings.json", "read_only": False},
    ]
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("normal")

    capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    publication = next(record for record in records if record["event"] == "capture_published")
    environment = cast(dict[str, object], publication["capture_environment_identity"])
    assert environment["mounted_inputs"] == [
        {
            "read_only": True,
            "sha256": identify_file(first).sha256,
            "size": len(b"request-v1"),
            "target": "/work/request.txt",
        },
        {
            "read_only": True,
            "sha256": compatibility.identify_directory(directory).sha256,
            "size": len(b"nested-input"),
            "target": "/work/directory-input",
        },
    ]


def test_public_capture_reuse_reidentifies_mounted_file_bytes_before_docker(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    seed_capture_lineage(prepared)
    mounted.write_bytes(b"request-v2")

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"stale public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=cast(Any, NoDocker()), clock=lambda: 100.0)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": "restore the declared mounted-input content identity",
        "detail": "mounted input request.txt is incompatible",
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }


def test_mounted_input_comparison_rejects_changed_read_only_directory_bytes(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "directory-input"
    directory.mkdir()
    payload = directory / "request.txt"
    payload.write_bytes(b"request-v1")
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mount = MountConfig(source=directory, target="/work/input", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    expected = lineage_module.identify_mounted_inputs(config)
    payload.write_bytes(b"request-v2")

    with pytest.raises(TrafficlabError, match="mounted input input is incompatible"):
        cast(Any, lineage_module)._require_matching_mounted_inputs(config, expected)


def test_prepared_capture_reuse_reidentifies_an_unavailable_mounted_file(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    seed_capture_lineage(prepared)
    mounted.unlink()

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"unavailable reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture_prepared_experiment(
            experiment_path,
            prepared,
            docker=cast(Any, NoDocker()),
            clock=lambda: 100.0,
        )

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": "restore the named mounted input bytes",
        "detail": "mounted input request.txt is unavailable",
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }
    assert (prepared.run_directory / "capture.json").exists()
    assert (prepared.run_directory / "reference.pcapng").exists()


def test_capture_reuse_rejects_a_valid_pair_bound_to_another_environment(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parseable pair from another image/tool identity is stale, not reusable."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    first_docker = DockerDouble("normal")
    first = capture_prepared_experiment(
        experiment_path,
        prepared,
        docker=first_docker,
        clock=Clock(first_docker),
        interruption=lambda: False,
    )
    assert first.reused is False
    before = {name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")}
    original_identity = prepared.report.environment_identity
    assert original_identity is not None
    incompatible = replace(
        prepared,
        report=replace(
            prepared.report,
            environment_identity=replace(original_identity, capture_tool_version="4.0.18"),
        ),
    )
    second_docker = DockerDouble("normal")

    with pytest.raises(TrafficlabError) as caught:
        capture_prepared_experiment(
            experiment_path,
            incompatible,
            docker=second_docker,
            clock=Clock(second_docker),
            interruption=lambda: False,
        )

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
        outcome.status,
    ) == (
        "artifact_stale",
        "capture",
        "capture pair has another identity",
        "capture pair",
        "preserved",
        "select its matching run or a new run directory",
        "primary",
        None,
    )
    assert second_docker.calls == []
    assert {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    } == before


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "malformed",
        "content-mismatch",
        "environment-not-object",
        "environment-fields",
        "environment-string",
        "environment-empty-string",
        "environment-platform",
        "environment-content-id",
        "environment-capture-content-id",
        "target-reference",
        "capture-reference",
        "mounted-inputs-not-array",
        "mounted-input-fields",
        "log-record-not-object",
    ],
)
def test_capture_reuse_rejects_missing_or_invalid_lineage_before_boundaries(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    first_docker = DockerDouble("normal")
    capture_prepared_experiment(
        experiment_path,
        prepared,
        docker=first_docker,
        clock=Clock(first_docker),
        interruption=lambda: False,
    )
    pair_before = {name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")}
    log_path = prepared.run_directory / "run.log"
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    publication = next(record for record in records if record["event"] == "capture_published")
    if corruption == "missing":
        records.remove(publication)
    elif corruption == "malformed":
        publication["capture_identity"] = {"size": "wrong", "sha256": "0" * 64}
    elif corruption == "content-mismatch":
        publication["reference_identity"] = {"size": 1, "sha256": "0" * 64}
    elif corruption == "environment-not-object":
        publication["capture_environment_identity"] = []
    elif corruption == "environment-fields":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        del environment["capture_tool_version"]
    elif corruption == "environment-string":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["target_reference"] = 1
    elif corruption == "environment-empty-string":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["capture_tool_version"] = " "
    elif corruption == "environment-platform":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["host_architecture"] = "linux/arm64"
    elif corruption == "environment-content-id":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["target_content_id"] = "mutable-tag"
    elif corruption == "environment-capture-content-id":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["capture_content_id"] = "mutable-tag"
    elif corruption == "target-reference":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["target_reference"] = "example.invalid/other:tag"
    elif corruption == "capture-reference":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["capture_reference"] = "example.invalid/other:tag"
    elif corruption == "mounted-inputs-not-array":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["mounted_inputs"] = {}
    elif corruption == "mounted-input-fields":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["mounted_inputs"] = [{"target": "/work/request.txt"}]
    else:
        records.append([])
    log_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"invalid lineage touched Docker operation {name}")

    def reject_clock() -> float:
        raise AssertionError("invalid lineage touched the clock")

    with pytest.raises(TrafficlabError) as caught:
        capture_prepared_experiment(
            experiment_path,
            prepared,
            docker=cast(Any, NoDocker()),
            clock=reject_clock,
        )

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
        outcome.status,
    ) == (
        "artifact_stale",
        "capture",
        "capture pair has another identity",
        "capture pair",
        "preserved",
        "select its matching run or a new run directory",
        "primary",
        None,
    )
    assert {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    } == pair_before


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ([], "object"),
        ({"target": "/work/request.txt"}, "fields"),
        ({"target": 1, "read_only": True, "size": 1, "sha256": "0" * 64}, "target"),
        ({"target": " ", "read_only": True, "size": 1, "sha256": "0" * 64}, "target"),
        ({"target": "request.txt", "read_only": True, "size": 1, "sha256": "0" * 64}, "target"),
        ({"target": "/work/request.txt", "read_only": 1, "size": 1, "sha256": "0" * 64}, "read_only"),
    ],
)
def test_mounted_input_identity_strictly_rejects_noncanonical_records(value: object, error: str) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        MountedInputIdentity.from_dict(value)


@pytest.mark.parametrize("mounted_inputs", [[], (object(),)])
def test_capture_environment_identity_rejects_untyped_mounted_inputs(mounted_inputs: object) -> None:
    with pytest.raises(TypeError, match="mounted_inputs"):
        CaptureEnvironmentIdentity(
            host_architecture="linux/amd64",
            target_reference="example.invalid/target:tag",
            target_content_id="sha256:" + ("1" * 64),
            capture_reference="example.invalid/capture:tag",
            capture_content_id="sha256:" + ("2" * 64),
            capture_tool_version="4.0.17",
            mounted_inputs=cast(Any, mounted_inputs),
        )


@pytest.mark.parametrize("replacement", ["missing", "directory"])
def test_mounted_input_identification_classifies_a_race_at_the_stable_file_boundary(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounted = tmp_path / "racy-request.txt"
    mounted.write_bytes(b"request-v1")
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    real_identify = lineage_module.identify_file

    def replace_before_identification(path: Path) -> object:
        path.unlink()
        if replacement == "directory":
            path.mkdir()
        return real_identify(path)

    monkeypatch.setattr(lineage_module, "identify_file", replace_before_identification)

    expected = "unavailable" if replacement == "missing" else "incompatible"
    with pytest.raises(TrafficlabError, match=expected):
        lineage_module.identify_mounted_inputs(config)


def test_mounted_input_identification_classifies_a_regular_file_read_error_as_unavailable(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounted = tmp_path / "unreadable-request.txt"
    mounted.write_bytes(b"request-v1")
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    real_open = Path.open

    def fail_mounted_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if path == mounted:
            raise PermissionError("injected mounted-input read failure")
        return real_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", fail_mounted_open)

    with pytest.raises(TrafficlabError, match="mounted input request.txt is unavailable") as caught:
        lineage_module.identify_mounted_inputs(config)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": "restore the named mounted input bytes",
        "detail": "mounted input request.txt is unavailable",
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }
    identity_error = caught.value.__cause__
    assert isinstance(identity_error, TrafficlabError)
    assert isinstance(identity_error.__cause__, PermissionError)


def test_mounted_input_comparison_names_a_new_regular_file_at_the_same_declared_target(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounted = tmp_path / "directory-to-file"
    mounted.mkdir()
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    expected = lineage_module.identify_mounted_inputs(config)
    mounted.rmdir()
    mounted.write_bytes(b"now-a-file")

    with pytest.raises(TrafficlabError, match="mounted input request.txt is incompatible"):
        cast(Any, lineage_module)._require_matching_mounted_inputs(config, expected)


def test_mounted_input_identification_rejects_a_nonregular_mount_source(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    target_file = tmp_path / "target-file"
    target_file.write_bytes(b"bytes")
    mounted = tmp_path / "request.txt"
    mounted.symlink_to(target_file)
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )

    with pytest.raises(TrafficlabError, match="mounted input request.txt is incompatible"):
        lineage_module.identify_mounted_inputs(config)


def test_prepared_capture_reuses_a_stable_pair_before_any_workload_setup(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stable reuse must not create temporary state, calculate deadlines, or touch Docker."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 64),), metadata)
    )
    seed_capture_lineage(prepared)

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"reuse touched Docker operation {name}")

    def reject_temporary(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("reuse created a temporary capture directory")

    def reject_clock() -> float:
        raise AssertionError("reuse calculated or checked a deadline")

    monkeypatch.setattr(capture_module, "temporary_capture_directory", reject_temporary)

    result = capture_prepared_experiment(
        experiment_path,
        prepared,
        docker=cast(Any, NoDocker()),
        clock=reject_clock,
    )

    assert result.reused is True
    assert result.target_status == 0
    assert result.packet_count == 1
    assert result.run_directory == prepared.run_directory
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1] == {
        "event": "capture_reused",
        "packet_count": 1,
        "path": str(prepared.run_directory / "reference.pcapng"),
        "reused": True,
        "stage": "capture",
    }


def test_prepared_capture_removes_a_stable_stale_diagnostic_pair_before_reuse_success(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful retry must not leave failed-attempt diagnostics beside its reusable pair."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 64),), metadata)
    )
    seed_capture_lineage(prepared)
    (prepared.run_directory / "diagnostic-capture.json").write_bytes(b"stale metadata")
    (prepared.run_directory / "diagnostic-reference.pcapng").write_bytes(b"stale pcapng")

    result = capture_prepared_experiment(experiment_path, prepared, docker=cast(Any, object()))

    assert result.reused is True
    assert not (prepared.run_directory / "diagnostic-capture.json").exists()
    assert not (prepared.run_directory / "diagnostic-reference.pcapng").exists()


@pytest.mark.parametrize("replacement", ["metadata", "pcapng"])
def test_prepared_capture_preserves_a_diagnostic_replacement_and_rejects_reuse_success(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    """Diagnostic cleanup must never delete a file replaced at its quarantine boundary."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 64),), metadata)
    )
    seed_capture_lineage(prepared)
    diagnostic_metadata = prepared.run_directory / "diagnostic-capture.json"
    diagnostic_pcapng = prepared.run_directory / "diagnostic-reference.pcapng"
    diagnostic_metadata.write_bytes(b"stale metadata")
    diagnostic_pcapng.write_bytes(b"stale pcapng")
    replacement_path = diagnostic_metadata if replacement == "metadata" else diagnostic_pcapng
    replacement_bytes = f"concurrent {replacement}".encode()
    winner_path = tmp_path / f"winner-{replacement}"
    winner_path.write_bytes(replacement_bytes)
    real_rename = artifact_module.os.rename
    swapped = False

    def replace_at_quarantine(source: str | Path, destination: str | Path) -> None:
        nonlocal swapped
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not swapped
            and source_path == replacement_path
            and destination_path.parent.name.startswith(".capture-recovery.")
        ):
            swapped = True
            artifact_module.os.replace(winner_path, source_path)
        real_rename(source, destination)

    monkeypatch.setattr(artifact_module.os, "rename", replace_at_quarantine)

    with pytest.raises(TrafficlabError, match="changed during invalid-pair recovery"):
        capture_prepared_experiment(experiment_path, prepared, docker=cast(Any, object()))

    assert swapped
    assert replacement_path.read_bytes() == replacement_bytes


@pytest.mark.parametrize(
    "corruption",
    ["type", "source", "config", "run-directory", "snapshot", "log-termination", "log-prefix", "missing"],
)
def test_prepared_capture_rejects_mismatched_authoritative_inputs_before_docker(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    """A stale prepared value, snapshot, or initial log must never launch a workload."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    candidate: object = prepared
    if corruption == "type":
        candidate = object()
    elif corruption == "source":
        candidate = replace(prepared, source=tmp_path / "other.toml")
    elif corruption == "config":
        changed_run = prepared.config.run.model_copy(update={"final_seed": prepared.config.run.final_seed + 1})
        candidate = replace(prepared, config=prepared.config.model_copy(update={"run": changed_run}))
    elif corruption == "run-directory":
        candidate = replace(prepared, run_directory=tmp_path / "other-run")
    elif corruption == "snapshot":
        (prepared.run_directory / "experiment.toml").write_bytes(b"changed")
    elif corruption == "log-termination":
        log_path = prepared.run_directory / "run.log"
        log_path.write_bytes(log_path.read_bytes().rstrip(b"\n"))
    elif corruption == "log-prefix":
        (prepared.run_directory / "run.log").write_text('{"event":"wrong"}\n', encoding="utf-8")
    else:
        (prepared.run_directory / "experiment.toml").unlink()

    with pytest.raises((TypeError, TrafficlabError), match="prepared"):
        capture_prepared_experiment(experiment_path, cast(Any, candidate), docker=cast(Any, object()))


def test_public_capture_preserves_typed_snapshot_mutation_before_pair_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture cannot publish a pair under snapshot bytes changed during the workload."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    snapshot_path = prepared.run_directory / "experiment.toml"

    class SnapshotMutatingDocker(DockerDouble):
        def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
            result = super().signal_capture(compose_path, project_name, deadline=deadline)
            snapshot_path.write_bytes(snapshot_path.read_bytes() + b"\n")
            return result

    docker = SnapshotMutatingDocker("normal")

    with pytest.raises(TrafficlabError, match="experiment.toml changed during capture") as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    expected = {
        "affected_evidence": "experiment.toml",
        "authority": "primary",
        "corrective_action": "restore the prepared experiment snapshot and rerun capture",
        "detail": "experiment.toml changed during capture",
        "evidence_state": "preserved",
        "kind": "artifact_changed",
        "stage": "capture",
    }
    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == expected
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_outcome"] == expected
    assert records[-1]["secondary_details"] == []
    assert records[-1]["secondary_failures"] == []
    assert records[-1]["secondary_outcomes"] == []

    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert not (prepared.run_directory / "diagnostic-capture.json").exists()
    assert not (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    assert not tuple(prepared.run_directory.glob(".trafficlab-capture-*"))


@pytest.mark.parametrize(
    ("mutation", "detail", "corrective_action"),
    [
        ("remove", "mounted input request.txt is unavailable", "restore the named mounted input bytes"),
        (
            "change",
            "mounted input request.txt is incompatible",
            "restore the declared mounted-input content identity",
        ),
    ],
)
def test_public_capture_reidentifies_mounted_input_before_publication(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    detail: str,
    corrective_action: str,
) -> None:
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)

    class MountedInputMutatingDocker(DockerDouble):
        def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
            result = super().signal_capture(compose_path, project_name, deadline=deadline)
            if mutation == "remove":
                mounted.unlink()
            else:
                mounted.write_bytes(b"request-v2")
            return result

    docker = MountedInputMutatingDocker("normal")

    with pytest.raises(TrafficlabError, match=detail) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    expected = {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": corrective_action,
        "detail": detail,
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }
    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == expected
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_outcome"] == expected
    assert records[-1]["secondary_details"] == []
    assert records[-1]["secondary_failures"] == []
    assert records[-1]["secondary_outcomes"] == []
    for name in (
        "capture.json",
        "reference.pcapng",
        "diagnostic-capture.json",
        "diagnostic-reference.pcapng",
    ):
        assert not (prepared.run_directory / name).exists()
    assert not tuple(prepared.run_directory.glob(".trafficlab-capture-*"))
