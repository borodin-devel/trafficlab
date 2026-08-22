"""Shared typed setup for this decomposed validation suite."""

from __future__ import annotations

import fcntl
import hashlib
import subprocess
import tomllib
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, cast

import scripts.validation_study.common as vs_common
import scripts.validation_study.evidence as vs_evidence
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.records as vs_records
import scripts.validation_study.results.codec as vs_results_codec
import scripts.validation_study.results.reporting as vs_results_reporting
import scripts.validation_study.results.reproduction as vs_results_reproduction
import scripts.validation_study.rotation.run as vs_rotation_run
import scripts.validation_study.workloads as vs_workloads
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study.artifacts import offline_capture, offline_validation_study_primary
from tests.support.validation_study.builders import frozen, response_headers
from tests.support.validation_study.constants import ROOT
from tests.support.validation_study.repository import write_study_inputs
from trafficlab.common.config import ExperimentConfig, SimilarityConfig
from trafficlab.common.trace import TrafficTrace
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.stage import generate_experiment
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies
from trafficlab.preflight.stage import open_or_prepare_experiment

VALIDATION_STUDY_LOCAL_EXCLUDE_LOCK = Path("/tmp") / (
    f"trafficlab-validation-study-{hashlib.sha256(str(ROOT).encode('utf-8')).hexdigest()}.exclude.lock"
)


def offline_published_study(repository_root: Path) -> tuple[Path, Path, Path]:
    prerequisite_path, _expected = write_study_inputs(repository_root)
    prerequisites = vs_prereq_codec.parse_prerequisite_results(
        prerequisite_path.read_bytes(), repository_root=repository_root
    )
    configs = vs_rotation_run.validate_base_configs(repository_root, prerequisites)
    workloads = {item.name: item for item in vs_workloads.workload_specs(prerequisites.url)}
    records: list[vs_records.StudyRunRecord] = []
    traces: dict[tuple[vs_common.WorkloadName, int], TrafficTrace] = {}
    settings: dict[vs_common.WorkloadName, SimilarityConfig] = {}
    for order, run_id, workload_value, repeat in vs_common.PRIMARY_ORDER:
        workload_name = cast(vs_common.WorkloadName, workload_value)
        run_result, spec, workload, responses = offline_validation_study_primary(
            repository_root,
            execution_order=order,
            run_id=run_id,
            workload_name=workload_name,
            repeat=repeat,
            base_config=configs[workload_name],
        )
        records.append(
            vs_evidence.extract_primary_record(
                repository_root,
                spec,
                workload,
                run_result,
                float(order),
                responses,
            )
        )
        traces[(workload_name, repeat)] = vs_results_reproduction.load_reference_trace(spec.run_directory)
        settings[workload_name] = configs[workload_name].similarity

    def reproduction_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        assert cwd == repository_root
        assert check is False and capture_output is True and shell is False
        assert timeout == 1230.0
        run_experiment(
            repository_root / command[-1],
            dependencies=RunDependencies(
                open_or_prepare_experiment,
                offline_capture,
                fit_experiment,
                generate_experiment,
                compare_experiment,
            ),
        )
        scratch = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "mount"
            / "study-1"
            / "streaming.headers"
        )
        scratch.write_bytes(response_headers(0, 4_194_303))
        return subprocess.CompletedProcess(command, 0, stdout=b"offline reproduction\n", stderr=b"")

    reproduction = vs_results_reproduction.run_cli_reproduction(
        repository_root,
        prerequisites.study_id,
        configs["streaming"],
        records[3],
        workloads["streaming"],
        object_size_bytes=cast(int, prerequisites.capability["object_size_bytes"]),
        runner=reproduction_runner,
        perf_counter=iter((20.0, 22.0)).__next__,
    )
    identity = cast(
        vs_common.JsonObject,
        {
            "git_commit": prerequisites.git_commit,
            "python_version": prerequisites.tools["python_version"],
            "trafficlab_version": prerequisites.tools["trafficlab_version"],
            "docker_engine_version": prerequisites.tools["docker_engine_version"],
            "docker_compose_version": prerequisites.tools["docker_compose_version"],
            "platform": prerequisites.tools["platform"],
        },
    )
    result = vs_records.StudyResults(
        schema_version=1,
        environment=vs_results_reproduction.environment_record(prerequisites, identity, "2026-08-13T13:00:00Z"),
        protocol=vs_results_reproduction.protocol_record(prerequisites, prerequisite_path.read_bytes()),
        runs=tuple(records),
        natural_variation=cast(
            tuple[vs_common.FrozenJsonObject, vs_common.FrozenJsonObject, vs_common.FrozenJsonObject],
            tuple(frozen(value) for value in vs_results_reporting.natural_variation(records, traces, settings)),
        ),
        workload_summaries=cast(
            tuple[vs_common.FrozenJsonObject, vs_common.FrozenJsonObject, vs_common.FrozenJsonObject],
            tuple(frozen(value) for value in vs_results_reporting.workload_summaries(records)),
        ),
        reproduction=reproduction,
    )
    result_path = repository_root / "examples" / "validation_study" / "results.json"
    vs_results_codec.publish_results(result_path, result, repository_root=repository_root)
    report_path = repository_root / "examples" / "validation_study" / "REPORT.md"
    identifiers = [
        prerequisites.study_id,
        prerequisites.git_commit,
        cast(str, prerequisites.images["target_image_id"]),
        cast(str, prerequisites.images["capture_image_id"]),
        *(record.run_id for record in records),
        "10-streaming-r2-reproduction",
    ]
    report_path.write_text("\n\n".join((*vs_common.REPORT_HEADINGS, *identifiers)), encoding="utf-8")
    return prerequisite_path, result_path, report_path


