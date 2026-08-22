"""Science behavior."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.validation_study.audit.common as vs_audit_common
import scripts.validation_study.audit.lifecycle as vs_audit_lifecycle
import scripts.validation_study.candidate.held_out as vs_candidate_held_out
import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.records as vs_records
import scripts.validation_study.results.codec as vs_results_codec
import scripts.validation_study.results.reporting as vs_results_reporting
import scripts.validation_study.results.reproduction as vs_results_reproduction
import scripts.validation_study.workloads as vs_workloads
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from tests.support.validation_study.artifacts import (
    candidate_index,
    rewrite_candidate_manifest,
    write_candidate_index,
    write_canonical_json,
)
from tests.support.validation_study.constants import CAPTURE_BYTES, FIT_FIXTURE, REFERENCE_BYTES, ROOT
from tests.support.validation_study.repository import copy_validation_study_candidate
from tests.unit.validation.study.audit._audit_support import (
    offline_published_study,
)
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_configuration_pair, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import TraceEvent, normalize_reference, parse_capture_metadata
from trafficlab.comparison.codec import parse_comparison_result


def test_local_audit_revalidates_report_checkpoint_artifacts_and_lineage_without_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, result_path, report_path = offline_published_study(repository_root)
    prerequisite = vs_prereq_codec.parse_prerequisite_results(
        prerequisite_path.read_bytes(), repository_root=repository_root
    )

    def reject_external(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("external")

    monkeypatch.setattr(subprocess, "run", reject_external)
    before = {path: path.read_bytes() for path in repository_root.rglob("*") if path.is_file()}
    vs_results_reproduction.audit_published_study(
        repository_root=repository_root,
        prerequisite_path=prerequisite_path,
        result_path=result_path,
        report_path=report_path,
    )
    after = {path: path.read_bytes() for path in repository_root.rglob("*") if path.is_file()}
    assert before == after
    for missing in (
        vs_common.REPORT_HEADINGS[0],
        prerequisite.study_id,
        prerequisite.git_commit,
        cast(str, prerequisite.images["target_image_id"]),
        cast(str, prerequisite.images["capture_image_id"]),
        vs_common.PRIMARY_ORDER[0][1],
        "10-streaming-r2-reproduction",
    ):
        original = report_path.read_text(encoding="utf-8")
        report_path.write_text(original.replace(missing, "removed", 1), encoding="utf-8")
        with pytest.raises(TrafficlabError, match="report"):
            vs_results_reproduction.audit_published_study(
                repository_root=repository_root,
                prerequisite_path=prerequisite_path,
                result_path=result_path,
                report_path=report_path,
            )
        report_path.write_text(original, encoding="utf-8")

    results = vs_results_codec.parse_study_results(result_path.read_bytes(), repository_root=repository_root)
    checkpoint_path = repository_root / results.runs[0].run_directory / "checkpoint.json"
    checkpoint_content = checkpoint_path.read_bytes()
    checkpoint_path.write_bytes(checkpoint_content + b" ")
    with pytest.raises(TrafficlabError):
        vs_results_reproduction.audit_published_study(
            repository_root=repository_root,
            prerequisite_path=prerequisite_path,
            result_path=result_path,
            report_path=report_path,
        )
    checkpoint_path.write_bytes(checkpoint_content)


def test_offline_audit_reconstructs_held_out_without_calling_the_producer_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auditor derives the independent held-out horizon from retained public bytes."""

    repository, candidate = copy_validation_study_candidate(tmp_path)

    def producer_boundary_must_not_run(**_kwargs: object) -> vs_records.HeldOutEvaluation:
        raise AssertionError("auditor delegated held-out reconstruction to the producer boundary")

    monkeypatch.setattr(vs_candidate_held_out, "evaluate_study_held_out", producer_boundary_must_not_run, raising=False)
    assert vs_audit_lifecycle.audit_bundle(candidate, repository=repository).bundle == candidate


