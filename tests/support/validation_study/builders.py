"""Builders owner for Validation Study tooling."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from statistics import fmean, variance
from typing import cast

from scripts.validation_study.common import (
    ARTIFACT_NAMES,
    BOOTSTRAP_SEED,
    FAMILY_ORDER,
    PRIMARY_ORDER,
    PUBLISHED_METHOD_ORDER,
    RUNTIME_BOUNDARY,
    TARGET_REFERENCE,
    FrozenJsonObject,
    JsonObject,
    JsonValue,
    freeze_json,
)
from scripts.validation_study.prerequisites.codec import render_prerequisite_results
from scripts.validation_study.records import PrerequisiteResults, ReproductionRecord, StudyResults, StudyRunRecord
from scripts.validation_study.workloads import build_base_config, render_checked_base_config, workload_specs
from tests.support.validation_study.constants import HASH, IMAGE_ID
from trafficlab import USER_AGENT
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import FamilyName
from trafficlab.common.statistics import bootstrap_interval
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState, encode_rng_state
from trafficlab.fitting.genetic.strategy import make_strategy_context
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateId,
    MethodTrialResult,
    TrialResult,
    rebuild_genetic_record,
)
from trafficlab.generation.models.common import MARKOV_MODEL_DIAGNOSTIC_KEYS, make_rng
from trafficlab.generation.models.fitted_model import BestModel, make_best_model
from trafficlab.generation.models.registry import get_family


def frozen(document: object) -> FrozenJsonObject:
    return cast(FrozenJsonObject, freeze_json(cast(JsonValue, document)))


def valid_prerequisite(*, study_id: str = "study-1") -> PrerequisiteResults:
    url = "https://downloads.example.test/object.bin"
    started = "2026-08-13T12:00:00Z"
    completed = "2026-08-13T12:01:00Z"
    mount_source = f"examples/validation_study/.study-work/mount/{study_id}"
    archive_path = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites/capability.headers"
    evidence_root = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    guard = ["scripts/run_bounded.sh", "--memory-high", "2G", "--memory-max", "3G", "--swap-max", "512M"]
    docker_argv = [
        *guard,
        "--wall-time",
        "20m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "docker",
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
        "--junitxml",
        f"{evidence_root}/docker.xml",
    ]
    internet_argv = [
        *guard,
        "--wall-time",
        "10m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "internet",
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
        "--internet-url",
        url,
        "--junitxml",
        f"{evidence_root}/internet.xml",
    ]
    command = {
        "kind": "docker_matrix",
        "argv": docker_argv,
        "started_utc": started,
        "completed_utc": completed,
        "exit_status": 0,
        "tests": {"total": 2, "passed": 2, "failed": 0, "errors": 0, "skipped": 0},
        "stdout_sha256": HASH,
        "stderr_sha256": HASH,
        "junit_sha256": HASH,
    }
    capability_argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"trafficlab-validation-study-capability-{study_id}",
        "--label",
        f"org.trafficlab.validation-study.study={study_id}",
        "--cidfile",
        f"{evidence_root}/capability.cid",
        "--network",
        "bridge",
        "--mount",
        f"type=bind,src={mount_source},dst=/trafficlab-study",
        TARGET_REFERENCE,
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--max-redirs",
        "3",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--http1.1",
        "--user-agent",
        USER_AGENT,
        "--connect-timeout",
        "15",
        "--max-time",
        "30",
        "--range",
        "0-0",
        "--max-filesize",
        "1",
        "--dump-header",
        "/trafficlab-study/.capability.headers",
        "--output",
        "/dev/null",
        "--write-out",
        "status=%{response_code}\nsize=%{size_download}\nurl=%{url_effective}\nredirects=%{num_redirects}\n",
        "--url",
        url,
    ]
    return PrerequisiteResults(
        schema_version=1,
        created_utc=started,
        study_id=study_id,
        git_commit="c" * 40,
        git_tree_clean=True,
        url=url,
        tools=frozen(
            {
                "docker_engine_version": "27.0.0",
                "docker_compose_version": "2.29.0",
                "host_architecture": "x86_64",
                "kernel_release": "test-kernel",
                "platform": "Linux-test",
                "python_implementation": "CPython",
                "python_version": "3.12.3",
                "trafficlab_version": "0.1.0",
                "uv_lock_sha256": HASH,
            }
        ),
        images=frozen(
            {
                "target_reference": TARGET_REFERENCE,
                "target_image_id": IMAGE_ID,
                "target_repo_digests": [TARGET_REFERENCE],
                "target_config_user": "",
                "capture_image_id": f"sha256:{'d' * 64}",
                "capture_dockerfile_sha256": HASH,
                "capture_script_sha256": HASH,
            }
        ),
        capability=frozen(
            {
                "argv": capability_argv,
                "started_utc": started,
                "completed_utc": completed,
                "exit_status": 0,
                "status": 206,
                "content_length": 1,
                "object_size_bytes": 4194304,
                "redirect_count": 0,
                "body_bytes_downloaded": 1,
                "content_range": "bytes 0-0/4194304",
                "final_url": url,
                "mount_source": mount_source,
                "canary_archive_path": archive_path,
                "canary_sha256": HASH,
                "container_id": "e" * 64,
                "stdout_sha256": HASH,
                "stderr_sha256": HASH,
                "used_image_default_user": True,
                "mount_directory_mode": 493,
                "canary_file_mode": 438,
                "canary_archive_mode": 384,
                "container_cleanup_verified": True,
            }
        ),
        config_sha256=frozen({"short": HASH, "streaming": HASH, "bursty": HASH}),
        commands=(frozen(command), frozen({**command, "kind": "internet_smoke", "argv": internet_argv})),
    )


def changed_config_paths(left: object, right: object, *, prefix: str = "") -> set[str]:
    if type(left) is dict and type(right) is dict:
        left_mapping = cast(dict[str, object], left)
        right_mapping = cast(dict[str, object], right)
        assert set(left_mapping) == set(right_mapping)
        changes: set[str] = set()
        for key in left_mapping:
            child = f"{prefix}.{key}" if prefix else key
            changes.update(changed_config_paths(left_mapping[key], right_mapping[key], prefix=child))
        return changes
    return set() if left == right else {prefix}


def write_checked_configs(
    repository_root: Path,
    *,
    study_id: str = "study-1",
    url: str = "https://downloads.example.test/object.bin",
    capture_image_id: str = f"sha256:{'d' * 64}",
) -> tuple[PrerequisiteResults, dict[str, bytes]]:
    contents: dict[str, bytes] = {}
    for spec in workload_specs(url):
        config = build_base_config(
            spec, repository_root=repository_root, study_id=study_id, url=url, capture_image_id=capture_image_id
        )
        destination = repository_root / "examples" / "validation_study" / "configs" / f"{spec.name}.toml"
        contents[spec.name] = render_checked_base_config(config, destination, repository_root)
    prerequisite = replace(
        valid_prerequisite(),
        config_sha256=frozen({name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}),
    )
    return (prerequisite, contents)


def response_headers(
    start: int, end: int, *, total: int = 4194304, status: int = 206, length: int | None = None, prefix: bytes = b""
) -> bytes:
    content_length = end - start + 1 if length is None else length
    return (
        prefix
        + f"HTTP/1.1 {status} Response\r\n".encode()
        + f"Content-Range: bytes {start}-{end}/{total}\r\n".encode()
        + f"Content-Length: {content_length}\r\n\r\n".encode()
    )


def score(value: float) -> dict[str, object]:
    return {"aggregate": value, "methods": {name: value for name in PUBLISHED_METHOD_ORDER}}


def _descriptive(values: list[int | float]) -> dict[str, object]:
    numbers = [float(value) for value in values]
    minimum = min(numbers)
    maximum = max(numbers)
    sample_variance = variance(numbers)
    return {
        "bootstrap": bootstrap_interval(numbers, seed=BOOTSTRAP_SEED).as_dict(),
        "count": 3,
        "mean": fmean(numbers),
        "minimum": minimum,
        "maximum": maximum,
        "range": maximum - minimum,
        "sample_variance": sample_variance,
        "sample_standard_deviation": math.sqrt(sample_variance),
    }


def _trace_summary(workload: str, repeat: int, *, generated: bool = False) -> dict[str, object]:
    packet_count = repeat + (4 if generated else 3)
    outbound = repeat + (2 if generated else 1)
    inbound = packet_count - outbound
    outbound_bytes = 100 * outbound
    inbound_bytes = 200 * inbound
    widths = [0.25, 1.0] if workload == "streaming" else [0.001, 0.01]
    return {
        "packet_count": packet_count,
        "observation_window_seconds": float(10 + repeat),
        "packet_totals": {"outbound": outbound, "inbound": inbound},
        "byte_totals": {"outbound": outbound_bytes, "inbound": inbound_bytes},
        "frame_lengths": {
            "count": packet_count,
            "minimum": 60.0,
            "median": 100.0,
            "quantile_probability": 0.95,
            "quantile": 200.0,
            "maximum": 200.0,
            "zero_count": 0,
        },
        "iats": {
            "count": packet_count - 1,
            "minimum": 0.0,
            "median": 0.5,
            "quantile_probability": 0.95,
            "quantile": 1.0,
            "maximum": 1.0,
            "zero_count": 1,
        },
        "scales": [
            {
                "width_seconds": width,
                "bins_per_direction": 2,
                "packet_totals": {"outbound": outbound, "inbound": inbound},
                "byte_totals": {"outbound": outbound_bytes, "inbound": inbound_bytes},
            }
            for width in widths
        ],
    }


def _genes(family: str) -> list[int | float]:
    if family == "markov_renewal":
        return [0.2, 0.7, 1.0, 4, 1.0]
    if family == "mmpp":
        return [1.0, 2.0, 10.0, 20.0]
    return [1.0]


def _champions(repeat: int, *, delta: float = 0.0) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, family in enumerate(FAMILY_ORDER):
        fitness = 0.5 + 0.05 * index + 0.01 * repeat + delta
        result.append(
            {
                "family": family,
                "candidate_id": {"birth_generation": 2, "birth_index": index},
                "genes": _genes(family),
                "selection_fitness": fitness,
                "selection_seeds": [17, 29],
                "selection_score": score(fitness),
            }
        )
    return result


def transfer_responses(study_id: str, run_id: str, workload: str) -> list[dict[str, object]]:
    if workload == "short":
        transfers = [(0, 1048575, "short.headers")]
    elif workload == "streaming":
        transfers = [(0, 4194303, "streaming.headers")]
    else:
        starts = (0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016)
        transfers = [(start, start + 32767, f"bursty-{index}.headers") for index, start in enumerate(starts)]
    return [
        {
            "transfer_index": index,
            "requested_start": start,
            "requested_end": end,
            "status": 206,
            "content_length": end - start + 1,
            "content_range": f"bytes {start}-{end}/4194304",
            "header_archive_path": f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}/{filename}",
            "header_sha256": HASH,
            "scratch_precreate_mode": 438,
            "archive_mode": 384,
            "inode_preserved": True,
        }
        for index, (start, end, filename) in enumerate(transfers)
    ]


def _run_document(study_id: str, execution_order: int, run_id: str, workload: str, repeat: int) -> dict[str, object]:
    champions = _champions(repeat)
    winner = champions[2]
    fresh_simulation_value = 0.7 + 0.01 * repeat
    published_value = 0.65 + 0.01 * repeat
    event_count = repeat + 4
    return {
        "execution_order": execution_order,
        "run_id": run_id,
        "key": {"workload": workload, "repeat": repeat},
        "config_path": f"runs/validation_study/{study_id}/realized-configs/{run_id}.toml",
        "run_directory": f"runs/validation_study/{study_id}/{run_id}",
        "transfer_evidence_directory": f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}",
        "elapsed_seconds": float(repeat),
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "cleanup_verified": True,
        "transfer_responses": transfer_responses(study_id, run_id, workload),
        "artifact_sha256": {name: HASH for name in ARTIFACT_NAMES},
        "reference": _trace_summary(workload, repeat),
        "generated": _trace_summary(workload, repeat, generated=True),
        "family_champions": champions,
        "winner": {
            "family": winner["family"],
            "candidate_id": winner["candidate_id"],
            "genes": winner["genes"],
            "selection_fitness": winner["selection_fitness"],
        },
        "fresh_simulation": {
            "seed": 97,
            "score": score(fresh_simulation_value),
            "source": "run_experiment_fit_outcome",
        },
        "published": {"seed": 97, "score": score(published_value)},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": float(10 + repeat),
            "trial_event_count": event_count,
            "final_event_count": event_count,
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": event_count,
            "reparsed_matches_quantized": True,
        },
    }


def _score_summary(values: list[float]) -> dict[str, object]:
    return {
        "aggregate": _descriptive(values),
        "methods": {name: _descriptive(values) for name in PUBLISHED_METHOD_ORDER},
    }


def _reference_descriptors(runs: list[dict[str, object]]) -> dict[str, object]:
    references = [cast(dict[str, object], run["reference"]) for run in runs]
    packet_totals = [cast(dict[str, int], reference["packet_totals"]) for reference in references]
    byte_totals = [cast(dict[str, int], reference["byte_totals"]) for reference in references]
    return {
        "packet_count": _descriptive([cast(int, reference["packet_count"]) for reference in references]),
        "observation_window_seconds": _descriptive(
            [cast(float, reference["observation_window_seconds"]) for reference in references]
        ),
        "outbound_packets": _descriptive([totals["outbound"] for totals in packet_totals]),
        "inbound_packets": _descriptive([totals["inbound"] for totals in packet_totals]),
        "outbound_bytes": _descriptive([totals["outbound"] for totals in byte_totals]),
        "inbound_bytes": _descriptive([totals["inbound"] for totals in byte_totals]),
    }


def valid_result_document(repository_root: Path) -> dict[str, object]:
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    prerequisite = cast(dict[str, object], json.loads(render_prerequisite_results(valid_prerequisite())))
    capability = prerequisite["capability"]
    runs = [
        _run_document(study_id, order, run_id, workload, repeat) for order, run_id, workload, repeat in PRIMARY_ORDER
    ]
    by_workload: dict[str, list[dict[str, object]]] = {}
    for workload in ("short", "streaming", "bursty"):
        workload_runs = [run for run in runs if cast(dict[str, object], run["key"])["workload"] == workload]
        by_workload[workload] = sorted(
            workload_runs, key=lambda run: cast(int, cast(dict[str, object], run["key"])["repeat"])
        )
    natural_variation: list[dict[str, object]] = []
    workload_summaries: list[dict[str, object]] = []
    for workload in ("short", "streaming", "bursty"):
        workload_runs = by_workload[workload]
        pairs: list[dict[str, object]] = []
        for left, right in ((1, 2), (1, 3), (2, 3)):
            forward_value = 0.4 + 0.01 * left + 0.02 * right
            reverse_value = forward_value + 0.02
            pairs.append(
                {
                    "left_repeat": left,
                    "right_repeat": right,
                    "forward": score(forward_value),
                    "reverse": score(reverse_value),
                    "symmetric": score((forward_value + reverse_value) / 2.0),
                }
            )
        descriptors = _reference_descriptors(workload_runs)
        natural_variation.append(
            {"workload": workload, "pairs": pairs, "reference_descriptors": copy.deepcopy(descriptors)}
        )
        champion_summaries: dict[str, object] = {}
        for family_index, family in enumerate(FAMILY_ORDER):
            values = [0.5 + 0.05 * family_index + 0.01 * repeat for repeat in (1, 2, 3)]
            champion_summaries[family] = {
                "selection_fitness": _descriptive(values),
                "selection_components": {name: _descriptive(values) for name in PUBLISHED_METHOD_ORDER},
            }
        winners = [cast(dict[str, object], run["winner"]) for run in workload_runs]
        winner_values = [cast(float, winner["selection_fitness"]) for winner in winners]
        held_values = [
            cast(float, cast(dict[str, object], cast(dict[str, object], run["fresh_simulation"])["score"])["aggregate"])
            for run in workload_runs
        ]
        published_values = [
            cast(float, cast(dict[str, object], cast(dict[str, object], run["published"])["score"])["aggregate"])
            for run in workload_runs
        ]
        workload_summaries.append(
            {
                "workload": workload,
                "runtime": _descriptive([cast(float, run["elapsed_seconds"]) for run in workload_runs]),
                "family_champions": champion_summaries,
                "winner_selection_fitness": _descriptive(winner_values),
                "fresh_simulation": _score_summary(held_values),
                "published": _score_summary(published_values),
                "reference_descriptors": copy.deepcopy(descriptors),
                "winner_counts": {"markov_renewal": 0, "mmpp": 0, "poisson_empirical": 3},
            }
        )
    source = by_workload["streaming"][1]
    reproduction_run_id = "10-streaming-r2-reproduction"
    reproduction_champions = _champions(2, delta=0.01)
    reproduction_winner = reproduction_champions[2]
    reproduction_held = 0.73
    reproduction_published = 0.68
    source_winner = cast(dict[str, object], source["winner"])
    source_held_score = cast(dict[str, object], cast(dict[str, object], source["fresh_simulation"])["score"])
    source_published_score = cast(dict[str, object], cast(dict[str, object], source["published"])["score"])
    config_path = f"runs/validation_study/{study_id}/realized-configs/reproduction.toml"
    command = ["uv", "run", "--locked", "trafficlab", "run", config_path]
    guard_command = [
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        "20m",
        "--kill-after",
        "10s",
        "--",
        *command,
    ]
    reproduction = {
        "source_key": {"workload": "streaming", "repeat": 2},
        "execution_order": 10,
        "run_id": reproduction_run_id,
        "config_path": config_path,
        "run_directory": f"runs/validation_study/{study_id}/{reproduction_run_id}",
        "transfer_evidence_directory": f"examples/validation_study/.study-work/evidence/{study_id}/{reproduction_run_id}",
        "command": command,
        "guard_command": guard_command,
        "guard_exit_status": 0,
        "guard_stdout_sha256": HASH,
        "guard_stderr_sha256": HASH,
        "elapsed_seconds": 4.0,
        "changed_config_fields": ["run.directory"],
        "same_locked_config": True,
        "seeded_artifact_count": 0,
        "cleanup_verified": True,
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "transfer_responses": transfer_responses(study_id, reproduction_run_id, "streaming"),
        "artifact_sha256": {name: "f" * 64 for name in ARTIFACT_NAMES},
        "reference": _trace_summary("streaming", 2),
        "generated": _trace_summary("streaming", 2, generated=True),
        "family_champions": reproduction_champions,
        "winner": {
            "family": reproduction_winner["family"],
            "candidate_id": reproduction_winner["candidate_id"],
            "genes": reproduction_winner["genes"],
            "selection_fitness": reproduction_winner["selection_fitness"],
        },
        "fresh_simulation": {"seed": 97, "score": score(reproduction_held), "source": "post_cli_evaluate_final"},
        "published": {"seed": 97, "score": score(reproduction_published)},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": 12.0,
            "trial_event_count": 6,
            "final_event_count": 6,
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": 6,
            "reparsed_matches_quantized": True,
        },
        "comparison_to_source": {
            "winner_family_equal": reproduction_winner["family"] == source_winner["family"],
            "winner_genes_equal": reproduction_winner["genes"] == source_winner["genes"],
            "winner_selection_fitness_delta": cast(float, reproduction_winner["selection_fitness"])
            - cast(float, source_winner["selection_fitness"]),
            "fresh_simulation_delta": {
                "aggregate": reproduction_held - cast(float, source_held_score["aggregate"]),
                "methods": {
                    name: reproduction_held - cast(float, cast(dict[str, object], source_held_score["methods"])[name])
                    for name in PUBLISHED_METHOD_ORDER
                },
            },
            "published_delta": {
                "aggregate": reproduction_published - cast(float, source_published_score["aggregate"]),
                "methods": {
                    name: reproduction_published
                    - cast(float, cast(dict[str, object], source_published_score["methods"])[name])
                    for name in PUBLISHED_METHOD_ORDER
                },
            },
            "reference_similarity": score(0.5),
        },
    }
    return {
        "schema_version": 1,
        "environment": {
            "git_commit": prerequisite["git_commit"],
            "python_version": "3.12.3",
            "trafficlab_version": "0.1.0",
            "docker_engine_version": "27.0.0",
            "docker_compose_version": "2.29.0",
            "platform": "Linux-test",
            "target_image_id": IMAGE_ID,
            "capture_image_id": f"sha256:{'d' * 64}",
            "study_date_utc": "2026-08-13T13:00:00Z",
        },
        "protocol": {
            "study_id": study_id,
            "url": url,
            "capability": capability,
            "prerequisites_sha256": HASH,
            "target_reference": TARGET_REFERENCE,
            "capture_image_id": f"sha256:{'d' * 64}",
            "transfer_evidence_mount_source": f"examples/validation_study/.study-work/mount/{study_id}",
            "base_config_sha256": {"short": HASH, "streaming": HASH, "bursty": HASH},
            "primary_order": [
                {"workload": workload, "repeat": repeat} for _order, _run_id, workload, repeat in PRIMARY_ORDER
            ],
            "seeds": {"master": 73, "final": 97, "selection": [17, 29]},
            "families": list(FAMILY_ORDER),
            "methods": list(PUBLISHED_METHOD_ORDER),
            "workloads": [
                {
                    "name": spec.name,
                    "argv": list(spec.argv),
                    "workload_timeout_seconds": spec.workload_timeout_seconds,
                    "total_timeout_seconds": spec.total_timeout_seconds,
                    "multiscale_widths_seconds": list(spec.multiscale_widths_seconds),
                }
                for spec in workload_specs(url)
            ],
            "runtime_boundary": RUNTIME_BOUNDARY,
        },
        "runs": runs,
        "natural_variation": natural_variation,
        "workload_summaries": workload_summaries,
        "reproduction": reproduction,
    }


def study_result_value(document: dict[str, object]) -> StudyResults:
    run_values: list[StudyRunRecord] = []
    for item in cast(list[dict[str, object]], document["runs"]):
        champions = tuple(frozen(value) for value in cast(list[JsonObject], item["family_champions"]))
        run_values.append(
            StudyRunRecord(
                execution_order=cast(int, item["execution_order"]),
                run_id=cast(str, item["run_id"]),
                key=frozen(cast(JsonObject, item["key"])),
                config_path=cast(str, item["config_path"]),
                run_directory=cast(str, item["run_directory"]),
                transfer_evidence_directory=cast(str, item["transfer_evidence_directory"]),
                elapsed_seconds=cast(float, item["elapsed_seconds"]),
                reuse=frozen(cast(JsonObject, item["reuse"])),
                cleanup_verified=cast(bool, item["cleanup_verified"]),
                transfer_responses=tuple(frozen(value) for value in cast(list[JsonObject], item["transfer_responses"])),
                artifact_sha256=frozen(cast(JsonObject, item["artifact_sha256"])),
                reference=frozen(cast(JsonObject, item["reference"])),
                generated=frozen(cast(JsonObject, item["generated"])),
                family_champions=cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], champions),
                winner=frozen(cast(JsonObject, item["winner"])),
                fresh_simulation=frozen(cast(JsonObject, item["fresh_simulation"])),
                published=frozen(cast(JsonObject, item["published"])),
                raw_sequence=frozen(cast(JsonObject, item["raw_sequence"])),
            )
        )
    natural = tuple(frozen(value) for value in cast(list[JsonObject], document["natural_variation"]))
    summaries = tuple(frozen(value) for value in cast(list[JsonObject], document["workload_summaries"]))
    return StudyResults(
        schema_version=cast(int, document["schema_version"]),
        environment=frozen(cast(JsonObject, document["environment"])),
        protocol=frozen(cast(JsonObject, document["protocol"])),
        runs=tuple(run_values),
        natural_variation=cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], natural),
        workload_summaries=cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], summaries),
        reproduction=ReproductionRecord(frozen(cast(JsonObject, document["reproduction"]))),
    )


def trial_result(seed: int, value: float) -> TrialResult:
    methods = tuple(
        MethodTrialResult(name=name, score=value, diagnostics={"observation_window_seconds": 3.0, "seed": seed})
        for name in PUBLISHED_METHOD_ORDER
    )
    return TrialResult(
        seed=seed,
        aggregate_score=value,
        methods=cast(
            tuple[
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
            ],
            methods,
        ),
    )


def _evaluated_candidate(
    identifier: CandidateId, family: FamilyName, genes: tuple[int | float, ...], first_score: float, second_score: float
) -> Candidate:
    diagnostics = {name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS} if family == "markov_renewal" else {}
    trials = (
        rebuild_genetic_record(trial_result(17, first_score), model_diagnostics=diagnostics),
        rebuild_genetic_record(trial_result(29, second_score), model_diagnostics=diagnostics),
    )
    return Candidate(
        identifier=identifier,
        family=family,
        genes=genes,
        status="valid",
        fitness=math.fsum(trial.aggregate_score for trial in trials) / 2.0,
        trials=trials,
        invalid=None,
        duplicate_diagnostics=(),
    )


def terminal_checkpoint_and_best(tmp_path: Path) -> tuple[CheckpointState, BestModel, ComparisonResult]:
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(1.0, Direction.INBOUND, 100),
            TraceEvent(2.0, Direction.OUTBOUND, 200),
            TraceEvent(3.0, Direction.INBOUND, 300),
        )
    )
    config = build_base_config(
        workload_specs("https://downloads.example.test/object.bin")[0],
        repository_root=tmp_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    context = make_strategy_context(
        config,
        reference,
        3.0,
        tmp_path / "run",
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )
    population = (
        _evaluated_candidate(
            CandidateId(birth_generation=2, birth_index=3), "markov_renewal", (0.2, 0.7, 1.0, 4, 1.0), 0.5, 0.7
        ),
        _evaluated_candidate(
            CandidateId(birth_generation=2, birth_index=0), "markov_renewal", (0.25, 0.75, 0.5, 3, 1.2), 0.6, 0.6
        ),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=4), "mmpp", (1.0, 2.0, 10.0, 20.0), 0.6, 0.8),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=1), "mmpp", (1.5, 2.5, 12.0, 24.0), 0.6, 0.7),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=5), "poisson_empirical", (1.0,), 0.8, 1.0),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=2), "poisson_empirical", (1.5,), 0.7, 0.9),
    )
    state = CheckpointState(
        compatibility=context.compatibility,
        generation=2,
        population=population,
        history=(),
        rng_state=encode_rng_state(make_rng(73)),
        best_identifier=CandidateId(birth_generation=2, birth_index=5),
        best_fitness=0.9,
        consecutive_stagnation=0,
        terminal_reason="hard_limit",
        family_priority=context.compatibility.family_priority,
    )
    bounds = config.models.poisson_empirical
    assert bounds is not None
    best = make_best_model(
        get_family("poisson_empirical"),
        reference,
        (1.0,),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
        final_seed=config.run.final_seed,
        final_limits=config.generation.final,
        W=3.0,
        bounds=bounds,
    )
    comparison = compare_traces(reference, reference, 3.0, config.similarity)
    return (state, best, comparison)
