"""Reproduction behavior."""

from __future__ import annotations

import hashlib
import json
import platform as platform
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.run as vs_prereq_run
import scripts.validation_study.results.codec as vs_results_codec
import trafficlab.common.compatibility as trafficlab_common_compatibility
from tests.support.validation_study.artifacts import write_retained_prerequisite_evidence
from tests.support.validation_study.builders import (
    frozen,
    study_result_value,
    valid_result_document,
    write_checked_configs,
)
from tests.support.validation_study.runners import (
    ScriptedPrerequisiteRunner,
    write_prerequisite_repository_inputs,
)
from tests.unit.validation.study.protocol._support import (
    contains_none,
    install_pre_user_agent_r6_predecessor,
)
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.errors import TrafficlabError


def test_result_codec_round_trips_nine_runs_reproduction_and_recomputed_summaries(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    value = study_result_value(document)

    rendered = vs_results_codec.render_study_results(value)
    parsed = vs_results_codec.parse_study_results(rendered, repository_root=repository_root)

    assert vs_results_codec.render_study_results(parsed) == rendered
    assert len(parsed.runs) == 9
    assert tuple((run.execution_order, run.run_id) for run in parsed.runs) == tuple(
        (order, run_id) for order, run_id, _workload, _repeat in vs_common.PRIMARY_ORDER
    )
    assert len(parsed.reproduction.document) == 27
    assert rendered.endswith(b"\n")
    assert b": " not in rendered
    assert not contains_none(json.loads(rendered))
    destination = repository_root / "examples" / "validation_study" / "results.json"
    destination.parent.mkdir(parents=True)
    vs_results_codec.publish_results(destination, value, repository_root=repository_root)
    assert destination.read_bytes() == rendered


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-capability-header",
        "tamper-capability.headers",
        "tamper-capability.stdout",
        "tamper-capability.stderr",
        "tamper-capability.cid",
        "tamper-capture.iid",
        "tamper-docker.stdout",
        "tamper-docker.stderr",
        "tamper-docker.xml",
        "tamper-internet.stdout",
        "tamper-internet.stderr",
        "tamper-internet.xml",
        "evidence-mode",
        "evidence-read-error",
        "non-ascii-cid",
        "invalid-junit",
        "junit-counts",
        "cid-record",
        "dockerfile-source",
        "capture-script-source",
    ],
)
def test_retained_prerequisite_evidence_reopens_hashes_and_crosschecks_every_authority(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, _contents = write_checked_configs(repository_root)
    prerequisite = write_retained_prerequisite_evidence(repository_root, prerequisite)
    evidence = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / prerequisite.study_id
        / "00-prerequisites"
    )
    if mutation == "missing-capability-header":
        (evidence / "capability.headers").unlink()
    elif mutation.startswith("tamper-"):
        name = mutation.removeprefix("tamper-")
        (evidence / name).write_bytes((evidence / name).read_bytes() + b"changed")
    elif mutation == "evidence-mode":
        (evidence / "internet.stderr").chmod(0o644)
    elif mutation == "evidence-read-error":
        (repository_root / "docker" / "capture" / "Dockerfile").unlink()
    elif mutation == "non-ascii-cid":
        (evidence / "capability.cid").write_bytes(b"\xff\n")
    elif mutation in {"invalid-junit", "junit-counts"}:
        junit = (
            b"not XML"
            if mutation == "invalid-junit"
            else (
                b'<testsuites tests="3" failures="0" errors="0" skipped="0">'
                b'<testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
            )
        )
        (evidence / "docker.xml").write_bytes(junit)
        commands = [cast(vs_common.JsonObject, vs_common.thaw_json(command)) for command in prerequisite.commands]
        commands[0]["junit_sha256"] = hashlib.sha256(junit).hexdigest()
        prerequisite = replace(prerequisite, commands=(frozen(commands[0]), frozen(commands[1])))
    elif mutation == "cid-record":
        capability = cast(
            vs_common.JsonObject,
            vs_common.thaw_json(prerequisite.capability),
        )
        capability["container_id"] = "SHORT"
        prerequisite = replace(prerequisite, capability=frozen(capability))
    elif mutation == "dockerfile-source":
        (repository_root / "docker" / "capture" / "Dockerfile").write_bytes(b"changed\n")
    elif mutation == "capture-script-source":
        (repository_root / "docker" / "capture" / "capture.sh").write_bytes(b"changed\n")

    with pytest.raises((TrafficlabError, ValueError)):
        vs_prereq_run.validate_prerequisite_evidence(
            repository_root,
            prerequisite,
        )