def test_offline_bundle_audit_reports_the_canonical_jsonl_owner_diagnostic(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    log_path = candidate / "training" / "short" / "r1" / "run.log"
    log_path.write_bytes(b'{"event": "fixture"}\n')
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.detail,
        outcome.affected_evidence,
        outcome.corrective_action,
    ) == (
        "artifact_corrupt",
        "run log record is not canonical JSONL",
        "training/short/r1/run.log",
        "restore canonical run log",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("directory", "artifact_foreign"),
        ("configuration_path", "artifact_foreign"),
        ("seeds", "scientific_semantics_incompatible"),
        ("run_configuration", "artifact_corrupt"),
        ("run_configuration_semantics", "artifact_foreign"),
        ("reconstruction", "artifact_corrupt"),
        ("history", "artifact_foreign"),
        ("winner", "artifact_foreign"),
        ("comparison_parse", "artifact_corrupt"),
        ("comparison", "artifact_foreign"),
        ("index_identity", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_training_record_and_reconstruction_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    training = cast(list[dict[str, object]], index["training"])
    record = training[0]
    run = candidate / "training" / "short" / "r1"

    if case == "directory":
        record["directory"] = "training/short/r2"
        write_candidate_index(candidate, index)
    elif case == "configuration_path":
        record["portable_config"] = "configs/training-short-r1.realized.toml"
        write_candidate_index(candidate, index)
    elif case == "seeds":
        protocol_path = candidate / "protocol.json"
        protocol = cast(dict[str, object], json.loads(protocol_path.read_text(encoding="utf-8")))
        protocol["selection_seeds"] = [18, 30]
        write_canonical_json(protocol_path, protocol)
    elif case == "run_configuration":
        (run / "experiment.toml").write_bytes((candidate / "configs" / "training-short-r1.realized.toml").read_bytes())
    elif case == "run_configuration_semantics":
        document = tomllib.loads((run / "experiment.toml").read_text(encoding="utf-8"))
        cast(dict[str, Any], document["run"])["master_seed"] = 74
        (run / "experiment.toml").write_bytes(render_effective_config(ExperimentConfig.model_validate(document)))
    elif case == "reconstruction":
        (run / "capture.json").write_bytes(b"{}\n")
    elif case == "history":
        (run / "ga_history.csv").write_bytes(b"unexpected-history\n")
    elif case == "winner":
        (run / "best_model.json").write_bytes(
            (candidate / "training" / "short" / "r2" / "best_model.json").read_bytes()
        )
    elif case == "comparison_parse":
        (run / "similarity.json").write_bytes(b"{}\n")
    elif case == "comparison":
        (run / "similarity.json").write_bytes(
            (candidate / "training" / "short" / "r2" / "similarity.json").read_bytes()
        )
    else:
        identity = cast(dict[str, object], record["reference_identity"])
        identity["sha256"] = "0" * 64
        write_candidate_index(candidate, index)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("field", "expected_kind"),
    (
        ("runtime", "artifact_foreign"),
        ("bootstrap", "artifact_foreign"),
        ("winner", "artifact_foreign"),
        ("weights", "artifact_corrupt"),
        ("invalid_chromosome", "artifact_foreign"),
        ("natural_variation", "artifact_corrupt"),
        ("natural_reverse_null", "artifact_corrupt"),
        ("natural_reverse_missing", "artifact_corrupt"),
        ("natural_excluded", "artifact_corrupt"),
    ),
)
def test_offline_bundle_audit_recomputes_each_report_input_family(
    tmp_path: Path,
    field: str,
    expected_kind: str,
) -> None:
    """Report inputs are independently reconstructed rather than trusted as producer output."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    path = candidate / "report_inputs.json"
    document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    if field == "runtime":
        records = cast(list[dict[str, object]], document["runtime_winner_variance"])
        runtime = cast(dict[str, object], records[0]["runtime_seconds"])
        runtime["mean"] = cast(float, runtime["mean"]) + 1.0
    elif field == "bootstrap":
        records = cast(list[dict[str, object]], document["runtime_winner_variance"])
        runtime = cast(dict[str, object], records[0]["runtime_seconds"])
        bootstrap = cast(dict[str, object], runtime["bootstrap"])
        lower = cast(float, bootstrap["lower_bound"])
        upper = cast(float, bootstrap["upper_bound"])
        bootstrap["lower_bound"] = lower + (upper - lower) / 2.0
    elif field == "winner":
        records = cast(list[dict[str, object]], document["runtime_winner_variance"])
        winners = cast(dict[str, object], records[0]["winner_family_counts"])
        winners["mmpp"] = cast(int, winners["mmpp"]) + 1
    elif field == "weights":
        records = cast(list[dict[str, object]], document["controlled_weight_analysis"])
        records[0]["alternative_aggregate"] = cast(float, records[0]["alternative_aggregate"]) + 1.0
    elif field == "invalid_chromosome":
        records = cast(list[dict[str, object]], document["invalid_chromosome_diagnostics"])
        limits = cast(dict[str, object], records[0]["trial_limits"])
        limits["max_packets"] = cast(int, limits["max_packets"]) + 1
    elif field == "natural_variation":
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        forward = cast(dict[str, object], pairs[0]["forward"])
        forward["aggregate"] = cast(float, forward["aggregate"]) + 1.0
    elif field == "natural_reverse_null":
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        pairs[0]["reverse"] = None
    elif field == "natural_reverse_missing":
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        del pairs[0]["reverse"]
    else:
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        pairs[0]["excluded"] = True
    write_canonical_json(path, document)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (expected_kind, "publication", "report_inputs.json", "not_published", "primary")


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("binding", "artifact_foreign"),
        ("configuration", "artifact_foreign"),
        ("training_reference", "artifact_foreign"),
        ("reconstruction", "artifact_corrupt"),
        ("outputs", "artifact_foreign"),
        ("record", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_independent_held_out_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    held = cast(list[dict[str, object]], index["held_out"])[0]
    directory = candidate / cast(str, held["directory"])
    if case == "binding":
        held["training_directory"] = "training/short/r2"
        write_candidate_index(candidate, index)
    elif case == "configuration":
        for name in ("portable.toml", "realized.toml"):
            path = directory / name
            path.write_bytes(path.read_bytes().replace(b"final_seed = 97", b"final_seed = 98"))
    elif case == "training_reference":
        (directory / "reference.pcapng").write_bytes(
            (candidate / "training" / "short" / "r1" / "reference.pcapng").read_bytes()
        )
    elif case == "reconstruction":
        (directory / "capture.json").write_bytes(b"{}\n")
    elif case == "outputs":
        (directory / "generated.pcapng").write_bytes(
            (candidate / "held_out" / "streaming" / "generated.pcapng").read_bytes()
        )
    else:
        record_path = directory / "record.json"
        record = cast(dict[str, object], json.loads(record_path.read_text(encoding="utf-8")))
        record["seed"] = 98
        write_canonical_json(record_path, record)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


def test_schema_file_inventory_reports_enumeration_lstat_and_nonregular_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    def unavailable_rglob(_path: Path, _pattern: str) -> Any:
        raise OSError("enumeration unavailable")

    monkeypatch.setattr(Path, "rglob", unavailable_rglob)
    with pytest.raises(Exception, match="could not enumerate retained bundle"):
        vs_audit_common.files_for_candidate(candidate, include_manifest=False)
    monkeypatch.undo()

    regular = candidate / "regular.bin"
    regular.write_bytes(b"regular")
    original_lstat = Path.lstat

    def unavailable_lstat(path: Path) -> os.stat_result:
        if path == regular:
            raise OSError("inspection unavailable")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", unavailable_lstat)
    with pytest.raises(Exception, match="could not inspect regular.bin"):
        vs_audit_common.files_for_candidate(candidate, include_manifest=False)
    monkeypatch.undo()

    fifo = candidate / "foreign.fifo"
    os.mkfifo(fifo)
    with pytest.raises(Exception, match="must be a regular file"):
        vs_audit_common.files_for_candidate(candidate, include_manifest=False)


def test_study_held_out_evaluator_uses_the_independent_window_with_the_fixed_training_model() -> None:
    """The study-only boundary evaluates a frozen training model without weakening ordinary stage lineage checks."""
    fixture = FIT_FIXTURE
    config = load_configuration_pair(fixture / "experiment.toml").realized
    metadata = parse_capture_metadata(CAPTURE_BYTES, source=fixture / "capture.json")
    original = read_pcapng_bytes(REFERENCE_BYTES, metadata, source=fixture / "reference.pcapng")
    independent = tuple(
        TraceEvent(event.timestamp, event.direction, event.frame_length + (1 if index == 1 else 0))
        for index, event in enumerate(original)
    )
    independent_bytes = encode_pcapng(independent, metadata)

    result = vs_candidate_held_out.evaluate_study_held_out(
        model_content=(fixture / "best_model.json").read_bytes(),
        model_source=fixture / "best_model.json",
        config=config,
        capture_content=CAPTURE_BYTES,
        capture_source=fixture / "capture.json",
        reference_content=independent_bytes,
        reference_source=Path("held_out/reference.pcapng"),
    )

    comparison = parse_comparison_result(result.comparison_json)
    assert result.seed == 97
    assert result.reference_identity.sha256 != result.training_model.reference_identity.sha256
    assert comparison.input_identities is not None
    assert result.generated_identity == comparison.input_identities.as_content_identities()["generated_pcapng"]
    assert comparison.methods.keys() == vs_common.PUBLISHED_METHOD_ORDER

    with pytest.raises(TrafficlabError, match="independent held-out reference"):
        vs_candidate_held_out.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config,
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=REFERENCE_BYTES,
            reference_source=fixture / "reference.pcapng",
        )

    with pytest.raises(TypeError, match="ExperimentConfig"):
        vs_candidate_held_out.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=cast(Any, object()),
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=independent_bytes,
            reference_source=Path("held_out/reference.pcapng"),
        )

    with pytest.raises(TrafficlabError, match="final seed"):
        vs_candidate_held_out.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config.model_copy(update={"run": config.run.model_copy(update={"final_seed": 98})}),
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=independent_bytes,
            reference_source=Path("held_out/reference.pcapng"),
        )

    longer_window = tuple(
        TraceEvent(
            event.timestamp + (1.0 if index == len(independent) - 1 else 0.0), event.direction, event.frame_length
        )
        for index, event in enumerate(independent)
    )
    shorter_window = tuple(
        TraceEvent(event.timestamp * 0.8, event.direction, event.frame_length) for event in independent
    )
    for name, events in (("short", shorter_window), ("long", longer_window)):
        _normalized, held_out_window = normalize_reference(events)
        evaluation = vs_candidate_held_out.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config,
            capture_content=CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=encode_pcapng(events, metadata),
            reference_source=Path(f"held_out/{name}-window.pcapng"),
        )
        assert held_out_window != result.training_model.observation_window_seconds
        assert evaluation.observation_window_seconds == held_out_window
        assert evaluation.training_model == result.training_model
        assert evaluation.training_model_identity == result.training_model_identity
        assert evaluation.seed == result.training_model.final_seed
        assert evaluation.training_model.final_limits == result.training_model.final_limits


def test_complete_fixture_freezes_training_model_selection_and_bidirectional_variation(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_text(encoding="utf-8")))
    report_inputs = cast(dict[str, object], json.loads((candidate / "report_inputs.json").read_text(encoding="utf-8")))

    selection = cast(dict[str, object], protocol["model_selection"])
    assert protocol["schema_version"] == 4
    assert "natural_variation_windows" not in protocol
    assert selection["rule"] == "highest_best_fitness_then_lowest_repeat"
    assert {cast(dict[str, object], value)["workload"] for value in cast(list[object], selection["selected"])} == {
        "short",
        "streaming",
        "bursty",
    }
    for row in cast(list[object], report_inputs["natural_variation"]):
        document = cast(dict[str, object], row)
        assert set(document) == {"pairs", "symmetric_mean", "workload"}
        for pair in cast(list[object], document["pairs"]):
            assert set(cast(dict[str, object], pair)) == {
                "forward",
                "left_repeat",
                "reverse",
                "right_repeat",
                "symmetric_mean",
            }
            for field in ("forward", "reverse", "symmetric_mean"):
                score = cast(dict[str, object], cast(dict[str, object], pair)[field])
                assert set(score) == {"aggregate", "methods"}
                assert type(score["aggregate"]) is float
                methods = cast(dict[str, object], score["methods"])
                assert tuple(methods) == vs_common.PUBLISHED_METHOD_ORDER
                assert all(type(methods[method]) is float for method in vs_common.PUBLISHED_METHOD_ORDER)

    assert vs_audit_lifecycle.audit_bundle(candidate, repository=repository).bundle == candidate


def test_historic_schema_one_workload_oracle_retains_the_measured_short_transfer() -> None:
    """The checked r3 result remains bound to its 256 KiB measured protocol."""

    short, streaming, bursty = vs_prereq_codec._historic_schema_one_workload_argvs()  # pyright: ignore[reportPrivateUsage]

    assert "--user-agent" not in short
    assert "0-262143" in short
    assert "262144" in short
    assert "0-1048575" not in short
    assert "1048576" not in short
    assert "0-4194303" in streaming
    assert "--parallel" in bursty
    assert vs_results_codec._expected_transfers(  # pyright: ignore[reportPrivateUsage]
        "short"
    ) == ((0, 1_048_575, "short.headers"),)
    assert vs_results_codec._expected_transfers("short", historic_schema_one_result=True) == (  # pyright: ignore[reportPrivateUsage]
        (0, 262_143, "short.headers"),
    )
    assert vs_results_codec._workload_widths("short") == (0.001, 0.01)  # pyright: ignore[reportPrivateUsage]
    assert vs_results_codec._workload_widths("short", historic_schema_one_result=True) == (  # pyright: ignore[reportPrivateUsage]
        0.001,
        0.01,
    )


@pytest.mark.parametrize("workload", ("short", "streaming"))
def test_historic_schema_one_result_does_not_follow_current_workload_metadata(
    monkeypatch: pytest.MonkeyPatch,
    workload: vs_common.WorkloadName,
) -> None:
    """The sole preserved result is bound to its complete primary and reproduction profiles."""

    current_workload_specs = vs_workloads.workload_specs

    def changed_current_workloads(url: str) -> tuple[vs_workloads.WorkloadSpec, ...]:
        return tuple(
            replace(
                specification,
                workload_timeout_seconds=36.0,
                total_timeout_seconds=91.0,
                multiscale_widths_seconds=(0.002, 0.02),
            )
            if specification.name == workload
            else specification
            for specification in current_workload_specs(url)
        )

    monkeypatch.setattr(vs_workloads, "workload_specs", changed_current_workloads)
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()

    assert vs_results_codec.parse_study_results(content, repository_root=ROOT).protocol["study_id"] == (
        "validation-study-20260814-ovh-r3"
    )


def test_historic_descriptive_accepts_legacy_shape_without_weakening_current() -> None:
    """Only the exact retained historic result may omit recomputed bootstrap evidence."""
    observations = [1, 2, 4]
    current = vs_results_reporting.descriptive_statistics(observations)
    historic = copy.deepcopy(current)
    historic.pop("bootstrap")

    assert (
        vs_results_reporting._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            historic,
            name="historic descriptive",
            observations=observations,
            historic_schema_one_result=True,
        )
        == historic
    )
    assert (
        vs_results_reporting._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            historic,
            name="historic descriptive without sources",
            historic_schema_one_result=True,
        )
        == historic
    )
    assert (
        vs_results_reporting._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            current,
            name="current descriptive without sources",
        )
        == current
    )
    with pytest.raises(ValueError, match="bootstrap"):
        vs_results_reporting._validate_descriptive(  # pyright: ignore[reportPrivateUsage]
            historic,
            name="current descriptive",
            observations=observations,
        )


def test_checked_study_result_uses_canonical_fresh_simulation_records() -> None:
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    document = cast(dict[str, object], json.loads(content))
    capability = cast(dict[str, object], cast(dict[str, object], document["protocol"])["capability"])
    argv = cast(list[str], capability["argv"])
    assert "--user-agent" not in argv
    result = vs_results_codec.parse_study_results(content, repository_root=ROOT)

    assert b'"fresh_simulation"' in content
    assert b'"held_out"' not in content
    assert vs_results_codec.render_study_results(result) == content

    near_miss = copy.deepcopy(document)
    near_miss_capability = cast(dict[str, object], cast(dict[str, object], near_miss["protocol"])["capability"])
    near_miss_argv = cast(list[str], near_miss_capability["argv"])
    near_miss_argv[near_miss_argv.index("--max-time") + 1] = "31"
    with pytest.raises(ValueError, match="capability argv"):
        vs_results_codec.parse_study_results(
            json.dumps(near_miss, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=ROOT,
        )

    workload_near_miss = copy.deepcopy(document)
    first_workload = cast(
        dict[str, object], cast(list[object], cast(dict[str, object], workload_near_miss["protocol"])["workloads"])[0]
    )
    workload_argv = cast(list[str], first_workload["argv"])
    workload_argv[workload_argv.index("--max-time") + 1] = "31"
    with pytest.raises(ValueError, match="short workload definition"):
        vs_results_codec.parse_study_results(
            json.dumps(workload_near_miss, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=ROOT,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("current-capability", "capability argv"),
        ("current-workload", "short workload definition"),
        ("all-current", "capability argv"),
        ("wrong-study-id", "historic schema-1 protocol identity"),
        ("wrong-url", "historic schema-1 protocol identity"),
    ),
)
def test_historic_schema_one_protocol_is_one_atomic_identity(mutation: str, message: str) -> None:
    """The sole legacy result cannot combine independent current and historic command projections."""
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    document = copy.deepcopy(cast(dict[str, object], json.loads(content)))
    protocol = cast(dict[str, object], document["protocol"])
    study_id = cast(str, protocol["study_id"])
    url = cast(str, protocol["url"])
    capability = cast(dict[str, object], protocol["capability"])
    workloads = cast(list[dict[str, object]], protocol["workloads"])
    current_capability = vs_prereq_codec.build_expected_capability_argv(
        study_id,
        url,
    )
    current_workloads = vs_workloads.workload_specs(url)

    if mutation in {"current-capability", "all-current"}:
        capability["argv"] = list(current_capability)
    if mutation in {"current-workload", "all-current"}:
        workloads[0]["argv"] = list(current_workloads[0].argv)
    if mutation == "all-current":
        for workload, current in zip(workloads, current_workloads, strict=True):
            workload["argv"] = list(current.argv)
    if mutation == "wrong-study-id":
        protocol["study_id"] = "legacy-study"
    if mutation == "wrong-url":
        protocol["url"] = "https://downloads.example.test/other.bin"

    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    with pytest.raises(ValueError, match=message):
        vs_results_codec.parse_study_results(invalid, repository_root=ROOT)


def test_current_protocol_rejects_a_capability_projection_without_the_package_user_agent() -> None:
    content = (ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    current = cast(dict[str, object], json.loads(content))
    environment = cast(dict[str, object], current["environment"])
    environment["git_commit"] = "c" * 40

    capability = cast(dict[str, object], cast(dict[str, object], current["protocol"])["capability"])
    argv = cast(list[str], capability["argv"])
    assert "--user-agent" not in argv

    with pytest.raises(ValueError, match="capability argv"):
        vs_results_codec.parse_study_results(
            json.dumps(current, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=ROOT,
        )
