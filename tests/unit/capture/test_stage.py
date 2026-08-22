from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.capture.stage as capture_module
from tests.support.capture import Clock, DockerDouble, prepared_capture
from trafficlab.capture.stage import CaptureResult, capture_experiment, capture_prepared_experiment
from trafficlab.common.errors import TrafficlabError


def test_capture_uses_default_docker_boundary_when_not_injected(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public default path must construct the same bounded Docker adapter used by injection tests."""
    experiment_path, _prepared_run = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("normal")
    constructed: list[object] = []

    def default_docker(*, clock: object) -> DockerDouble:
        constructed.append(clock)
        return docker

    monkeypatch.setattr(capture_module, "DockerCompose", default_docker)
    clock = Clock(docker)

    result = capture_experiment(experiment_path, clock=clock, interruption=lambda: False)

    assert constructed == [clock]
    assert result.packet_count == 2


def test_capture_rejects_internal_success_without_a_reusable_result(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public boundary must never silently return when an internal result is unexpectedly absent."""
    experiment_path, _prepared_run = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("normal")

    def absent_result(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(capture_module, "CaptureResult", absent_result)

    with pytest.raises(TrafficlabError, match="completed without a reusable reference"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (("run", Path("/reference"), 1, 0), "run_directory"),
        ((Path("run"), Path("/reference"), 1, 0), "run_directory"),
        ((Path("/run"), "reference", 1, 0), "reference_path"),
        ((Path("/run"), Path("reference"), 1, 0), "reference_path"),
        ((Path("/run"), Path("/reference"), True, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), 0, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), -1, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), 1, True), "target_status"),
    ],
)
def test_capture_result_strictly_rejects_invalid_public_values(arguments: tuple[object, ...], error: str) -> None:
    """Accepting coerced or relative result fields would break the public capture contract."""
    with pytest.raises((TypeError, ValueError), match=error):
        CaptureResult(*cast(tuple[Any, Any, Any, Any], arguments))


def test_fresh_capture_requires_full_preflight_image_identity_before_compose(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    prepared = replace(
        prepared,
        report=replace(prepared.report, environment_identity=None),
    )

    def reject_compose(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("capture without image identity rendered Compose")

    monkeypatch.setattr(capture_module, "write_production_compose", reject_compose)

    with pytest.raises(TrafficlabError, match="resolved Docker image identities") as caught:
        capture_prepared_experiment(
            experiment_path,
            prepared,
            docker=cast(Any, object()),
        )

    assert caught.value.corrective_action == "run full preflight without --config-only and retry capture"


def test_capture_result_rejects_a_non_boolean_reuse_flag() -> None:
    """Truthiness coercion would make capture ownership ambiguous to the coordinator."""
    with pytest.raises(TypeError, match="reused"):
        CaptureResult(Path("/run"), Path("/run/reference.pcapng"), 1, 0, cast(Any, 1))
