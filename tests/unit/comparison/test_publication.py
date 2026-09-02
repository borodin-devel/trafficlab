import copy
import json
import os
from pathlib import Path

import pytest

import trafficlab.artifacts.io as artifact_io
import trafficlab.comparison.codec as comparison_codec
import trafficlab.comparison.publication as comparison_publication
import trafficlab.comparison.schema as comparison_schema
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from tests.support.comparison import settings as _settings
from tests.support.comparison import trace as _trace
from tests.support.comparison import valid_result, valid_result_document
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent
from trafficlab.comparison.codec import render_comparison_result
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult


def test_publication_reports_creation_instead_of_reuse_for_an_absent_destination(tmp_path: Path) -> None:
    """Returning no ownership state would make retry logging claim a newly created result was reused."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()

    created = comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert created is True
    assert destination.read_bytes() == render_comparison_result(expected)
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_reuses_strict_canonical_bytes_and_reads_existing_destination_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry must recognize only the exact canonical result without reopening mutable destination bytes."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    expected_content = render_comparison_result(expected)
    destination.write_bytes(expected_content)
    real_read_bytes = Path.read_bytes
    destination_reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal destination_reads
        if path == destination:
            destination_reads += 1
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    created = comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert created is False
    assert destination_reads == 1
    assert real_read_bytes(destination) == expected_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_rejects_renderer_bytes_for_a_different_valid_result_before_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced renderer must not redefine which scientific result qualifies for exact reuse."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    assert expected.input_identities is not None
    different_inputs = expected.input_identities.as_content_identities()
    different_inputs["capture_json"] = ContentIdentity(size=1, sha256="d" * 64)
    rendered_result = expected.with_input_identities(different_inputs)
    rendered_content = render_comparison_result(rendered_result)
    destination.write_bytes(rendered_content)

    def render_different(_result: ComparisonResult) -> bytes:
        return rendered_content

    monkeypatch.setattr(comparison_publication, "render_comparison_result", render_different)

    with pytest.raises(TrafficlabError, match="rendered similarity artifact.*canonical evaluated result"):
        comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == rendered_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


@pytest.mark.parametrize(
    "existing_content",
    [
        b"not-json\n",
        (json.dumps(valid_result_document(), sort_keys=True, separators=(",", ":")) + "\n").encode(),
    ],
    ids=["malformed", "noncanonical"],
)
def test_publication_preserves_malformed_or_noncanonical_existing_bytes(
    tmp_path: Path, existing_content: bytes
) -> None:
    """Malformed bytes or a noncanonical encoding must never be blessed as the evaluated artifact."""
    destination = tmp_path / "similarity.json"
    destination.write_bytes(existing_content)

    with pytest.raises(TrafficlabError):
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == existing_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_preserves_a_valid_existing_result_with_different_lineage(tmp_path: Path) -> None:
    """A valid result for different exact inputs must remain a collision rather than a successful retry."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    assert expected.input_identities is not None
    different_inputs = expected.input_identities.as_content_identities()
    different_inputs["capture_json"] = ContentIdentity(size=1, sha256="f" * 64)
    different = expected.with_input_identities(different_inputs)
    different_content = render_comparison_result(different)
    destination.write_bytes(different_content)

    with pytest.raises(TrafficlabError, match="already exists"):
        comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == different_content


def test_publication_preserves_a_valid_existing_result_with_a_different_score(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Matching lineage alone must not permit reuse of a scientifically different comparison."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    assert expected.input_identities is not None
    changed_trace = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 180),
        TraceEvent(3.0, Direction.OUTBOUND, 100),
    )
    different = compare_traces(_trace(), changed_trace, 3.0, _settings(valid_config_data)).with_input_identities(
        expected.input_identities.as_content_identities()
    )
    different_content = render_comparison_result(different)
    assert different.aggregate_score != expected.aggregate_score
    destination.write_bytes(different_content)

    with pytest.raises(TrafficlabError, match="already exists"):
        comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == different_content


