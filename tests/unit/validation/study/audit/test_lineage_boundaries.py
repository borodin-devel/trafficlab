"""Lineage Boundaries behavior."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from statistics import fmean
from typing import Any, cast

import pytest

import scripts.validation_study.audit.common as vs_audit_common
import scripts.validation_study.audit.lifecycle as vs_audit_lifecycle
import scripts.validation_study.audit.science as vs_audit_science
import scripts.validation_study.candidate.artifacts as vs_candidate_artifacts
import scripts.validation_study.candidate.reporting as vs_candidate_reporting
import trafficlab.capture.docker.image as trafficlab_capture_docker_image
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study.artifacts import (
    candidate_index,
    rewrite_candidate_manifest,
    write_candidate_index,
    write_canonical_json,
)
from tests.support.validation_study.constants import CAPTURE_BYTES, FIT_FIXTURE
from tests.support.validation_study.repository import copy_validation_study_candidate
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig, SimilarityConfig
from trafficlab.common.config_io import load_configuration_pair
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, align_generated, normalize_reference, parse_capture_metadata
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState


def test_cold_capture_build_argv_freezes_task9_reproducibility_controls(tmp_path: Path) -> None:
    """Study prerequisites use the same cold locked capture-build contract as the Docker owner."""

    assert trafficlab_capture_docker_image.cold_capture_build_argv(
        "trafficlab-validation-study-1:capture",
        tmp_path / "capture.iid",
    ) == (
        "docker",
        "build",
        "--pull",
        "--no-cache",
        "--provenance=false",
        "--platform",
        "linux/amd64",
        "--output",
        "type=image,rewrite-timestamp=true,unpack=false",
        "--tag",
        "trafficlab-validation-study-1:capture",
        "--iidfile",
        str(tmp_path / "capture.iid"),
        "docker/capture",
    )


@pytest.mark.parametrize(
    ("tag", "iidfile", "error", "message"),
    (
        (object(), Path("capture.iid"), ValueError, "capture image tag must be a nonempty string"),
        ("trafficlab-validation-study-1:capture", object(), TypeError, "iidfile must be a pathlib.Path"),
    ),
)
def test_cold_capture_build_argv_rejects_invalid_boundary_types(
    tag: object,
    iidfile: object,
    error: type[Exception],
    message: str,
) -> None:
    """The public cold-build boundary retains deterministic runtime validation."""

    with pytest.raises(error, match=message):
        trafficlab_capture_docker_image.cold_capture_build_argv(tag, iidfile)


@pytest.mark.parametrize(
    ("relative", "record"),
    (
        ("training/short/r1/run.log", b'{"event":"capture_published","stage":"capture","workload":"short"}\n'),
        ("held_out/short/run.log", b'{"event":"held_out_evaluated","stage":"compare","workload":"short"}\n'),
    ),
)
def test_offline_auditor_rejects_contradictory_required_run_log_records(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    relative: str,
    record: bytes,
) -> None:
    """Run logs are retained lineage, not merely syntax-valid diagnostics."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    with (candidate / relative).open("ab") as stream:
        stream.write(record)
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
    ) == ("artifact_foreign", "publication", relative, "not_published", "primary")