def test_retained_prerequisite_evidence_accepts_exact_local_files(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, _contents = write_checked_configs(repository_root)
    prerequisite = write_retained_prerequisite_evidence(repository_root, prerequisite)

    vs_prereq_run.validate_prerequisite_evidence(
        repository_root,
        prerequisite,
    )


def test_prerequisite_rotation_preserves_the_one_checked_pre_user_agent_r6_predecessor(
    tmp_path: Path,
) -> None:
    """Only the retained r6 raw evidence can bridge the short-lived no-User-Agent format."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    assert identify_bytes(predecessor_content).as_dict() == {
        "sha256": "a6cb727911ad19333c2faffa09e7f8e246750c8524b04c8cac13f3402672d275",
        "size": 5662,
    }
    with pytest.raises(ValueError, match="capability argv"):
        vs_prereq_codec.parse_prerequisite_results(predecessor_content, repository_root=repository_root)

    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")
    result = vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    published = canonical.read_bytes()
    parsed = vs_prereq_codec.parse_prerequisite_results(published, repository_root=repository_root)
    captured_live_argv = next(
        command
        for command, _timeout in runner.calls
        if command[:2] == ("docker", "run") and f"trafficlab-validation-study-capability-{runner.study_id}" in command
    )
    projected_argv = list(captured_live_argv)
    projected_argv[8] = str((runner.evidence / "capability.cid").relative_to(repository_root))
    projected_argv[12] = f"type=bind,src={runner.mount.relative_to(repository_root)},dst=/trafficlab-study"

    assert parsed == result
    assert cast(tuple[str, ...], parsed.capability["argv"]) == tuple(projected_argv)


def test_prerequisite_rotation_recreates_the_checked_r6_archive_when_the_legacy_root_lacks_one(
    tmp_path: Path,
) -> None:
    """The exact predecessor remains recoverable when its original raw archive was not yet retained."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    archive = canonical.parent / ".study-work" / "attempts" / source["study_id"] / "prerequisites.raw.json"
    archive.unlink()
    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")

    vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    assert archive.read_bytes() == predecessor_content


def test_prerequisite_rotation_rejects_an_arbitrary_pre_user_agent_schema_one_predecessor(tmp_path: Path) -> None:
    """A synthetic schema-1 projection cannot opt in to the r6-only rotation exception."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    prior_runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r6")
    vs_prereq_run.run_prerequisites(
        prior_runner.url,
        prior_runner.study_id,
        repository_root=repository_root,
        runner=prior_runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )
    study_root = repository_root / "examples" / "validation_study"
    canonical = study_root / "prerequisites.json"
    prior_archive = study_root / ".study-work" / "attempts" / prior_runner.study_id / "prerequisites.raw.json"
    prior_marker = prior_archive.with_name("prerequisites-success.json")
    legacy = cast(dict[str, object], json.loads(canonical.read_text(encoding="utf-8")))
    capability = cast(dict[str, object], legacy["capability"])
    argv = cast(list[str], capability["argv"])
    user_agent = argv.index("--user-agent")
    del argv[user_agent : user_agent + 2]
    legacy_content = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    canonical.write_bytes(legacy_content)
    prior_archive.write_bytes(legacy_content)
    marker = cast(dict[str, object], json.loads(prior_marker.read_text(encoding="utf-8")))
    marker["prerequisites_identity"] = trafficlab_common_compatibility.identify_bytes(legacy_content).as_dict()
    prior_marker.write_bytes(json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")

    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == legacy_content


def test_prerequisite_rotation_rejects_an_unreadable_retained_r6_evidence_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I/O failures while pinning the fixed retained evidence remain a canonical rotation rejection."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, _predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    evidence = canonical.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")
    original_iterdir = Path.iterdir

    def fail_preserved_evidence_iterdir(path: Path) -> Any:
        if path == evidence:
            raise OSError("simulated retained evidence read failure")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_preserved_evidence_iterdir)
    before = canonical.read_bytes()
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == before


@pytest.mark.parametrize("mutation", ("study_id", "url", "source", "tree", "raw", "marker", "evidence"))
def test_prerequisite_rotation_rejects_each_mutation_of_the_preserved_r6_predecessor(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Every identity component of the exact compatibility bridge remains independently pinned."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    attempt = canonical.parent / ".study-work" / "attempts" / source["study_id"]
    evidence = canonical.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")

    if mutation in {"study_id", "url", "source"}:
        document = cast(dict[str, object], json.loads(predecessor_content))
        document[mutation if mutation != "source" else "git_commit"] = (
            "study-r6"
            if mutation == "study_id"
            else "https://example.test/other.bin"
            if mutation == "url"
            else "0" * 40
        )
        canonical.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    elif mutation == "tree":
        runner.git_trees[f"{source['git_commit']}^{{tree}}"] = b"0" * 40 + b"\n"
    elif mutation == "raw":
        canonical.write_bytes(predecessor_content + b" ")
    elif mutation == "marker":
        (attempt / "prerequisites-success.json").write_bytes(b"{}\n")
    else:
        (evidence / "capability.headers").write_bytes(b"mutated retained evidence\n")

    before = canonical.read_bytes()
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == before