@pytest.mark.parametrize("collision", [False, True], ids=["existing", "link-race-winner"])
def test_publication_rejects_a_canonical_entry_replaced_immediately_after_its_validation_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision: bool,
) -> None:
    """Reuse must remain bound to the unchanged directory entry whose exact bytes were validated."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    expected_content = render_comparison_result(expected)
    assert expected.input_identities is not None
    replacement_inputs = expected.input_identities.as_content_identities()
    replacement_inputs["capture_json"] = ContentIdentity(size=1, sha256="a" * 64)
    replacement_content = render_comparison_result(expected.with_input_identities(replacement_inputs))
    if not collision:
        destination.write_bytes(expected_content)

    real_read_bytes = Path.read_bytes
    real_link = os.link
    replaced = False

    def replace_after_read(path: Path) -> bytes:
        nonlocal replaced
        content = real_read_bytes(path)
        if path == destination and not replaced:
            replacement_path = tmp_path / "replacement-similarity.json"
            replacement_path.write_bytes(replacement_content)
            os.replace(replacement_path, destination)
            replaced = True
        return content

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(expected_content)
        real_link(source, target)

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    if collision:
        monkeypatch.setattr(comparison_publication.os, "link", collide)

    with pytest.raises(TrafficlabError, match="changed during.*validation"):
        comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert replaced is True
    assert real_read_bytes(destination) == replacement_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


@pytest.mark.parametrize("winner_kind", ["identical", "different"])
def test_publication_link_race_reuses_only_an_identical_winner_and_preserves_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winner_kind: str
) -> None:
    """Losing an exclusive-link race must validate the winner exactly and never replace it."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    expected_content = render_comparison_result(expected)
    if winner_kind == "identical":
        winner = expected_content
    else:
        assert expected.input_identities is not None
        different_inputs = expected.input_identities.as_content_identities()
        different_inputs["capture_json"] = ContentIdentity(size=1, sha256="e" * 64)
        winner = render_comparison_result(expected.with_input_identities(different_inputs))
    real_link = os.link

    def collide(source: str | Path, destination_arg: str | Path) -> None:
        Path(destination_arg).write_bytes(winner)
        real_link(source, destination_arg)

    monkeypatch.setattr(comparison_publication.os, "link", collide)

    if winner_kind == "identical":
        created = comparison_publication.publish_comparison_result(  # pyright: ignore[reportPrivateUsage]
            destination, expected
        )
        assert created is False
    else:
        with pytest.raises(TrafficlabError, match="already exists"):
            comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == winner
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_reports_a_link_race_winner_that_disappears_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vanished race winner must remain an expected publication error and release the owned temp."""
    destination = tmp_path / "similarity.json"

    def lose_to_vanished_winner(_source: str | Path, _destination: str | Path) -> None:
        raise FileExistsError("injected transient collision")

    monkeypatch.setattr(comparison_publication.os, "link", lose_to_vanished_winner)

    with pytest.raises(TrafficlabError, match="could not publish similarity artifact"):
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_fsync_failure_is_translated_and_cleans_the_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed durability boundary must not leak the package API or leave a partial temporary artifact."""
    destination = tmp_path / "similarity.json"

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("injected similarity fsync failure")

    monkeypatch.setattr(comparison_publication.os, "fsync", fail_fsync)

    with pytest.raises(TrafficlabError, match="injected similarity fsync failure") as caught:
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert caught.value.corrective_action == "verify the run directory is writable and has available space"
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
    ) == (
        "publication_failed",
        "compare",
        "similarity.json durability check failed",
        "similarity.json",
        "not_published",
        "correct storage and rerun compare",
        "primary",
    )
    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_fsyncs_directory_after_exclusive_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "similarity.json"
    operations: list[str] = []
    real_link = os.link

    def observe_link(source: str | Path, target: str | Path) -> None:
        operations.append("link")
        real_link(source, target)

    def observe_directory_fsync(path: Path, *, stage: str, affected_evidence: str) -> None:
        assert path == destination
        assert stage == "compare"
        assert affected_evidence == "similarity.json"
        operations.append("fsync-directory")

    monkeypatch.setattr(comparison_publication.os, "link", observe_link)
    monkeypatch.setattr(comparison_publication, "fsync_published_artifact", observe_directory_fsync, raising=False)

    comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert operations == ["link", "fsync-directory"]