@pytest.mark.parametrize(
    ("relative", "field"),
    (("training/short/r1/run.log", "reused"), ("held_out/short/run.log", "capture_identity")),
)
def test_offline_auditor_rejects_required_run_log_identity_and_status_mismatches(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    relative: str,
    field: str,
) -> None:
    """Required run-log records bind both capture identity and completion status."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    path = candidate / relative
    records = [cast(dict[str, object], json.loads(line)) for line in path.read_bytes().splitlines()]
    capture = next(record for record in records if record["event"] == "capture_published")
    if field == "reused":
        capture["reused"] = True
    else:
        identity = cast(dict[str, object], capture["capture_identity"])
        identity["sha256"] = "0" * 64
    path.write_bytes(
        b"".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
            + b"\n"
            for record in records
        )
    )
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
    ) == ("artifact_foreign", "publication", relative, "not_published", "primary")


def test_offline_auditor_accepts_fixture_canonical_capture_platform_log_lineage() -> None:
    """The deterministic fixture records the frozen platform and successful lineage sequences."""

    bundle = VALIDATION_STUDY_CANDIDATE
    environment = cast(dict[str, object], json.loads((bundle / "environment.json").read_bytes()))
    sequences = (
        (
            "training/short/r1/run.log",
            (
                "capture_environment_identity",
                "capture_published",
                "best_model_published",
                "generated_pcapng_published",
                "comparison_succeeded",
                "run_completed",
                "validation_study_training_completed",
            ),
            ("run_completed", "validation_study_training_completed"),
        ),
        (
            "held_out/short/run.log",
            ("capture_environment_identity", "capture_published", "held_out_evaluated"),
            ("held_out_evaluated",),
        ),
    )
    for relative, events, terminal in sequences:
        records = vs_audit_common.parse_run_log_records(
            (bundle / relative).read_bytes(),
            name=relative,
        )
        vs_audit_common._require_successful_log_status(records, name=relative)  # pyright: ignore[reportPrivateUsage]
        vs_audit_common.require_terminal_log_events(records, events=terminal, name=relative)
        vs_audit_common.require_ordered_log_events(records, events=events, name=relative)

    directory = bundle / "training" / "short" / "r1"
    records = vs_audit_common.parse_run_log_records(
        (directory / "run.log").read_bytes(), name="training/short/r1/run.log"
    )

    vs_audit_common.require_capture_log_lineage(
        records,
        name="training/short/r1/run.log",
        environment=environment,
        capture=(directory / "capture.json").read_bytes(),
        reference=(directory / "reference.pcapng").read_bytes(),
        experiment=(directory / "experiment.toml").read_bytes(),
        packet_count=None,
    )


def test_offline_auditor_rejects_each_contradictory_run_log_lineage_mutation(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """Every contradictory successful-run claim independently fails the public audit."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    cases = (
        ("capture_reused", "training/short/r1/run.log"),
        ("run_failed", "held_out/short/run.log"),
        ("reused_field", "training/short/r1/run.log"),
        ("training_after_terminal", "training/short/r1/run.log"),
        ("held_out_after_terminal", "held_out/short/run.log"),
        ("training_order", "training/short/r1/run.log"),
        ("held_out_order", "held_out/short/run.log"),
    )
    for case, relative in cases:
        path = candidate / relative
        original = path.read_bytes()
        records = [json.loads(line) for line in original.splitlines()]
        if case == "capture_reused":
            records.append({"event": "capture_reused", "stage": "capture"})
        elif case == "run_failed":
            records.append({"event": "run_failed", "stage": "run"})
        elif case == "reused_field":
            next(record for record in records if record["event"] == "best_model_published")["reused"] = True
        elif case.endswith("after_terminal"):
            records.append({"event": "diagnostic", "stage": "run"})
        else:
            first_event, second_event = (
                ("capture_published", "best_model_published")
                if case == "training_order"
                else ("capture_environment_identity", "capture_published")
            )
            first = next(index for index, record in enumerate(records) if record["event"] == first_event)
            second = next(index for index, record in enumerate(records) if record["event"] == second_event)
            records[first], records[second] = records[second], records[first]
        path.write_bytes(
            b"".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                    "utf-8"
                )
                + b"\n"
                for record in records
            )
        )
        rewrite_candidate_manifest(candidate)
        try:
            with pytest.raises(TrafficlabError) as error:
                vs_audit_lifecycle.audit_bundle(candidate, repository=repository)
            outcome = error.value.failure_outcome
            assert outcome is not None
            assert (outcome.kind, outcome.affected_evidence) == ("artifact_foreign", relative)
        finally:
            path.write_bytes(original)
            rewrite_candidate_manifest(candidate)


def test_offline_auditor_reports_a_missing_ordered_run_log_event() -> None:
    """The ordering guard keeps its canonical error if a caller omits a required stage."""

    with pytest.raises(vs_audit_common.Issue) as error:
        vs_audit_common.require_ordered_log_events(
            ({"event": "capture_environment_identity"},),
            events=("capture_environment_identity", "capture_published"),
            name="training/short/r1/run.log",
        )

    assert error.value.kind == "artifact_foreign"
    assert error.value.affected == "training/short/r1/run.log"


