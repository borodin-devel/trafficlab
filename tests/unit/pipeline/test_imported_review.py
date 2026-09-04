# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest
import tomli_w

import trafficlab.pipeline.imported as imported_module
import trafficlab.pipeline.imported_io as imported_io
from trafficlab.common.errors import TrafficlabError
from trafficlab.pipeline.imported import discover_import_source, import_reference, run_imported_experiment
from trafficlab.preflight.stage import run_preflight

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "data" / "import_run"


def _source_copy(tmp_path: Path) -> imported_module.ImportSource:
    destination = tmp_path / "source"
    shutil.copytree(FIXTURES / "classic-pcap-source", destination)
    return discover_import_source(destination)


def _prepared(valid_config_data: dict[str, object], tmp_path: Path) -> tuple[Path, object]:
    experiment = tmp_path / "experiment.toml"
    experiment.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    return experiment, run_preflight(experiment, config_only=True)


def _run_entries(run_directory: Path) -> dict[str, tuple[str, bytes | None]]:
    entries: dict[str, tuple[str, bytes | None]] = {}
    for path in run_directory.iterdir():
        if path.is_symlink():
            entries[path.name] = ("symlink", os.readlink(path).encode())
        elif path.is_file():
            entries[path.name] = ("file", path.read_bytes())
        elif path.is_dir():
            entries[path.name] = ("directory", None)
        else:
            entries[path.name] = ("special", None)
    return entries