def finish_validation_study_exclude_restore(*, body_error: BaseException | None, restore: Callable[[], None]) -> None:
    """Restore shared Git exclusion bytes without discarding a primary audit failure."""

    try:
        restore()
    except BaseException as cleanup_error:
        if body_error is not None:
            raise BaseExceptionGroup(
                "validation-study audit and shared Git exclusion cleanup both failed",
                (body_error, cleanup_error),
            ) from None
        raise


@contextmanager
def exclusive_validation_study_file_lock(path: Path) -> Generator[None, None, None]:
    """Serialize a temporary mutation of shared Git administration bytes across workers."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def candidate_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def auditor_semantics_fixture_config() -> ExperimentConfig:
    """Build one valid config with the declared host mount that publication relocates."""

    document = tomllib.loads(
        (VALIDATION_STUDY_CANDIDATE / "configs" / "training-short-r1.realized.toml").read_text(encoding="utf-8")
    )
    target = cast(dict[str, Any], document["target"])
    target["mounts"] = [{"source": "/retained/mount", "target": "/trafficlab-study/short.headers", "read_only": True}]
    return ExperimentConfig.model_validate(document)


NONOPERATIONAL_CONFIG_MUTATIONS = (
    "master_seed",
    "target_image",
    "target_argv_order",
    "target_environment",
    "target_working_directory",
    "mount_target",
    "mount_read_only",
    "capture_timeout",
    "trial_limit",
    "final_limit",
    "population_size",
    "model_bound",
    "similarity_limit",
)

NONOPERATIONAL_REALIZED_CONFIG_MUTATIONS = tuple(
    case for case in NONOPERATIONAL_CONFIG_MUTATIONS if case not in {"mount_target", "mount_read_only"}
)


def nonoperational_config_mutation(config: ExperimentConfig, case: str) -> ExperimentConfig:
    document = config.model_dump(mode="json")
    target = cast(dict[str, Any], document["target"])
    if case == "master_seed":
        cast(dict[str, Any], document["run"])["master_seed"] = 74
    elif case == "target_image":
        target["image"] = "curlimages/curl@sha256:" + "1" * 64
    elif case == "target_argv_order":
        target["argv"] = list(reversed(cast(list[str], target["argv"])))
    elif case == "target_environment":
        target["environment"] = {"TRAFFICLAB_MUTATION": "1"}
    elif case == "target_working_directory":
        target["working_directory"] = "/changed"
    elif case == "mount_target":
        mount = cast(dict[str, Any], cast(list[object], target["mounts"])[0])
        mount["target"] = "/changed.headers"
    elif case == "mount_read_only":
        mount = cast(dict[str, Any], cast(list[object], target["mounts"])[0])
        mount["read_only"] = False
    elif case == "capture_timeout":
        cast(dict[str, Any], document["capture"])["readiness_timeout_seconds"] = 3.0
    elif case == "trial_limit":
        cast(dict[str, Any], cast(dict[str, Any], document["generation"])["trial"])["max_packets"] = 501
    elif case == "final_limit":
        cast(dict[str, Any], cast(dict[str, Any], document["generation"])["final"])["max_packets"] = 1001
    elif case == "population_size":
        cast(dict[str, Any], document["genetic"])["population_size"] = 7
    elif case == "model_bound":
        cast(dict[str, Any], cast(dict[str, Any], document["models"])["poisson_empirical"])["c_lambda"]["lower"] = 0.6
    else:
        cast(dict[str, Any], document["similarity"])["max_direction_bin_cells"] = 101

    return ExperimentConfig.model_validate(document)


def config_semantic_leaf_paths(value: Any, prefix: tuple[str | int, ...] = ()) -> tuple[tuple[str | int, ...], ...]:
    if prefix == ("run", "directory"):
        return ()
    if prefix == ("target", "mounts"):
        mounts = cast(list[dict[str, Any]], value)
        return tuple(
            path
            for index, mount in enumerate(mounts)
            for path in (("target", "mounts", index, "target"), ("target", "mounts", index, "read_only"))
            if path[-1] in mount
        )
    if prefix == ("similarity", "method_weights"):
        return (prefix,)
    if prefix == ("similarity",):
        coupled = {
            "acf_lags",
            "acf_lag_weights",
            "acf_iat_weight",
            "acf_size_weight",
            "multiscale_packet_weight",
            "multiscale_byte_weight",
        }
        settings = cast(dict[str, Any], value)
        return (
            ("similarity", "__acf_lags_and_weights__"),
            ("similarity", "__acf_component_weights__"),
            ("similarity", "__multiscale_component_weights__"),
            *(
                path
                for key, child in settings.items()
                if key not in coupled
                for path in config_semantic_leaf_paths(child, (*prefix, key))
            ),
        )
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if not mapping:
            return (prefix,)
        return tuple(
            path for key, child in mapping.items() for path in config_semantic_leaf_paths(child, (*prefix, key))
        )
    return (prefix,)


def config_semantic_path_value(document: dict[str, Any], path: tuple[str | int, ...]) -> Any:
    if isinstance(path[-1], str) and path[-1].startswith("__"):
        return None
    value: Any = document
    for part in path:
        value = value[part]
    return value


def set_config_semantic_path_value(
    document: dict[str, Any],
    path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    if path == ("similarity", "__acf_lags_and_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["acf_lags"] = [1, 2]
        similarity["acf_lag_weights"] = [0.5, 0.5]
        return
    if path == ("similarity", "__acf_component_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["acf_iat_weight"] = 0.6
        similarity["acf_size_weight"] = 0.4
        return
    if path == ("similarity", "__multiscale_component_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["multiscale_packet_weight"] = 0.6
        similarity["multiscale_byte_weight"] = 0.4
        return
    parent = config_semantic_path_value(document, path[:-1])
    parent[path[-1]] = replacement


def config_semantic_replacements(path: tuple[str | int, ...], value: Any) -> tuple[Any, ...]:
    if isinstance(path[-1], str) and path[-1].startswith("__"):
        return (None,)
    if path == ("target", "image"):
        return ("curlimages/curl@sha256:" + "1" * 64,)
    if path == ("capture", "image"):
        return ("trafficlab-capture@sha256:" + "2" * 64,)
    if path == ("capture", "network_probe_url"):
        return ("https://example.test/changed",)
    if path == ("similarity", "method_weights"):
        weights = cast(dict[str, float], value)
        return ({**weights, "frame_size_ks": 0.30, "iat_ks": 0.20},)
    if type(value) is bool:
        return (not value,)
    if type(value) is int:
        return (value + 1, value - 1)
    if type(value) is float:
        return (value + 0.01, value - 0.01)
    if type(value) is str:
        return (f"{value}-changed",)
    if isinstance(value, list):
        items = cast(list[Any], value)
        if path == ("genetic", "trial_seeds"):
            return ([cast(int, items[0]) + 1],)
        if all(type(item) is str for item in items):
            return (list(reversed(items)),)
        if all(type(item) is int for item in items):
            return ([cast(int, items[0]) + 1],)
        if len(items) == 1:
            return ([cast(float, items[0]) + 0.1], [cast(float, items[0]) - 0.1])
        changed = list(items)
        changed[0] = cast(float, changed[0]) + 0.1
        changed[-1] = cast(float, changed[-1]) - 0.1
        return (changed,)
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return ({**mapping, "TRAFFICLAB_MUTATION": "1"},)
    raise AssertionError(f"no mutation candidate for config path {path}")