def test_offline_auditor_rejects_incomplete_capture_log_records() -> None:
    """Canonical JSON alone is insufficient when retained capture-lineage fields are absent."""

    environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_bytes()),
    )
    capture = b"capture"
    reference = b"reference"
    experiment = b"experiment"
    environment_fields = vs_audit_common._capture_log_environment(environment)  # pyright: ignore[reportPrivateUsage]
    records = (
        {"event": "capture_environment_identity", "stage": "preflight", **environment_fields},
        {
            "capture_identity": identify_bytes(capture).as_dict(),
            "event": "capture_published",
            "experiment_identity": identify_bytes(experiment).as_dict(),
            "reference_identity": identify_bytes(reference).as_dict(),
            "reused": False,
            "stage": "capture",
        },
    )
    with pytest.raises(vs_audit_common.Issue) as incomplete:
        vs_audit_common.require_capture_log_lineage(
            records,
            name="run.log",
            environment=environment,
            capture=capture,
            reference=reference,
            experiment=experiment,
            packet_count=None,
        )
    assert incomplete.value.kind == "artifact_foreign"


@pytest.mark.parametrize(
    ("section", "expected_directory"),
    (("training", "training/short/r1"), ("held_out", "held_out/short")),
)
def test_offline_auditor_rejects_capture_lineage_that_disagrees_with_retained_bytes(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    section: str,
    expected_directory: str,
) -> None:
    """Training and held-out capture provenance cannot be substituted after capture validation."""
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    index = candidate_index(candidate)
    record = cast(list[dict[str, object]], index[section])[0]
    cast(dict[str, object], record["capture_lineage"])["capture_tool_version"] = "tampered"
    write_candidate_index(candidate, index)
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
    ) == ("artifact_foreign", "publication", expected_directory, "not_published", "primary")


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("rule", "scientific_semantics_incompatible"),
        ("count", "artifact_corrupt"),
        ("duplicate", "artifact_foreign"),
        ("mismatch", "artifact_foreign"),
        ("order", "artifact_foreign"),
    ),
)
def test_offline_auditor_reconstructs_all_training_model_selection_rejections(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    case: str,
    expected_kind: str,
) -> None:
    """The protocol's retained training-only selection is recomputed before held-out evaluation."""
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    protocol_path = candidate / "protocol.json"
    protocol = cast(dict[str, object], json.loads(protocol_path.read_text(encoding="utf-8")))
    selection = cast(dict[str, object], protocol["model_selection"])
    selected = cast(list[dict[str, object]], selection["selected"])
    if case == "rule":
        selection["rule"] = "first_training_record"
    elif case == "count":
        selection["selected"] = []
    elif case == "duplicate":
        selected[1] = copy.deepcopy(selected[0])
    elif case == "mismatch":
        selected_repeat = cast(int, selected[0]["repeat"])
        selected[0]["repeat"] = 1 if selected_repeat != 1 else 2
    else:
        selection["selected"] = list(reversed(selected))
    write_canonical_json(protocol_path, protocol)
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
    ) == (expected_kind, "publication", "protocol", "not_published", "primary")