def test_publication_directory_durability_failure_preserves_similarity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    content = render_comparison_result(expected)

    def fail_directory_fsync(_path: Path) -> None:
        raise TrafficlabError("injected similarity directory fsync failure", corrective_action="repair storage")

    monkeypatch.setattr(artifact_io, "fsync_containing_directory", fail_directory_fsync)

    with pytest.raises(TrafficlabError, match="similarity directory fsync failure") as caught:
        comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "publication_failed",
        "compare",
        "similarity.json",
        "preserved",
    )
    assert destination.read_bytes() == content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_rejects_owned_temp_bytes_changed_after_strict_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validated temp changed before linking must fail its exact canonical byte check."""
    destination = tmp_path / "similarity.json"
    real_load = comparison_codec.load_comparison_result

    def mutate_after_load(path: Path) -> ComparisonResult:
        persisted = real_load(path)
        path.write_bytes(path.read_bytes().removesuffix(b"\n") + b" \n")
        return persisted

    monkeypatch.setattr(comparison_publication, "load_comparison_result", mutate_after_load)

    with pytest.raises(TrafficlabError, match="temporary similarity artifact did not round-trip"):
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_propagates_an_unexpected_validation_exception_after_owned_temp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Programming defects must retain their original type while still respecting temporary-file ownership."""
    destination = tmp_path / "similarity.json"
    primary = RuntimeError("injected unexpected parser defect")

    def fail_parse(_content: bytes) -> ComparisonResult:
        raise primary

    monkeypatch.setattr(comparison_codec, "parse_comparison_result", fail_parse)

    with pytest.raises(RuntimeError) as error:
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value is primary
    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_preserves_an_unexpected_exception_when_owned_temp_cleanup_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup failure must annotate, not translate or conceal, an unexpected programming defect."""
    destination = tmp_path / "similarity.json"
    primary = RuntimeError("injected unexpected parser defect")
    real_unlink = os.unlink

    def fail_parse(_content: bytes) -> ComparisonResult:
        raise primary

    def fail_owned_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(path).name.startswith(".similarity.json."):
            raise OSError("injected unexpected cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(comparison_codec, "parse_comparison_result", fail_parse)
    monkeypatch.setattr(comparison_publication.os, "unlink", fail_owned_unlink)

    with pytest.raises(RuntimeError) as error:
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value is primary
    assert error.value.__notes__ == ["owned temporary file cleanup also failed: injected unexpected cleanup failure"]
    assert not destination.exists()
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_publication_preserves_unexpected_primary_identity_when_cleanup_is_also_unexpected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected cleanup defect must annotate rather than mask an unexpected publication defect."""
    destination = tmp_path / "similarity.json"
    primary = RuntimeError("injected unexpected primary")
    cleanup = RuntimeError("injected unexpected cleanup")

    def fail_parse(_content: bytes) -> ComparisonResult:
        raise primary

    def fail_cleanup(_path: str | Path, *args: object, **kwargs: object) -> None:
        raise cleanup

    monkeypatch.setattr(comparison_codec, "parse_comparison_result", fail_parse)
    monkeypatch.setattr(comparison_publication.os, "unlink", fail_cleanup)

    with pytest.raises(RuntimeError) as error:
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value is primary
    assert error.value.__notes__ == [f"owned temporary file cleanup also failed: {cleanup}"]
    assert not destination.exists()
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_publication_translates_expected_primary_when_cleanup_is_unexpected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected cleanup defect must not mask the actionable expected publication failure."""
    destination = tmp_path / "similarity.json"
    primary = OSError("injected expected primary")
    cleanup = RuntimeError("injected unexpected cleanup")

    def fail_fsync(_file_descriptor: int) -> None:
        raise primary

    def fail_cleanup(_path: str | Path, *args: object, **kwargs: object) -> None:
        raise cleanup

    monkeypatch.setattr(comparison_publication.os, "fsync", fail_fsync)
    monkeypatch.setattr(comparison_publication.os, "unlink", fail_cleanup)

    with pytest.raises(
        TrafficlabError,
        match="injected expected primary.*cleanup incomplete.*injected unexpected cleanup",
    ) as error:
        comparison_publication.publish_comparison_result(destination, valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value.__cause__ is primary
    assert not destination.exists()
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_publication_propagates_unexpected_cleanup_by_identity_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no primary failure, an unexpected cleanup defect must propagate after preserving publication."""
    destination = tmp_path / "similarity.json"
    expected = valid_result()
    cleanup = RuntimeError("injected unexpected cleanup")

    def fail_cleanup(_path: str | Path, *args: object, **kwargs: object) -> None:
        raise cleanup

    monkeypatch.setattr(comparison_publication.os, "unlink", fail_cleanup)

    with pytest.raises(RuntimeError) as error:
        comparison_publication.publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert error.value is cleanup
    assert destination.read_bytes() == render_comparison_result(expected)
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_malformed_nested_method_cannot_render_or_publish(tmp_path: Path) -> None:
    """A model_copy-mutated diagnostic must fail before bytes or a destination become visible."""
    source = PIPELINE_FIXTURE_ROOT / "similarity.json"
    valid = comparison_codec.parse_comparison_result(source.read_bytes())
    original = valid.methods["frame_size_ks"]
    corrupted_diagnostics = original.diagnostics.model_copy(update={"reference_count": 0})
    corrupted_method = original.model_copy(update={"diagnostics": corrupted_diagnostics})
    methods = dict(valid.methods)
    methods["frame_size_ks"] = corrupted_method
    corrupted = valid.model_copy(update={"methods": methods})

    with pytest.raises(ValueError, match="reference_count"):
        comparison_codec.render_comparison_result(corrupted)

    destination = tmp_path / "similarity.json"
    with pytest.raises(TrafficlabError, match="reference_count"):
        comparison_publication.publish_comparison_result(destination, corrupted)  # pyright: ignore[reportPrivateUsage]
    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_parser_and_publication_reject_null_lineage_and_cross_key_diagnostics(tmp_path: Path) -> None:
    """Only the exact publication wire shape may parse or reach an immutable destination."""
    document = json.loads((PIPELINE_FIXTURE_ROOT / "similarity.json").read_bytes())
    missing_lineage = copy.deepcopy(document)
    missing_lineage["input_identities"] = None
    with pytest.raises(ValueError, match="input_identities"):
        comparison_codec.parse_comparison_result(json.dumps(missing_lineage).encode())

    wrong_method = copy.deepcopy(document)
    wrong_method["methods"]["frame_size_ks"] = copy.deepcopy(wrong_method["methods"]["iat_ks"])
    with pytest.raises(ValueError, match="frame_size_ks.*wrong method discriminator"):
        comparison_codec.parse_comparison_result(json.dumps(wrong_method).encode())

    wrong_ecdf_method = copy.deepcopy(document)
    wrong_ecdf_method["methods"]["cramer_von_mises"] = copy.deepcopy(
        wrong_ecdf_method["methods"]["anderson_darling"]
    )
    with pytest.raises(ValueError, match="cramer_von_mises.*wrong method discriminator"):
        comparison_codec.parse_comparison_result(json.dumps(wrong_ecdf_method).encode())

    with pytest.raises(ValueError, match="comparison result"):
        comparison_codec.parse_comparison_result(b"[]")

    non_object_method = copy.deepcopy(document)
    non_object_method["methods"]["frame_size_ks"] = 1
    with pytest.raises(ValueError, match="frame_size_ks"):
        comparison_codec.parse_comparison_result(json.dumps(non_object_method).encode())

    invalid_window = copy.deepcopy(document)
    invalid_window["methods"]["frame_size_ks"]["diagnostics"]["observation_window_seconds"] = 10
    with pytest.raises(ValueError, match="finite positive float"):
        comparison_codec.parse_comparison_result(json.dumps(invalid_window).encode())

    operational = comparison_schema.ComparisonResult.model_validate(missing_lineage)
    destination = tmp_path / "similarity.json"
    with pytest.raises(TrafficlabError, match="identities are required"):
        comparison_publication.publish_comparison_result(destination, operational)  # pyright: ignore[reportPrivateUsage]
    assert not destination.exists()
