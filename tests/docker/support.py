from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import tomli_w

from tests.conftest import DockerProjectTracker, DockerTestEnvironment, inspect_project_resources


def write_docker_experiment(
    path: Path,
    data: dict[str, object],
    environment: DockerTestEnvironment,
    *,
    argv: list[str],
    target_image: str | None = None,
    readiness_timeout: float = 5.0,
    workload_timeout: float = 15.0,
    flush_timeout: float = 5.0,
    total_timeout: float = 35.0,
    probe_url: str = "http://endpoint:18080/",
) -> Path:
    configured = copy.deepcopy(data)
    run = cast(dict[str, object], configured["run"])
    target = cast(dict[str, object], configured["target"])
    capture = cast(dict[str, object], configured["capture"])
    run["directory"] = str(path.parent / f"{path.stem}-run")
    run["minimum_free_bytes"] = 1
    target["image"] = target_image or environment.client_image
    target["argv"] = argv
    target["environment"] = {}
    target["working_directory"] = "/"
    target["mounts"] = []
    capture["image"] = environment.capture_image
    capture["network_probe_url"] = probe_url
    capture["readiness_timeout_seconds"] = readiness_timeout
    capture["workload_timeout_seconds"] = workload_timeout
    capture["flush_timeout_seconds"] = flush_timeout
    capture["total_timeout_seconds"] = total_timeout
    path.write_text(tomli_w.dumps(configured), encoding="utf-8")
    return path


def write_run_docker_experiment(
    path: Path,
    data: dict[str, object],
    environment: DockerTestEnvironment,
    *,
    readiness_timeout: float = 5.0,
    probe_url: str = "http://endpoint:18080/",
) -> Path:
    """Write the bounded three-family configuration used by complete Docker runs."""
    configured = copy.deepcopy(data)
    run = cast(dict[str, object], configured["run"])
    generation = cast(dict[str, object], configured["generation"])
    genetic = cast(dict[str, object], configured["genetic"])
    similarity = cast(dict[str, object], configured["similarity"])
    run["master_seed"] = 73
    run["final_seed"] = 97
    generation["trial"] = {
        "max_packets": 500,
        "max_output_bytes": 1_000_000,
        "max_wall_seconds": 5.0,
    }
    generation["final"] = {
        "max_packets": 1_000,
        "max_output_bytes": 2_000_000,
        "max_wall_seconds": 10.0,
    }
    genetic.update(
        {
            "population_size": 6,
            "generation_count": 0,
            "tournament_size": 2,
            "elite_count": 1,
            "trial_seeds": [17],
            "duplicate_mutation_attempts": 1,
            "early_stopping_generations": 0,
            "early_stopping_tolerance": 0.0,
            "resume": True,
        }
    )
    similarity["multiscale_widths_seconds"] = [0.001, 0.005]
    return write_docker_experiment(
        path,
        configured,
        environment,
        argv=[
            "traffic",
            "--tcp-count",
            "2",
            "--udp-count",
            "3",
            "--inter-request-seconds",
            "0.002",
        ],
        readiness_timeout=readiness_timeout,
        probe_url=probe_url,
    )


def assert_tracked_projects_clean(tracker: DockerProjectTracker) -> None:
    """Require every resource kind for every unique test project to be absent."""
    assert tracker.projects, "the Docker test did not record a resource-owning Compose project"
    for project_name in sorted(tracker.projects):
        inspection = inspect_project_resources(project_name)
        assert inspection.diagnostics == ()
        assert inspection.resources == {
            "containers": (),
            "networks": (),
            "volumes": (),
        }


def capture_log(run_directory: Path) -> list[dict[str, object]]:
    return [cast(dict[str, object], json.loads(line)) for line in (run_directory / "run.log").read_text().splitlines()]


def capture_lifecycle_positions(run_directory: Path) -> tuple[int, int]:
    """Return readiness and successful publication positions from the real run-log schema."""
    records = capture_log(run_directory)
    ready = next(index for index, record in enumerate(records) if record.get("event") == "capture_ready")
    published = next(index for index, record in enumerate(records) if record.get("event") == "capture_published")
    return ready, published


def capture_project_name(run_directory: Path) -> str:
    for record in capture_log(run_directory):
        if record.get("event") == "capture_project_created":
            return cast(str, record["project_name"])
    raise AssertionError("capture project was not recorded")