def test_fresh_interpreter_import_has_repository_provenance_without_docker_or_subprocess() -> None:
    expected = Path(imported_module.__file__).resolve()
    script = textwrap.dedent(
        """
        import builtins
        import pathlib
        import sys

        original = builtins.__import__
        def guarded(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "subprocess" or name.startswith("trafficlab.capture.docker"):
                raise AssertionError(f"forbidden eager import: {name}")
            return original(name, globals, locals, fromlist, level)
        builtins.__import__ = guarded

        import trafficlab.pipeline.imported as imported
        assert "subprocess" not in sys.modules
        assert not any(name.startswith("trafficlab.capture.docker") for name in sys.modules)
        print(pathlib.Path(imported.__file__).resolve())
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[3],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(expected)


@pytest.mark.parametrize("kind", ["dangling-symlink", "fifo", "socket"])
def test_nonregular_canonical_entry_is_preserved_without_hashing_or_recovery(
    kind: str, valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    canonical = prepared.run_directory / "capture.json"
    held_socket: socket.socket | None = None
    if kind == "dangling-symlink":
        canonical.symlink_to(prepared.run_directory / "missing")
    elif kind == "fifo":
        os.mkfifo(canonical)
    else:
        held_socket = socket.socket(socket.AF_UNIX)
        held_socket.bind(str(canonical))
    before = _run_entries(prepared.run_directory)
    try:
        with pytest.raises(TrafficlabError, match="exact imported-reference reuse"):
            import_reference(source, prepared, clock=lambda: 0.0)
        assert _run_entries(prepared.run_directory) == before
    finally:
        if held_socket is not None:
            held_socket.close()


def test_absent_pair_with_retained_import_authority_cannot_publish_second_authority(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    log = prepared.run_directory / "run.log"
    stale = {"event": "reference_imported", "reused": False, "stage": "capture"}
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(stale) + "\n")
    before = _run_entries(prepared.run_directory)

    with pytest.raises(TrafficlabError, match="retained import lineage"):
        import_reference(source, prepared, clock=lambda: 0.0)

    assert _run_entries(prepared.run_directory) == before


def test_post_preflight_configuration_change_is_owned_by_capture_failure(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    experiment = tmp_path / "experiment.toml"
    experiment.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    real_preflight = imported_module._config_only_preflight

    def changed_preflight(path: Path) -> imported_module.PreparedExperiment:
        changed = dict(valid_config_data)
        capture = dict(cast(dict[str, object], changed["capture"]))
        capture["total_timeout_seconds"] = 61.0
        changed["capture"] = capture
        path.write_text(tomli_w.dumps(changed), encoding="utf-8")
        return real_preflight(path)

    monkeypatch.setattr(imported_module, "_config_only_preflight", changed_preflight)
    with pytest.raises(TrafficlabError, match="changed during imported preflight"):
        run_imported_experiment(experiment, source.directory)

    run_directory = Path(cast(str, cast(dict[str, object], valid_config_data["run"])["directory"]))
    records = [json.loads(line) for line in (run_directory / "run.log").read_text().splitlines()]
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "capture"
    assert not (run_directory / "capture.json").exists()


def test_discovery_rejects_capture_replaced_between_lstat_and_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    capture = source.capture_path
    outside = tmp_path / "outside.pcap"
    outside.write_bytes(capture.read_bytes())
    real_resolve = Path.resolve
    replaced = False

    def replace_then_resolve(path: Path, strict: bool = False) -> Path:
        nonlocal replaced
        if path == capture and not replaced:
            replaced = True
            path.unlink()
            path.symlink_to(outside)
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", replace_then_resolve)
    with pytest.raises(TrafficlabError, match="changed during discovery"):
        discover_import_source(source.directory)


@pytest.mark.parametrize("boundary", ["content-identity", "metadata-bytes", "lineage-bytes"])
def test_import_file_boundaries_check_deadline_inside_chunk_loop(
    boundary: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"abc")
    monkeypatch.setattr(imported_io, "_READ_CHUNK_SIZE", 1)
    values = iter((0.0, 0.0, 2.0))

    def clock() -> float:
        return next(values)

    with pytest.raises(TrafficlabError, match="total_timeout_seconds expired"):
        if boundary == "content-identity":
            imported_module._identify_file_deadline(path, deadline=1.0, clock=clock, kind="source")
        elif boundary == "metadata-bytes":
            imported_module._read_bytes_deadline(path, deadline=1.0, clock=clock, kind="metadata")
        else:
            imported_module._read_bytes_deadline(path, deadline=1.0, clock=clock, kind="lineage")


@pytest.mark.parametrize("canonical_name", ["capture.json", "reference.pcapng"])
@pytest.mark.parametrize("boundary", ["after-publication", "during-lineage", "during-append", "after-append"])
def test_fresh_import_binds_publication_identity_across_lineage_append(
    canonical_name: str,
    boundary: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    canonical = prepared.run_directory / canonical_name

    def replace_canonical() -> None:
        canonical.write_bytes(f"replacement-{canonical_name}-{boundary}".encode())

    if boundary == "after-publication":
        real_publish = imported_module.publish_capture_pair

        def publish_then_replace(*args: object, **kwargs: object) -> object:
            result = cast(Any, real_publish)(*args, **kwargs)
            replace_canonical()
            return result

        monkeypatch.setattr(imported_module, "publish_capture_pair", publish_then_replace)
    elif boundary == "during-lineage":
        real_lineage = imported_module._lineage_record

        def lineage_while_replaced(*args: object, **kwargs: object) -> dict[str, object]:
            replace_canonical()
            return cast(Any, real_lineage)(*args, **kwargs)

        monkeypatch.setattr(imported_module, "_lineage_record", lineage_while_replaced)
    else:
        real_append = imported_module.append_run_log

        def append_with_replacement(*args: object, **kwargs: object) -> None:
            if boundary == "during-append":
                replace_canonical()
            cast(Any, real_append)(*args, **kwargs)
            if boundary == "after-append":
                replace_canonical()

        monkeypatch.setattr(imported_module, "append_run_log", append_with_replacement)

    with pytest.raises(TrafficlabError, match="changed after publication"):
        import_reference(source, prepared, clock=lambda: 0.0)

    assert canonical.read_bytes().startswith(b"replacement-")


def test_discovery_reports_change_that_makes_accepted_child_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    real_resolve = Path.resolve

    def failed_child_resolve(path: Path, strict: bool = False) -> Path:
        if path.name == "source.pcap":
            raise OSError("child resolve sentinel")
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", failed_child_resolve)
    with pytest.raises(TrafficlabError, match="changed during discovery"):
        discover_import_source(source.directory)


@pytest.mark.parametrize("operation", ["read", "identify"])
@pytest.mark.parametrize("failure", ["nonregular", "oserror", "during", "after"])
def test_deadline_file_helpers_reject_unstable_or_unreadable_inputs(
    operation: str, failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"abc")
    target = path
    if failure == "nonregular":
        target = tmp_path
    elif failure == "oserror":
        target = tmp_path / "missing"
    elif failure in {"during", "after"}:
        real_state = imported_io._path_state
        calls = 0

        def unstable_state(status: os.stat_result) -> tuple[int, int, int, int, int, int]:
            nonlocal calls
            calls += 1
            state = real_state(status)
            mismatch_call = 2 if failure == "during" else 6
            return (*state[:-1], state[-1] + 1) if calls == mismatch_call else state

        monkeypatch.setattr(imported_io, "_path_state", unstable_state)

    with pytest.raises(TrafficlabError):
        if operation == "read":
            imported_module._read_bytes_deadline(target, deadline=1.0, clock=lambda: 0.0, kind="input")
        else:
            imported_module._identify_file_deadline(target, deadline=1.0, clock=lambda: 0.0, kind="input")


def test_copy_snapshot_propagates_internal_deadline_error(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"abc")
    with pytest.raises(TrafficlabError, match="total_timeout_seconds expired"):
        imported_module._copy_snapshot(source, tmp_path / "snapshot", deadline=1.0, clock=lambda: 2.0)


def test_canonical_presence_translates_lstat_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_stat = Path.stat

    def failed_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path.name == "capture.json":
            raise OSError("canonical lstat sentinel")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", failed_stat)
    with pytest.raises(TrafficlabError, match="canonical lstat sentinel"):
        imported_module._canonical_pair_presence(tmp_path)


def test_complete_nonregular_pair_is_rejected_before_content_hashing(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    (prepared.run_directory / "capture.json").mkdir()
    (prepared.run_directory / "reference.pcapng").mkdir()

    with pytest.raises(TrafficlabError, match="not a regular file"):
        import_reference(source, prepared, clock=lambda: 0.0)


def test_canonical_identity_reports_member_removed_after_lstat(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    import_reference(source, prepared, clock=lambda: 0.0)
    real_identity = imported_module.file_identity

    def missing_identity(path: Path) -> object:
        if path.name == "capture.json":
            return None
        return real_identity(path)

    monkeypatch.setattr(imported_module, "file_identity", missing_identity)
    with pytest.raises(TrafficlabError, match="capture pair is incomplete"):
        import_reference(source, prepared, clock=lambda: 0.0)


def test_owned_publication_requires_publisher_identity(tmp_path: Path) -> None:
    from trafficlab.artifacts.capture import CapturePublication

    class MissingIdentity:
        owned_identity = None

    publication = cast(CapturePublication, MissingIdentity())
    with pytest.raises(TrafficlabError, match="no creator-owned identity"):
        imported_module._require_owned_publication(
            tmp_path,
            publication,
            cast(Any, ()),
            deadline=1.0,
            clock=lambda: 0.0,
        )


@pytest.mark.parametrize("lineage_call", [2, 3])
def test_fresh_import_rejects_lineage_appearing_during_publication(
    lineage_call: int,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    real_read = imported_module._read_import_lineage
    calls = 0

    def appearing_lineage(run_directory: Path, *, deadline: float, clock: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        records = real_read(run_directory, deadline=deadline, clock=cast(Any, clock))
        return [{"event": "reference_imported"}] if calls == lineage_call else records

    monkeypatch.setattr(imported_module, "_read_import_lineage", appearing_lineage)
    with pytest.raises(TrafficlabError, match="retained import lineage"):
        import_reference(source, prepared, clock=lambda: 0.0)


def test_lazy_pipeline_wrappers_delegate_to_existing_stage_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    import trafficlab.comparison.stage as comparison_stage
    import trafficlab.fitting.stage as fitting_stage
    import trafficlab.generation.stage as generation_stage

    sentinel = object()

    def return_sentinel(_path: Path) -> object:
        return sentinel

    monkeypatch.setattr(fitting_stage, "fit_experiment", return_sentinel)
    monkeypatch.setattr(generation_stage, "generate_experiment", return_sentinel)
    monkeypatch.setattr(comparison_stage, "compare_experiment", return_sentinel)

    assert imported_module._fit_experiment(Path("experiment")) is sentinel
    assert imported_module._generate_experiment(Path("experiment")) is sentinel
    assert imported_module._compare_experiment(Path("experiment")) is sentinel


def test_canonical_identity_revalidates_first_member_after_hashing_second(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    import_reference(source, prepared, clock=lambda: 0.0)
    metadata = prepared.run_directory / "capture.json"
    real_identify = imported_module._identify_file_deadline

    def replace_metadata_before_reference_hash(path: Path, **kwargs: object) -> object:
        if path.name == "reference.pcapng":
            replacement = prepared.run_directory / "replacement.json"
            replacement.write_bytes(metadata.read_bytes())
            os.replace(replacement, metadata)
        return cast(Any, real_identify)(path, **kwargs)

    monkeypatch.setattr(imported_module, "_identify_file_deadline", replace_metadata_before_reference_hash)
    with pytest.raises(TrafficlabError, match="changed during content identity"):
        imported_module._canonical_identities(prepared.run_directory, deadline=1.0, clock=lambda: 0.0)


def test_source_identity_revalidates_first_member_after_hashing_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    capture = source.capture_path
    real_identify = imported_module._identify_file_deadline

    def replace_capture_before_metadata_hash(path: Path, **kwargs: object) -> object:
        if path == source.metadata_path:
            replacement = source.directory / "replacement.pcap"
            replacement.write_bytes(capture.read_bytes())
            os.replace(replacement, capture)
        return cast(Any, real_identify)(path, **kwargs)

    monkeypatch.setattr(imported_module, "_identify_file_deadline", replace_capture_before_metadata_hash)
    with pytest.raises(TrafficlabError, match="source changed during content identity"):
        imported_module._source_identities(source, deadline=1.0, clock=lambda: 0.0)


def test_fresh_publication_race_preserves_new_nonregular_canonical_entry(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    canonical = prepared.run_directory / "capture.json"
    real_publish = imported_module.publish_capture_pair

    def race_then_publish(*args: object, **kwargs: object) -> object:
        canonical.symlink_to(prepared.run_directory / "missing")
        return cast(Any, real_publish)(*args, **kwargs)

    monkeypatch.setattr(imported_module, "publish_capture_pair", race_then_publish)
    with pytest.raises(TrafficlabError, match="already exists"):
        import_reference(source, prepared, clock=lambda: 0.0)

    assert canonical.is_symlink()
    assert os.readlink(canonical) == str(prepared.run_directory / "missing")