def test_offline_auditor_uses_each_directional_similarity_settings_instance(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Independent reconstruction applies the settings belonging to each reference trace."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    original_training = vs_audit_science.rebuild_training
    original_report_inputs = vs_audit_science.rebuild_report_inputs
    original_compare = vs_audit_science.compare_traces
    expected_settings: dict[tuple[tuple[float, Direction, int], ...], SimilarityConfig] = {}
    calls: list[tuple[tuple[tuple[float, Direction, int], ...], SimilarityConfig]] = []
    recording = False

    def trace_key(events: Sequence[TraceEvent]) -> tuple[tuple[float, Direction, int], ...]:
        return tuple((event.timestamp, event.direction, event.frame_length) for event in events)

    def isolated_training(*args: Any, **kwargs: Any) -> vs_audit_common.Training:
        item = original_training(*args, **kwargs)
        return replace(item, config=item.config.model_copy(deep=True))

    def report_inputs_spy(*args: Any, **kwargs: Any) -> dict[str, object]:
        nonlocal recording
        training = cast(Sequence[vs_audit_common.Training], args[0])
        expected_settings.update(
            {trace_key(normalize_reference(item.reference)[0]): item.config.similarity for item in training}
        )
        recording = True
        try:
            return original_report_inputs(*args, **kwargs)
        finally:
            recording = False

    def comparison_spy(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        if recording:
            calls.append((trace_key(reference), settings))
        return original_compare(reference, generated, window, settings)

    monkeypatch.setattr(vs_audit_lifecycle, "rebuild_training", isolated_training)
    monkeypatch.setattr(vs_audit_lifecycle, "rebuild_report_inputs", report_inputs_spy)
    monkeypatch.setattr(vs_audit_science, "compare_traces", comparison_spy)

    assert vs_audit_lifecycle.audit_bundle(candidate, repository=repository).bundle == candidate
    assert len(calls) == 18
    assert all(settings is expected_settings[reference] for reference, settings in calls)


def test_candidate_natural_variation_derives_each_directional_reference_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Natural variation compares repeated captures at each reference-derived W, not a metric bin width."""
    base_config = load_configuration_pair(FIT_FIXTURE / "experiment.toml").realized
    config = base_config.model_copy(
        update={
            "similarity": base_config.similarity.model_copy(
                update={"max_direction_bin_cells": 2_000, "multiscale_widths_seconds": (0.001, 0.01)}
            )
        }
    )
    frozen_bin_width = max(config.similarity.multiscale_widths_seconds)
    assert frozen_bin_width == 0.01

    def trace(spacing: float) -> tuple[TraceEvent, ...]:
        return tuple(
            TraceEvent(
                timestamp=index * spacing,
                direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                frame_length=128 + index,
            )
            for index in range(20)
        )

    metadata = parse_capture_metadata(CAPTURE_BYTES, source=FIT_FIXTURE / "capture.json")

    configurations = tuple(config.model_copy(deep=True) for _ in range(3))
    assert configurations[0].similarity is not configurations[1].similarity

    def training(
        repeat: int,
        raw_reference: tuple[TraceEvent, ...],
        configuration: ExperimentConfig,
    ) -> vs_candidate_artifacts.CandidateTraining:
        reference, window = normalize_reference(raw_reference)
        return vs_candidate_artifacts.CandidateTraining(
            workload="short",
            repeat=repeat,
            directory=tmp_path / f"r{repeat}",
            config=configuration,
            contents={},
            metadata=metadata,
            reference=reference,
            observation_window_seconds=window,
            runtime_seconds=0.0,
            checkpoint=cast(CheckpointState, object()),
            comparison=cast(ComparisonResult, object()),
        )

    records = tuple(
        training(repeat, raw_reference, configurations[repeat - 1])
        for repeat, raw_reference in enumerate((trace(0.005), trace(0.03), trace(0.025)), start=1)
    )
    with pytest.raises(TrafficlabError, match="invalid generated trace: at least two events"):
        compare_traces(
            align_generated(records[0].reference, frozen_bin_width),
            align_generated(records[1].reference, frozen_bin_width),
            frozen_bin_width,
            config.similarity,
        )

    first_reference, forward_window = normalize_reference(records[0].reference)
    second_reference, reverse_window = normalize_reference(records[1].reference)
    forward = compare_traces(
        first_reference,
        align_generated(records[1].reference, forward_window),
        forward_window,
        config.similarity,
    )
    reverse = compare_traces(
        second_reference,
        align_generated(records[0].reference, reverse_window),
        reverse_window,
        config.similarity,
    )

    settings_calls: list[SimilarityConfig] = []
    original_compare = vs_candidate_reporting.compare_traces

    def comparison_spy(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        settings_calls.append(settings)
        return original_compare(reference, generated, window, settings)

    monkeypatch.setattr(vs_candidate_reporting, "compare_traces", comparison_spy)
    result = vs_candidate_reporting.candidate_natural_variation(records)
    assert settings_calls[:2] == [records[0].config.similarity, records[1].config.similarity]
    assert settings_calls[0] is records[0].config.similarity
    assert settings_calls[1] is records[1].config.similarity
    first_pair = cast(dict[str, object], cast(list[object], result["pairs"])[0])
    forward_score = cast(dict[str, object], first_pair["forward"])
    reverse_score = cast(dict[str, object], first_pair["reverse"])
    symmetric = cast(dict[str, object], first_pair["symmetric_mean"])
    assert forward_score == vs_candidate_reporting._candidate_score(forward)  # pyright: ignore[reportPrivateUsage]
    assert reverse_score == vs_candidate_reporting._candidate_score(reverse)  # pyright: ignore[reportPrivateUsage]
    assert symmetric["aggregate"] == fmean((forward.aggregate_score, reverse.aggregate_score))
    for method in ("frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate"):
        assert cast(dict[str, float], symmetric["methods"])[method] == fmean(
            (forward.methods[method].score, reverse.methods[method].score)
        )
