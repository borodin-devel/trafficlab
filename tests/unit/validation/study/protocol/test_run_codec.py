"""Run Codec behavior."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform as platform
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.validation_study.common as vs_common
import scripts.validation_study.evidence as vs_evidence
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.commands as vs_prereq_commands
import scripts.validation_study.prerequisites.run as vs_prereq_run
import scripts.validation_study.results.codec as vs_results_codec
import scripts.validation_study.results.reporting as vs_results_reporting
import scripts.validation_study.results.reproduction as vs_results_reproduction
import scripts.validation_study.transfer as vs_transfer
import scripts.validation_study.workloads as vs_workloads
import trafficlab.capture.docker.image as trafficlab_capture_docker_image
import trafficlab.common.config_io as trafficlab_common_config_io
from tests.support.validation_study.artifacts import (
    OfflinePrimaryBaseline,
    materialize_offline_primary_baseline,
)
from tests.support.validation_study.builders import (
    frozen,
    response_headers,
    study_result_value,
    terminal_checkpoint_and_best,
    trial_result,
    valid_prerequisite,
    valid_result_document,
)
from tests.support.validation_study.constants import CAPTURE_DOCKERFILE, CAPTURE_SCRIPT, HASH, ROOT
from tests.support.validation_study.runners import ScriptedPrerequisiteRunner, write_prerequisite_repository_inputs
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import GenerationLimits
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.fitting.genetic.evaluation import (
    ValidatedEvaluationContext,
    evaluate_candidate,
    validate_evaluation_context,
)
from trafficlab.fitting.genetic.population import derive_family_priority, initial_population
from trafficlab.fitting.genetic.strategy import make_strategy_context
from trafficlab.fitting.genetic.types import Candidate, TrialResult, rebuild_genetic_record
from trafficlab.generation.models.common import FittedModel, GenerationResult, make_rng
from trafficlab.generation.models.registry import get_family


def test_family_champions_use_terminal_valid_candidates_stable_ids_and_selection_means(tmp_path: Path) -> None:
    state, _best, _comparison = terminal_checkpoint_and_best(tmp_path)

    champions = vs_results_reporting.family_champions(state)

    assert tuple(item["family"] for item in champions) == vs_common.FAMILY_ORDER
    assert champions[0]["candidate_id"] == {"birth_generation": 2, "birth_index": 0}
    assert champions[0]["selection_seeds"] == [17, 29]
    assert champions[0]["selection_fitness"] == 0.6
    assert champions[0]["selection_score"] == {
        "aggregate": 0.6,
        "methods": {name: 0.6 for name in vs_common.PUBLISHED_METHOD_ORDER},
    }
    assert champions[1]["selection_fitness"] == 0.7
    assert champions[2]["selection_fitness"] == 0.9


def test_winner_fresh_simulation_and_published_records_remain_distinct(tmp_path: Path) -> None:
    state, best, comparison = terminal_checkpoint_and_best(tmp_path)
    final_trial = trial_result(97, 0.75)

    winner = vs_results_reporting.select_winner(state, best)
    fresh_simulation = {
        "seed": final_trial.seed,
        "score": vs_results_reporting.score_from_trial(final_trial),
        "source": "run_experiment_fit_outcome",
    }
    published = {
        "seed": 97,
        "score": vs_results_reporting.score_from_comparison(comparison),
    }

    assert winner == {
        "family": "poisson_empirical",
        "candidate_id": {"birth_generation": 2, "birth_index": 5},
        "genes": [1.0],
        "selection_fitness": 0.9,
    }
    assert fresh_simulation == {
        "seed": 97,
        "score": {"aggregate": 0.75, "methods": {name: 0.75 for name in vs_common.PUBLISHED_METHOD_ORDER}},
        "source": "run_experiment_fit_outcome",
    }
    assert published["score"] == {"aggregate": 1.0, "methods": {name: 1.0 for name in vs_common.PUBLISHED_METHOD_ORDER}}
    assert fresh_simulation != published


def test_offline_primary_baseline_materializes_independent_regular_copies(
    tmp_path: Path,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    """Each extraction mutation starts from an independent regular-file primary tree."""

    baseline = offline_primary_baselines["short"]
    first_root, first_result, first_spec, _first_workload, _first_responses = materialize_offline_primary_baseline(
        baseline
    )
    first_log = first_spec.run_directory / "run.log"
    original_log = first_log.read_bytes()
    first_log.write_bytes(b"mutated\n")

    second_root, second_result, second_spec, _second_workload, _second_responses = materialize_offline_primary_baseline(
        baseline
    )
    first_config = trafficlab_common_config_io.load_experiment(first_spec.config_path)
    second_config = trafficlab_common_config_io.load_experiment(second_spec.config_path)

    assert first_root == second_root
    assert first_result.run_directory.is_relative_to(first_root)
    assert second_result.run_directory.is_relative_to(second_root)
    assert second_result.capture.reference_path.is_relative_to(second_root)
    assert second_result.fit.best_model_path.is_relative_to(second_root)
    assert second_result.generation.generated_path.is_relative_to(second_root)
    assert second_spec.config_path.is_relative_to(second_root)
    assert second_spec.transfer_evidence_directory.is_relative_to(second_root)
    assert first_config.run.directory == first_spec.run_directory
    assert first_config.target.mounts[0].source.is_relative_to(first_root)
    assert second_config.run.directory == second_spec.run_directory
    assert second_config.target.mounts[0].source.is_relative_to(second_root)
    assert (second_spec.run_directory / "run.log").read_bytes() == original_log


def test_primary_extraction_reloads_nine_artifacts_and_proves_raw_quantized_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    repository_root, result, spec, workload, transfer_responses = materialize_offline_primary_baseline(
        offline_primary_baselines["short"]
    )
    authoritative_trial = result.fit.outcome.final_trials[0]
    observed_trials: list[TrialResult] = []
    real_reconstruct = vs_evidence.reconstruct_science

    def reconstruct(
        evidence: object,
        fresh_simulation: TrialResult,
        *,
        generated_path: Path,
    ) -> object:
        observed_trials.append(fresh_simulation)
        return real_reconstruct(evidence, fresh_simulation, generated_path=generated_path)  # type: ignore[arg-type]

    monkeypatch.setattr(vs_evidence, "reconstruct_science", reconstruct)

    def reject_evaluate(
        _candidate: Candidate,
        _context: ValidatedEvaluationContext,
        _seed: int,
    ) -> tuple[TrialResult, ...]:
        raise AssertionError("primary reevaluation")

    monkeypatch.setattr(vs_results_reproduction, "evaluate_final", reject_evaluate)

    record = vs_evidence.extract_primary_record(
        repository_root,
        spec,
        workload,
        result,
        1.25,
        transfer_responses,
    )

    assert tuple(item["family"] for item in record.family_champions) == vs_common.FAMILY_ORDER
    assert record.reuse == {"capture": False, "best_model": False, "generated": False, "similarity": False}
    assert record.cleanup_verified is True
    assert set(record.artifact_sha256) == set(vs_common.ARTIFACT_NAMES)
    assert record.fresh_simulation["source"] == "run_experiment_fit_outcome"
    assert observed_trials == [authoritative_trial]
    assert observed_trials[0] is authoritative_trial
    assert record.raw_sequence == {
        "seed": 97,
        "observation_window_seconds": 10.0,
        "trial_event_count": len(result.fit.outcome.final_trials) and len(result.generation.trace),
        "final_event_count": len(result.generation.trace),
        "raw_events_equal": True,
        "fresh_simulation_score_reproduced": True,
        "reparsed_event_count": len(result.generation.trace),
        "reparsed_matches_quantized": True,
    }
    assert sorted(path.name for path in spec.run_directory.iterdir()) == sorted(vs_common.ARTIFACT_NAMES)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-artifact",
        "tenth-run-entry",
        "reused-stage",
        "checkpoint-mismatch",
        "history-mismatch",
        "best-model-mismatch",
        "held-out-wrong-seed",
        "raw-trial-final-differ",
        "raw-score-differ",
        "quantized-events-differ",
        "similarity-lineage-differ",
        "cleanup-not-proven",
    ],
)
def test_run_extraction_rejects_missing_malformed_inconsistent_or_reused_evidence(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    repository_root, result, spec, workload, transfer_responses = materialize_offline_primary_baseline(
        offline_primary_baselines["short"]
    )

    if mutation == "missing-artifact":
        (spec.run_directory / "run.log").unlink()
    elif mutation == "tenth-run-entry":
        (spec.run_directory / "unexpected").write_bytes(b"unexpected")
    elif mutation == "reused-stage":
        result = replace(result, capture=replace(result.capture, reused=True))
    elif mutation == "checkpoint-mismatch":
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, generation=99)))
    elif mutation == "history-mismatch":
        with (spec.run_directory / "ga_history.csv").open("ab") as stream:
            stream.write(b"stale\n")
    elif mutation == "best-model-mismatch":
        with (spec.run_directory / "best_model.json").open("ab") as stream:
            stream.write(b" ")
    elif mutation == "held-out-wrong-seed":
        trial = rebuild_genetic_record(result.fit.outcome.final_trials[0], seed=17)
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, final_trials=(trial,))))
    elif mutation == "raw-trial-final-differ":
        original_family = get_family(result.fit.best_model.family)

        class DifferingFinalFamily:
            def __getattr__(self, name: str) -> object:
                return getattr(original_family, name)

            def generate(
                self,
                model: FittedModel,
                seed: int,
                W: float,
                limits: GenerationLimits,
            ) -> GenerationResult:
                generated = original_family.generate(model, seed, W, limits)
                if limits == trafficlab_common_config_io.load_experiment(spec.config_path).generation.final:
                    first, *remaining = generated.trace.to_events()
                    changed = TraceEvent(first.timestamp, first.direction, first.frame_length + 1)
                    return replace(generated, trace=TrafficTrace.from_events((changed, *remaining)))
                return generated

        def differing_family(_name: str) -> Any:
            return DifferingFinalFamily()

        monkeypatch.setattr(
            vs_evidence,
            "get_family",
            differing_family,
            raising=False,
        )
    elif mutation == "raw-score-differ":
        original = result.fit.outcome.final_trials[0]
        aggregate = 0.0 if original.aggregate_score != 0.0 else 1.0
        trial = rebuild_genetic_record(original, aggregate_score=aggregate)
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, final_trials=(trial,))))
    elif mutation == "quantized-events-differ":
        first, *remaining = result.generation.trace.to_events()
        changed = TraceEvent(first.timestamp, first.direction, first.frame_length + 1)
        result = replace(
            result,
            generation=replace(result.generation, trace=TrafficTrace.from_events((changed, *remaining))),
        )
    elif mutation == "similarity-lineage-differ":
        assert result.comparison.input_identities is not None
        identities = result.comparison.input_identities.as_content_identities()
        identities["capture_json"] = ContentIdentity(size=identities["capture_json"].size, sha256="0" * 64)
        result = replace(result, comparison=result.comparison.with_input_identities(identities))
    elif mutation == "cleanup-not-proven":
        run_log = spec.run_directory / "run.log"
        records = [json.loads(line) for line in run_log.read_text().splitlines()]
        next(record for record in records if record.get("event") == "capture_published")["event"] = "capture_missing"
        run_log.write_text(
            "".join(f"{json.dumps(record, sort_keys=True, separators=(',', ':'))}\n" for record in records)
        )

    with pytest.raises((TrafficlabError, TypeError, ValueError)):
        vs_evidence.extract_primary_record(
            repository_root,
            spec,
            workload,
            result,
            1.25,
            transfer_responses,
        )


def test_endpoint_contract_rejects_noncredential_free_https_object_urls() -> None:
    assert vs_common.validate_endpoint_url("https://downloads.example.test/object.bin") == (
        "https://downloads.example.test/object.bin"
    )
    for value in [
        "http://example.test/object",
        "https://user@example.test/object",
        "https://example.test/object?query=1",
        "https://example.test/object#fragment",
        "https://127.0.0.1/object",
        "https:///object",
    ]:
        with pytest.raises(ValueError, match="credential-free HTTPS.*DNS hostname"):
            vs_common.validate_endpoint_url(value)


def test_validation_study_mmpp_bounds_retain_a_valid_candidate_for_a_short_observation_window(tmp_path: Path) -> None:
    url = "https://downloads.example.test/object.bin"
    config = vs_workloads.build_base_config(
        vs_workloads.workload_specs(url)[0],
        repository_root=tmp_path,
        study_id="study-1",
        url=url,
        capture_image_id=f"sha256:{'d' * 64}",
    )
    window = 0.7874600887298584
    reference = TrafficTrace.from_events(
        tuple(
            TraceEvent(
                window * index / 176,
                Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                60 if index % 2 == 0 else 100,
            )
            for index in range(177)
        )
    )
    context = make_strategy_context(
        config,
        reference,
        window,
        tmp_path / "run",
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )
    validated = validate_evaluation_context(context.evaluation)
    pending = initial_population(
        derive_family_priority(config.run.master_seed, config.models.enabled),
        population_size=config.genetic.population_size,
        bounds=validated.bounds,
        reference=validated.reference,
        rng=make_rng(config.run.master_seed),
    )
    evaluated = tuple(evaluate_candidate(candidate, validated) for candidate in pending)

    assert any(candidate.family == "mmpp" and candidate.status == "valid" for candidate in evaluated)


def test_scratch_files_are_exclusive_regular_0666_and_archives_are_sibling_0600(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = vs_workloads.workload_specs("https://downloads.example.test/object.bin")[0]
    mount_directory = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / "study-1"
    mount_directory.mkdir(parents=True)
    scratch = mount_directory / "short.headers"
    scratch.write_bytes(b"stale")
    run_directory = repository_root / "runs" / "validation_study" / "study-1" / "01-short-r1"
    run_directory.mkdir(parents=True)
    for name in vs_common.ARTIFACT_NAMES:
        (run_directory / name).write_bytes(b"artifact")

    prepared = vs_transfer.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)

    assert tuple(prepared) == ("short.headers",)
    path, inode = prepared["short.headers"]
    assert path == scratch
    assert inode == path.lstat().st_ino
    assert stat.S_ISREG(path.lstat().st_mode)
    assert stat.S_IMODE(path.lstat().st_mode) == 0o666
    assert path.read_bytes() == b""
    header_bytes = response_headers(0, 1048575)
    path.write_bytes(header_bytes)

    responses = vs_transfer.archive_transfer_evidence(
        repository_root,
        "study-1",
        "01-short-r1",
        workload,
        prepared,
        object_size_bytes=4_194_304,
    )

    archive = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "01-short-r1"
        / "short.headers"
    )
    assert responses == (
        {
            "transfer_index": 0,
            "requested_start": 0,
            "requested_end": 1048575,
            "status": 206,
            "content_length": 1048576,
            "content_range": "bytes 0-1048575/4194304",
            "header_archive_path": "examples/validation_study/.study-work/evidence/study-1/01-short-r1/short.headers",
            "header_sha256": hashlib.sha256(header_bytes).hexdigest(),
            "scratch_precreate_mode": 438,
            "archive_mode": 384,
            "inode_preserved": True,
        },
    )
    assert archive.read_bytes() == header_bytes
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert not path.exists()
    assert not archive.is_relative_to(run_directory)
    assert set(item.name for item in run_directory.iterdir()) == set(vs_common.ARTIFACT_NAMES)

    path.symlink_to(repository_root / "outside")
    with pytest.raises(ValueError, match="symlink|regular"):
        vs_transfer.prepare_transfer_scratch(repository_root, "study-1", "02-short-r2", workload)
    assert path.is_symlink()


def test_range_header_parser_validates_redirect_chain_final_status_range_and_length(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = vs_workloads.workload_specs("https://downloads.example.test/object.bin")[1]
    prepared = vs_transfer.prepare_transfer_scratch(repository_root, "study-1", "02-streaming-r1", workload)
    redirect = b"HTTP/1.1 302 Found\r\nLocation: /stable/object.bin\r\nContent-Length: 0\r\n\r\n"
    header_bytes = response_headers(0, 4194303, prefix=redirect)
    prepared["streaming.headers"][0].write_bytes(header_bytes)

    responses = vs_transfer.archive_transfer_evidence(
        repository_root,
        "study-1",
        "02-streaming-r1",
        workload,
        prepared,
        object_size_bytes=4_194_304,
    )

    assert responses[0]["status"] == 206
    assert responses[0]["content_range"] == "bytes 0-4194303/4194304"
    assert responses[0]["content_length"] == 4_194_304
    assert responses[0]["header_sha256"] == hashlib.sha256(header_bytes).hexdigest()


@pytest.mark.parametrize("kind", ["prerequisites", "results"])
def test_official_publication_collision_preserves_winner_and_cleans_private_temp(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    destination = repository_root / "examples" / "validation_study" / f"{kind}.json"
    destination.parent.mkdir(parents=True)
    winner = b"concurrent publisher\n"
    linked_sources: list[Path] = []

    def collide(source: str | Path, target: str | Path, *_args: object, **_kwargs: object) -> None:
        temporary = Path(source)
        linked_sources.append(temporary)
        assert temporary.parent == destination.parent
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        Path(target).write_bytes(winner)
        raise FileExistsError("simulated publication race")

    monkeypatch.setattr(os, "link", collide)
    if kind == "prerequisites":
        prerequisite_value = valid_prerequisite()

        def publish() -> None:
            vs_prereq_run.publish_prerequisites(
                destination,
                prerequisite_value,
                repository_root=repository_root,
            )

    else:
        result_value = study_result_value(valid_result_document(repository_root))

        def publish() -> None:
            vs_results_codec.publish_results(
                destination,
                result_value,
                repository_root=repository_root,
            )

    with pytest.raises(TrafficlabError, match="already exists"):
        publish()

    assert destination.read_bytes() == winner
    assert len(linked_sources) == 1
    assert not tuple(destination.parent.glob(f".{destination.name}.*"))


def test_support_publication_refuses_an_existing_target_before_creating_a_temp(tmp_path: Path) -> None:
    destination = tmp_path / "results.json"
    destination.write_bytes(b"winner\n")

    with pytest.raises(TrafficlabError, match="already exists"):
        vs_common.publish_support_json(
            destination,
            b"candidate\n",
            validate=lambda _content: None,
        )

    assert destination.read_bytes() == b"winner\n"
    assert not tuple(tmp_path.glob(".results.json.*"))


def test_support_publication_closes_and_cleans_a_temp_when_fdopen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "results.json"

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated fdopen failure")

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="fdopen"):
        vs_common.publish_support_json(
            destination,
            b"candidate\n",
            validate=lambda _content: None,
        )

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".results.json.*"))


def test_result_codec_rejects_nonoracle_workload_argv(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    protocol = cast(dict[str, object], document["protocol"])
    workload = cast(list[dict[str, object]], protocol["workloads"])[0]
    workload["argv"] = ["--url", "https://downloads.example.test/object.bin"]
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="workload definition"):
        vs_results_codec.parse_study_results(invalid, repository_root=repository_root)


def test_result_codec_rejects_a_nonbest_family_champion_as_winner(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    run = cast(list[dict[str, object]], document["runs"])[0]
    champion = cast(list[dict[str, object]], run["family_champions"])[0]
    run["winner"] = {
        key: copy.deepcopy(champion[key]) for key in ("family", "candidate_id", "genes", "selection_fitness")
    }
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="overall best"):
        vs_results_codec.parse_study_results(invalid, repository_root=repository_root)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-primary-order",
        "duplicate-run-key",
        "missing-family",
        "wrong-method-order",
        "nullable-value",
        "stale-statistic",
        "wrong-pair-average",
        "winner-count-mismatch",
        "wrong-reproduction-source",
        "extra-artifact-hash",
        "true-reuse",
        "wrong-guard",
    ],
)
def test_result_codec_rejects_nested_schema_and_cross_record_inconsistency(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    runs = cast(list[dict[str, object]], document["runs"])
    protocol = cast(dict[str, object], document["protocol"])
    summaries = cast(list[dict[str, object]], document["workload_summaries"])
    natural = cast(list[dict[str, object]], document["natural_variation"])
    reproduction = cast(dict[str, object], document["reproduction"])

    if mutation == "wrong-primary-order":
        runs[0], runs[1] = runs[1], runs[0]
    elif mutation == "duplicate-run-key":
        runs[1]["key"] = copy.deepcopy(runs[0]["key"])
    elif mutation == "missing-family":
        cast(list[object], runs[0]["family_champions"]).pop()
    elif mutation == "wrong-method-order":
        protocol["methods"] = list(reversed(cast(list[object], protocol["methods"])))
    elif mutation == "nullable-value":
        runs[0]["elapsed_seconds"] = None
    elif mutation == "stale-statistic":
        cast(dict[str, object], summaries[0]["runtime"])["mean"] = 99.0
    elif mutation == "wrong-pair-average":
        first_pair = cast(list[dict[str, object]], natural[0]["pairs"])[0]
        cast(dict[str, object], first_pair["symmetric"])["aggregate"] = 0.0
    elif mutation == "winner-count-mismatch":
        cast(dict[str, object], summaries[0]["winner_counts"])["mmpp"] = 2
    elif mutation == "wrong-reproduction-source":
        reproduction["source_key"] = {"workload": "short", "repeat": 2}
    elif mutation == "extra-artifact-hash":
        cast(dict[str, object], runs[0]["artifact_sha256"])["extra"] = HASH
    elif mutation == "true-reuse":
        cast(dict[str, object], runs[0]["reuse"])["capture"] = True
    elif mutation == "wrong-guard":
        cast(list[str], reproduction["guard_command"]).pop()

    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(ValueError):
        vs_results_codec.parse_study_results(invalid, repository_root=repository_root)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("integer-gene", "exact.*float"),
        ("escaping-path", "repository-relative"),
        ("score-over-one", r"\[0.0, 1.0\]"),
        ("wrong-trace-count", "packet totals"),
        ("wrong-artifact-set", "exact keys"),
        ("raw-window-lineage", "observation windows"),
        ("raw-count-lineage", "event counts"),
    ],
)
def test_result_codec_rejects_scalar_path_gene_trace_and_artifact_violations(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    run = cast(list[dict[str, object]], document["runs"])[0]
    if mutation == "integer-gene":
        champion = cast(list[dict[str, object]], run["family_champions"])[2]
        champion["genes"] = [1]
    elif mutation == "escaping-path":
        run["config_path"] = "../escape.toml"
    elif mutation == "score-over-one":
        fresh_simulation = cast(dict[str, object], run["fresh_simulation"])
        cast(dict[str, object], fresh_simulation["score"])["aggregate"] = 1.1
    elif mutation == "wrong-trace-count":
        reference = cast(dict[str, object], run["reference"])
        cast(dict[str, object], reference["packet_totals"])["outbound"] = 99
    elif mutation == "wrong-artifact-set":
        cast(dict[str, object], run["artifact_sha256"]).pop("run.log")
    elif mutation == "raw-window-lineage":
        generated = cast(dict[str, object], run["generated"])
        generated["observation_window_seconds"] = 99.0
    elif mutation == "raw-count-lineage":
        raw_sequence = cast(dict[str, object], run["raw_sequence"])
        raw_sequence["trial_event_count"] = 99
        raw_sequence["final_event_count"] = 99
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match=message):
        vs_results_codec.parse_study_results(invalid, repository_root=repository_root)


def test_prerequisite_commands_are_exact_guarded_serial_argv_with_relative_projection(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    evidence = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    docker = (
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
        f"{evidence}/docker.xml",
    )
    internet = (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
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
        f"{evidence}/internet.xml",
    )

    assert vs_prereq_commands.docker_matrix_argv(study_id) == docker
    assert vs_prereq_commands.internet_smoke_argv(study_id, url) == internet
    for kind, checked in (("docker_matrix", docker), ("internet_smoke", internet)):
        live: list[str] = list(checked)
        live[-1] = str(repository_root / checked[-1])
        assert vs_prereq_commands.command_live_argv(
            cast(vs_common.PrerequisiteCommandKind, kind), checked, repository_root=repository_root
        ) == tuple(live)
        assert (
            vs_prereq_commands._project_command_argv(  # pyright: ignore[reportPrivateUsage]
                cast(vs_common.PrerequisiteCommandKind, kind), live, repository_root=repository_root
            )
            == checked
        )
        tampered = list(checked)
        tampered[-2] = "--xml"
        with pytest.raises(ValueError, match="exact"):
            vs_prereq_commands.command_live_argv(
                cast(vs_common.PrerequisiteCommandKind, kind), tampered, repository_root=repository_root
            )


@pytest.mark.parametrize(
    "invalid",
    [
        b'<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
        b'<testsuite tests="2" failures="0" errors="0" skipped="1"/>',
        b'<testsuite tests="2" failures="1" errors="0" skipped="0"/>',
        b"not xml",
    ],
)
def test_junit_parser_requires_positive_all_passed_selection(invalid: bytes) -> None:
    assert vs_prereq_commands.parse_junit_counts(
        b'<testsuites tests="3" failures="0" errors="0" skipped="0">'
        b'<testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
    ) == {"total": 3, "passed": 3, "failed": 0, "errors": 0, "skipped": 0}
    with pytest.raises(ValueError, match="JUnit|test"):
        vs_prereq_commands.parse_junit_counts(invalid)


def test_capability_records_digest_ids_default_user_range_canary_modes_and_cleanup(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root)
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    result = vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: now,
    )

    assert result.git_commit == "c" * 40
    assert result.tools == {
        "python_version": "3.12.3",
        "trafficlab_version": "0.1.0",
        "docker_engine_version": "27.0.0",
        "docker_compose_version": "2.29.0",
        "host_architecture": platform.machine(),
        "kernel_release": platform.release(),
        "platform": platform.platform(),
        "python_implementation": "CPython",
        "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    }
    assert result.images == {
        "target_reference": vs_common.TARGET_REFERENCE,
        "target_image_id": runner.target_id,
        "target_repo_digests": tuple(sorted(("curlimages/curl@sha256:" + "f" * 64, vs_common.TARGET_REFERENCE))),
        "target_config_user": "curl_user",
        "capture_image_id": runner.capture_id,
        "capture_dockerfile_sha256": hashlib.sha256(CAPTURE_DOCKERFILE).hexdigest(),
        "capture_script_sha256": hashlib.sha256(CAPTURE_SCRIPT).hexdigest(),
    }
    capability = result.capability
    assert capability["status"] == 206
    assert capability["object_size_bytes"] == 4_194_304
    assert capability["redirect_count"] == 1
    assert capability["final_url"] == runner.final_url
    assert capability["container_id"] == runner.container_id
    assert capability["used_image_default_user"] is True
    assert capability["container_cleanup_verified"] is True
    assert capability["mount_directory_mode"] == 0o755
    assert capability["canary_file_mode"] == 0o666
    assert capability["canary_archive_mode"] == 0o600
    assert (
        capability["stdout_sha256"]
        == hashlib.sha256(f"status=206\nsize=1\nurl={runner.final_url}\nredirects=1\n".encode()).hexdigest()
    )
    assert capability["stderr_sha256"] == hashlib.sha256(b"curl diagnostic\n").hexdigest()
    assert stat.S_IMODE((runner.evidence / "capability.cid").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.headers").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.stdout").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.stderr").stat().st_mode) == 0o600
    assert not (runner.mount / ".capability.headers").exists()
    assert [command["tests"] for command in result.commands] == [
        frozen({"total": 7, "passed": 7, "failed": 0, "errors": 0, "skipped": 0}),
        frozen({"total": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0}),
    ]
    docker_live = vs_prereq_commands.command_live_argv(
        "docker_matrix",
        vs_prereq_commands.docker_matrix_argv(runner.study_id),
        repository_root=repository_root,
    )
    internet_live = vs_prereq_commands.command_live_argv(
        "internet_smoke",
        vs_prereq_commands.internet_smoke_argv(runner.study_id, runner.url),
        repository_root=repository_root,
    )
    assert [command for command, _timeout in runner.calls] == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        (
            "git",
            "check-ignore",
            "-z",
            "--stdin",
        ),
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
        ("docker", "image", "pull", vs_common.TARGET_REFERENCE),
        ("docker", "image", "inspect", vs_common.TARGET_REFERENCE),
        trafficlab_capture_docker_image.cold_capture_build_argv(
            f"trafficlab-validation-{runner.study_id}:capture",
            runner.evidence / "capture.iid",
        ),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{runner.capability_name}$",
            "--format",
            "{{.ID}}",
        ),
        runner.expected_capability(),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"id={runner.container_id}",
            "--format",
            "{{.ID}}",
        ),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{runner.capability_name}$",
            "--format",
            "{{.ID}}",
        ),
        docker_live,
        internet_live,
        ("docker", "image", "rm", "--force", f"trafficlab-validation-{runner.study_id}:capture"),
    ]
    assert [timeout for _command, timeout in runner.calls] == [
        20.0,
        20.0,
        20.0,
        20.0,
        20.0,
        300.0,
        300.0,
        300.0,
        20.0,
        45.0,
        20.0,
        20.0,
        1230.0,
        630.0,
        300.0,
    ]
    for command, prefix, stdout, stderr in (
        (result.commands[0], "docker", b"docker pass\n", b""),
        (result.commands[1], "internet", b"internet pass\n", b""),
    ):
        junit = (runner.evidence / f"{prefix}.xml").read_bytes()
        assert command["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()
        assert command["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
        assert command["junit_sha256"] == hashlib.sha256(junit).hexdigest()
        for suffix in ("stdout", "stderr", "xml"):
            assert stat.S_IMODE((runner.evidence / f"{prefix}.{suffix}").stat().st_mode) == 0o600
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    assert (
        vs_prereq_codec.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root)
        == result
    )
    for name, content_hash in result.config_sha256.items():
        config_path = repository_root / "examples" / "validation_study" / "configs" / f"{name}.toml"
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == content_hash
        assert trafficlab_common_config_io.load_experiment(config_path).capture.image == runner.capture_id
