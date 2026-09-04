# pyright: reportPrivateUsage=false
from __future__ import annotations

import copy
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

    def changed_preflight(path: Path, source_directory: Path) -> imported_module.PreparedExperiment:
        changed = dict(valid_config_data)
        capture = dict(cast(dict[str, object], changed["capture"]))
        capture["total_timeout_seconds"] = 61.0
        changed["capture"] = capture
        path.write_text(tomli_w.dumps(changed), encoding="utf-8")
        return real_preflight(path, source_directory)

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
            mismatch_call = 2 if failure == "during" else 3
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
        imported_module._canonical_pair_presence(tmp_path, deadline=1.0, clock=lambda: 0.0)


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


def test_fresh_import_revalidates_single_authority_after_lineage_append(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    real_append = imported_module.append_run_log

    def append_with_competing_authority(run_directory: Path, record: object) -> None:
        document = cast(dict[str, object], record)
        real_append(run_directory, document)
        if document.get("event") == "reference_imported":
            real_append(run_directory, {**document, "packet_count": 999, "reused": False})

    monkeypatch.setattr(imported_module, "append_run_log", append_with_competing_authority)

    with pytest.raises(TrafficlabError, match="lineage changed after publication append"):
        import_reference(source, prepared, clock=lambda: 0.0)

    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    authorities = [record for record in records if record.get("event") == "reference_imported"]
    assert len(authorities) == 2


def test_import_deadline_starts_before_prepared_snapshot_read_and_source_inspection(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    values = iter((0.0, 61.0))

    def forbidden_discovery(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("expired prepared-snapshot read reached source discovery")

    monkeypatch.setattr(imported_module, "discover_import_source", forbidden_discovery)
    with pytest.raises(TrafficlabError, match="total_timeout_seconds expired"):
        import_reference(source, prepared, clock=lambda: next(values))


@pytest.mark.parametrize("boundary", ["discovery", "canonical-presence"])
def test_import_inspection_boundaries_honor_existing_absolute_deadline(
    boundary: str, valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    with pytest.raises(TrafficlabError, match="total_timeout_seconds expired"):
        if boundary == "discovery":
            discover_import_source(source.directory, deadline=1.0, clock=lambda: 1.0)
        else:
            imported_module._canonical_pair_presence(
                prepared.run_directory,
                deadline=1.0,
                clock=lambda: 1.0,
            )


def test_authoritative_preflight_overlap_race_never_creates_source_artifact(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    experiment = tmp_path / "experiment.toml"
    experiment.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    original_load = imported_module.load_configuration_pair
    import trafficlab.preflight.stage as preflight_stage

    preflight_load = preflight_stage.load_configuration_pair
    calls = 0

    def race_on_authoritative_load(path: Path) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            changed = dict(valid_config_data)
            changed_run = dict(cast(dict[str, object], changed["run"]))
            changed_run["directory"] = str(source.directory / "nested-run")
            changed["run"] = changed_run
            path.write_text(tomli_w.dumps(changed), encoding="utf-8")
        return preflight_load(path)

    monkeypatch.setattr(preflight_stage, "load_configuration_pair", race_on_authoritative_load)
    monkeypatch.setattr(imported_module, "load_configuration_pair", original_load)
    before = _run_entries(source.directory)

    with pytest.raises(TrafficlabError, match="overlap"):
        run_imported_experiment(experiment, source.directory)

    assert _run_entries(source.directory) == before
    assert not (source.directory / "nested-run").exists()


@pytest.mark.parametrize("operation", ["read", "identify", "copy"])
@pytest.mark.parametrize("replacement_kind", ["symlink", "fifo"])
def test_import_reads_bind_nonfollowing_nonblocking_descriptor_before_validation(
    operation: str,
    replacement_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"original")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    destination = tmp_path / "snapshot"
    real_open = os.open
    raced = False

    def racing_open(path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777) -> int:
        nonlocal raced
        if Path(path) == source and not raced:
            raced = True
            source.unlink()
            if replacement_kind == "symlink":
                source.symlink_to(outside)
            else:
                os.mkfifo(source)
        return real_open(path, flags, mode)

    monkeypatch.setattr(imported_io.os, "open", racing_open)

    with pytest.raises(TrafficlabError, match="imported reference acquisition failed"):
        if operation == "read":
            imported_io._read_bytes_deadline(source, deadline=1.0, clock=lambda: 0.0, kind="source")
        elif operation == "identify":
            imported_io._identify_file_deadline(source, deadline=1.0, clock=lambda: 0.0, kind="source")
        else:
            imported_module._copy_snapshot(source, destination, deadline=1.0, clock=lambda: 0.0)

    assert raced is True
    assert not destination.exists()


def test_run_creation_rejects_parent_swapped_to_source_symlink_after_overlap_guard(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    safe_parent = tmp_path / "safe-parent"
    safe_parent.mkdir()
    run_directory = safe_parent / "run"
    configured = copy.deepcopy(valid_config_data)
    cast(dict[str, object], configured["run"])["directory"] = str(run_directory)
    experiment = tmp_path / "experiment.toml"
    experiment.write_text(tomli_w.dumps(configured), encoding="utf-8")
    real_require = imported_module._require_separate_directories
    calls = 0

    def swap_after_authoritative_guard(source_directory: Path, configured_run: Path) -> None:
        nonlocal calls
        real_require(source_directory, configured_run)
        calls += 1
        if calls == 3:
            safe_parent.rename(tmp_path / "displaced-safe-parent")
            safe_parent.symlink_to(source.directory, target_is_directory=True)

    monkeypatch.setattr(imported_module, "_require_separate_directories", swap_after_authoritative_guard)
    before = _run_entries(source.directory)

    with pytest.raises(TrafficlabError, match="run directory"):
        run_imported_experiment(experiment, source.directory)

    assert _run_entries(source.directory) == before
    assert not (source.directory / "run").exists()


@pytest.mark.parametrize("entrypoint", ["direct", "coordinator"])
def test_import_temp_root_creation_failure_is_actionable_and_coordinator_owned(
    entrypoint: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)

    def failed_mkdtemp(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise PermissionError("mkdtemp permission sentinel")

    monkeypatch.setattr(imported_module.tempfile, "mkdtemp", failed_mkdtemp)

    with pytest.raises(TrafficlabError, match="could not create owned temporary directory"):
        if entrypoint == "direct":
            import_reference(source, prepared, clock=lambda: 0.0)
        else:
            shutil.rmtree(prepared.run_directory)
            run_imported_experiment(experiment, source.directory)

    if entrypoint == "coordinator":
        records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
        assert records[-1]["event"] == "run_failed"
        assert records[-1]["failed_stage"] == "capture"


@pytest.mark.parametrize("persistent", [False, True], ids=["retry-succeeds", "retry-fails"])
def test_import_resolves_publisher_cleanup_warnings_before_success_lineage(
    persistent: bool,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    real_unlink = os.unlink
    attempts: dict[Path, int] = {}

    def fail_creator_temp_once(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *args: object, **kwargs: object
    ) -> None:
        candidate = Path(path)
        if candidate.name.startswith(".capture-pair."):
            attempts[candidate] = attempts.get(candidate, 0) + 1
            if persistent or attempts[candidate] == 1:
                raise OSError("publisher cleanup sentinel")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(imported_io.os, "unlink", fail_creator_temp_once)

    if persistent:
        with pytest.raises(TrafficlabError, match="publisher.*cleanup"):
            import_reference(source, prepared, clock=lambda: 0.0)
        records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
        assert not [record for record in records if record.get("event") == "reference_imported"]
        assert tuple(prepared.run_directory.glob(".capture-pair.*"))
    else:
        result = import_reference(source, prepared, clock=lambda: 0.0)
        assert result.reused is False
        assert not tuple(prepared.run_directory.glob(".capture-pair.*"))
        assert all(count == 2 for count in attempts.values())


@pytest.mark.parametrize("boundary", ["source", "output", "authority", "deadline"])
def test_reuse_revalidates_every_authority_after_lineage_append(
    boundary: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    import_reference(source, prepared, clock=lambda: 0.0)
    real_append = imported_module.append_run_log
    appended = False

    def append_then_race(run_directory: Path, record: object) -> None:
        nonlocal appended
        document = cast(dict[str, object], record)
        real_append(run_directory, document)
        if document.get("event") != "reference_imported" or document.get("reused") is not True:
            return
        appended = True
        if boundary == "source":
            source.capture_path.write_bytes(source.capture_path.read_bytes() + b"changed")
        elif boundary == "output":
            canonical = run_directory / "reference.pcapng"
            replacement = run_directory / "replacement.pcapng"
            replacement.write_bytes(canonical.read_bytes())
            os.replace(replacement, canonical)
        elif boundary == "authority":
            real_append(run_directory, {**document, "reused": False, "packet_count": 999})

    monkeypatch.setattr(imported_module, "append_run_log", append_then_race)

    def clock() -> float:
        return 61.0 if boundary == "deadline" and appended else 0.0

    with pytest.raises(TrafficlabError):
        import_reference(source, prepared, clock=clock)


def test_fresh_import_rechecks_source_after_lineage_append(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    real_append = imported_module.append_run_log

    def append_then_mutate_source(run_directory: Path, record: object) -> None:
        document = cast(dict[str, object], record)
        real_append(run_directory, document)
        if document.get("event") == "reference_imported":
            source.metadata_path.write_bytes(source.metadata_path.read_bytes() + b" ")

    monkeypatch.setattr(imported_module, "append_run_log", append_then_mutate_source)

    with pytest.raises(TrafficlabError, match="source changed"):
        import_reference(source, prepared, clock=lambda: 0.0)


def test_reuse_rejects_a_noop_lineage_append(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse success requires exactly one newly durable matching lineage record."""
    source = _source_copy(tmp_path)
    _experiment, prepared_object = _prepared(valid_config_data, tmp_path)
    prepared = cast(imported_module.PreparedExperiment, prepared_object)
    import_reference(source, prepared, clock=lambda: 0.0)

    def noop_append(_run_directory: Path, _record: object) -> None:
        return None

    monkeypatch.setattr(imported_module, "append_run_log", noop_append)

    with pytest.raises(TrafficlabError, match="exactly one new reuse record"):
        import_reference(source, prepared, clock=lambda: 0.0)
